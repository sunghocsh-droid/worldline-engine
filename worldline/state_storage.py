"""Lossless SQLite physical storage; logical World/save schemas stay unchanged.

Large history objects are immutable content-addressed blobs. Current mappings
hold references, and public reads expand them into the original detached JSON.
This module never changes simulation data, scheduler, validation, or RNG.
"""
import hashlib
import json
import zlib
from .state_runtime import version

VERSION = 1
MARKER = "__worldline_storage__"
REFERENCE = "__worldline_archive_ref__"
COMPONENTS = '__worldline_components__'


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _same_json(left, right):
    """Canonical JSON equality, including boolean/numeric types and signed zero.

    Python considers 1 == True == 1.0 and -0.0 == 0.0. Reusing references on
    that basis would silently alter the original saved information.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(value, right[key]) for key, value in left.items())
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return left.hex() == right.hex()
    return left == right


def ensure_schema(db):
    # Called only after an existing database has passed the game validators.
    db.executescript("""
      CREATE TABLE IF NOT EXISTS state_archive(
        digest TEXT PRIMARY KEY, codec TEXT NOT NULL, payload BLOB NOT NULL,
        raw_bytes INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS archive_catalog(
        kind TEXT NOT NULL, owner TEXT NOT NULL, item TEXT NOT NULL,
        digest TEXT NOT NULL REFERENCES state_archive(digest),
        country TEXT, region TEXT, day TEXT, active INTEGER, parent TEXT,
        PRIMARY KEY(kind,owner,item,digest));
      CREATE TABLE IF NOT EXISTS state_heads(
        kind TEXT NOT NULL, owner TEXT NOT NULL, item TEXT NOT NULL,
        digest TEXT NOT NULL REFERENCES state_archive(digest),
        country TEXT, region TEXT, day TEXT, active INTEGER, parent TEXT,
        PRIMARY KEY(kind,owner,item));
      CREATE TABLE IF NOT EXISTS state_components(
        path TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)));
      CREATE TRIGGER IF NOT EXISTS archive_no_update BEFORE UPDATE ON state_archive
        BEGIN SELECT RAISE(ABORT,'immutable state archive'); END;
      CREATE TRIGGER IF NOT EXISTS archive_no_delete BEFORE DELETE ON state_archive
        BEGIN SELECT RAISE(ABORT,'immutable state archive'); END;
      CREATE TRIGGER IF NOT EXISTS archive_no_replace BEFORE INSERT ON state_archive
        WHEN EXISTS(SELECT 1 FROM state_archive WHERE digest=NEW.digest)
        AND (NEW.codec!= (SELECT codec FROM state_archive WHERE digest=NEW.digest)
          OR NEW.payload!=(SELECT payload FROM state_archive WHERE digest=NEW.digest)
          OR NEW.raw_bytes!=(SELECT raw_bytes FROM state_archive WHERE digest=NEW.digest))
        BEGIN SELECT RAISE(ABORT,'immutable state archive'); END;
      CREATE TRIGGER IF NOT EXISTS catalog_no_update BEFORE UPDATE ON archive_catalog
        BEGIN SELECT RAISE(ABORT,'immutable archive catalog'); END;
      CREATE TRIGGER IF NOT EXISTS catalog_no_delete BEFORE DELETE ON archive_catalog
        BEGIN SELECT RAISE(ABORT,'immutable archive catalog'); END;
      CREATE TRIGGER IF NOT EXISTS catalog_no_replace BEFORE INSERT ON archive_catalog
        WHEN EXISTS(SELECT 1 FROM archive_catalog WHERE kind=NEW.kind AND owner=NEW.owner
          AND item=NEW.item AND digest=NEW.digest AND
          (country IS NOT NEW.country OR region IS NOT NEW.region OR day IS NOT NEW.day
            OR active IS NOT NEW.active OR parent IS NOT NEW.parent))
        BEGIN SELECT RAISE(ABORT,'immutable archive catalog'); END;
      CREATE INDEX IF NOT EXISTS archive_actor_date ON archive_catalog(owner,kind,day);
      CREATE INDEX IF NOT EXISTS heads_actor_date ON state_heads(owner,kind,day);
      CREATE INDEX IF NOT EXISTS heads_active_country ON state_heads(kind,active,country,region);
      CREATE INDEX IF NOT EXISTS heads_parent ON state_heads(kind,parent);
    """)


def _store(db, value, kind, owner, item, cache):
    key = (kind, owner, str(item))
    cache[None].add(key)
    cached = cache.get(key)
    token = version(value)
    # Equality checks every nested value, including direct edits by trusted code.
    # A cached identity alone would silently lose mutations and is never enough.
    if cached is not None and (token is not None and len(cached) > 2 and cached[2] == token
                              or _same_json(value, cached[0])):
        if token is not None:
            cache[key] = (cached[0], cached[1], token)
        return {REFERENCE: cached[1]}
    else:
        raw = _encode(value).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        packed = zlib.compress(raw, 3)
        db.execute("INSERT OR IGNORE INTO state_archive VALUES(?,?,?,?)",
                   (digest, "zlib-json-v1", packed, len(raw)))
        # Detached decoded snapshot: later in-place mutations cannot fool cache.
        cache[key] = (json.loads(raw), digest, token)
    country = value.get("country") if isinstance(value, dict) else None
    region = value.get("region") if isinstance(value, dict) else None
    day = (value.get("date") or value.get("as_of") or value.get("day") or value.get("start_date")) if isinstance(value, dict) else None
    if kind == 'military_political_decisions':
        body = value['body']
        country = body['request'].get('country')
        region = body['request']['origin_region']
        day = body['date']
    active = int(value.get("ongoing", False)) if kind == "event" else None
    parent = value.get("parent_event") if isinstance(value, dict) else None
    db.execute("INSERT OR IGNORE INTO archive_catalog VALUES(?,?,?,?,?,?,?,?,?)",
               (kind, owner, str(item), digest, country, region, day, active, parent))
    db.execute("INSERT INTO state_heads VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,owner,item) DO UPDATE SET "
               "digest=excluded.digest,country=excluded.country,region=excluded.region,day=excluded.day,"
               "active=excluded.active,parent=excluded.parent",
               (kind, owner, str(item), digest, country, region, day, active, parent))
    return {REFERENCE: digest}


def compact(db, payload, cache=None):
    """Compose a physical projection without modifying the logical payload."""
    cache = {} if cache is None else cache
    cache[None] = set()
    world = payload["world"]
    physical_world = dict(world)
    npc = world.get("npc_state")
    if npc is not None:
        out = dict(npc)
        for field, kind in (("reports", "report"), ("relationships", "relation")):
            out[field] = {owner: {item: _store(db, value, kind, owner, item, cache)
                          for item, value in values.items()} for owner, values in npc[field].items()}
        for field, kind in (("memories", "memory"), ("institutional_memories", "institutional_memory")):
            out[field] = {owner: [_store(db, value, kind, owner, value["event_id"], cache)
                          for value in values] for owner, values in npc[field].items()}
        out["records"] = {item: _store(db, value, "record", value.get("subject", ""), item, cache)
                          for item, value in npc["records"].items()}
        for field, kind in (("decisions", "decision"), ("actions", "action")):
            out[field] = [_store(db, value, kind, value.get("actor", ""), value.get("id", str(index)), cache)
                          for index, value in enumerate(npc[field])]
        physical_world["npc_state"] = out
    crisis = world.get("crisis_state")
    if crisis is not None:
        out = dict(crisis)
        for field, kind in (("events", "event"), ("messages", "message"), ("effects", "effect")):
            out[field] = {item: _store(db, value, kind, value.get("actor", ""), item, cache)
                          for item, value in crisis[field].items()}
        physical_world["crisis_state"] = out
    military = world.get('military_state')
    if military is not None:
        out = dict(military)
        for field in ('operations', 'commands', 'loss_receipts', 'procurement_receipts', 'shipments', 'logistics_receipts', 'innovations'):
            out[field] = {item: _store(db, value, 'military_' + field, value.get('actor', ''), item, cache)
                          for item, value in military[field].items()}
        if 'political_decisions' in military:
            out['political_decisions'] = {item: _store(db, value, 'military_political_decisions',
                value['body']['request']['actor'], item, cache)
                for item, value in military['political_decisions'].items()}
        physical_world['military_state'] = out
    for field, kind in (('diplomacy_state', 'diplomacy_receipts'),
                        ('intelligence_state', 'intelligence_receipts')):
        domain = world.get(field)
        if domain is not None:
            out = dict(domain)
            out['receipts'] = {item: _store(db, value, kind, '', item, cache)
                               for item, value in domain['receipts'].items()}
            physical_world[field] = out
    used = cache.pop(None)
    # Only current references expire. Historical objects and their catalog never
    # disappear when the unchanged NPC recent-decision window advances.
    obsolete = [key for key in db.execute("SELECT kind,owner,item FROM state_heads") if key not in used]
    db.executemany("DELETE FROM state_heads WHERE kind=? AND owner=? AND item=?", obsolete)
    for key in list(cache):
        if key not in used:
            del cache[key]
    return {**payload, "world": physical_world, MARKER: VERSION}


def _archive_raw(digest, row):
    """Verify one immutable archive body shared by detached query decoders."""
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Malformed archive reference")
    if row is None or row[0] != "zlib-json-v1":
        raise ValueError("Missing or unsupported state archive object")
    raw = zlib.decompress(row[1])
    if len(raw) != row[2] or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("State archive checksum mismatch")
    return raw


def _expand(db, value, blobs):
    if isinstance(value, dict):
        if set(value) == {REFERENCE}:
            digest = value[REFERENCE]
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("Malformed archive reference")
            if digest not in blobs:
                row = db.execute("SELECT codec,payload,raw_bytes FROM state_archive WHERE digest=?", (digest,)).fetchone()
                blobs[digest] = _archive_raw(digest, row)
            # Separate decode per reference preserves public inspection isolation.
            return json.loads(blobs[digest])
        return {key: _expand(db, item, blobs) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(db, item, blobs) for item in value]
    return value


def read(db, text):
    payload = json.loads(text)
    if COMPONENTS in payload:
        if (set(payload) != {MARKER, COMPONENTS, 'paths', 'revision'} or payload.get(MARKER) != VERSION
            or type(payload[COMPONENTS]) is not int or payload[COMPONENTS] != 1
            or type(payload['revision']) is not int or payload['revision'] < 1
            or not isinstance(payload['paths'], list)):
            raise ValueError('Unsupported SQLite component storage version')
        paths = payload['paths']
        if any(not isinstance(p, list) or not 1 <= len(p) <= 3 or not all(isinstance(k, str) and k for k in p)
               for p in paths) or len({tuple(p) for p in paths}) != len(paths):
            raise ValueError('Invalid SQLite component references')
        rows = dict(db.execute('SELECT path,payload FROM state_components'))
        if set(rows) != {_encode(p) for p in paths}:
            raise ValueError('Missing or unreferenced SQLite component')
        payload = {MARKER: VERSION}
        for path in paths:
            value = json.loads(rows[_encode(path)])
            destination = payload
            for key in path[:-1]:
                if key in destination and not isinstance(destination[key], dict):
                    raise ValueError('Conflicting SQLite component reference')
                destination = destination.setdefault(key, {})
            if path[-1] in destination:
                raise ValueError('Conflicting SQLite component reference')
            destination[path[-1]] = value
    if MARKER not in payload:
        return payload  # Original SQLite format is still supported.
    if type(payload[MARKER]) is not int or payload[MARKER] != VERSION:
        raise ValueError("Unsupported SQLite physical state storage version")
    payload.pop(MARKER)
    try:
        # Expand only schema-owned archival positions. A user's canon/facts may
        # legitimately contain a dictionary resembling an internal reference;
        # treating arbitrary game content as storage metadata would change it.
        world, blobs = payload["world"], {}
        npc = world.get("npc_state")
        if npc is not None:
            for field in ("reports", "relationships"):
                npc[field] = {owner: {key: _expand(db, value, blobs) for key, value in values.items()}
                              for owner, values in npc[field].items()}
            for field in ("memories", "institutional_memories"):
                npc[field] = {owner: [_expand(db, value, blobs) for value in values]
                              for owner, values in npc[field].items()}
            npc["records"] = {key: _expand(db, value, blobs) for key, value in npc["records"].items()}
            for field in ("decisions", "actions"):
                npc[field] = [_expand(db, value, blobs) for value in npc[field]]
        crisis = world.get("crisis_state")
        if crisis is not None:
            for field in ("events", "messages", "effects"):
                crisis[field] = {key: _expand(db, value, blobs) for key, value in crisis[field].items()}
        military = world.get('military_state')
        if military is not None:
            for field in ('operations', 'commands', 'loss_receipts', 'procurement_receipts', 'shipments', 'logistics_receipts', 'innovations'):
                military[field] = {key: _expand(db, value, blobs) for key, value in military[field].items()}
            if 'political_decisions' in military:
                military['political_decisions'] = {key: _expand(db, value, blobs)
                    for key, value in military['political_decisions'].items()}
        for field in ('diplomacy_state', 'intelligence_state'):
            domain = world.get(field)
            if domain is not None:
                domain['receipts'] = {key: _expand(db, value, blobs)
                                      for key, value in domain['receipts'].items()}
        return payload
    except (zlib.error, json.JSONDecodeError) as exc:
        raise ValueError("Corrupt state archive object") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed compact SQLite state") from exc


def write_components(db, physical, logical, cache, revision):
    """Persist only dirty current components; archival bodies remain append-only.

    The existing physical version-one archive references are preserved. This
    separate component envelope is internal SQLite metadata, never a save schema.
    """
    flat = {}
    for name, value in physical.items():
        if name == MARKER:
            continue
        if name != 'world':
            flat[(name,)] = (value, version(logical.get(name)))
            continue
        for field, component in value.items():
            source = logical['world'][field]
            if field in ('npc_state', 'crisis_state', 'military_state',
                         'diplomacy_state', 'intelligence_state') and isinstance(component, dict):
                for bucket, item in component.items():
                    flat[('world', field, bucket)] = (item, version(source.get(bucket)))
            else:
                flat[('world', field)] = (component, version(source))
    for path, (value, token) in flat.items():
        prior = cache.get(path)
        if token is not None and prior is not None and prior[1] == token:
            continue
        text = _encode(value)
        if prior is None or prior[0] != text:
            db.execute('INSERT INTO state_components(path,payload) VALUES(?,?) '
                       'ON CONFLICT(path) DO UPDATE SET payload=excluded.payload', (_encode(path), text))
        cache[path] = (text, token)
    obsolete = set(cache) - set(flat)
    for path in obsolete:
        db.execute('DELETE FROM state_components WHERE path=?', (_encode(path),))
        del cache[path]
    # When resuming, cache is initially empty. Expired component rows are still
    # removed without touching any historical archive or catalog.
    present = {_encode(list(p)) for p in flat}
    for path, in db.execute('SELECT path FROM state_components').fetchall():
        if path not in present:
            db.execute('DELETE FROM state_components WHERE path=?', (path,))
    return {MARKER: VERSION, COMPONENTS: 1, 'paths': [list(p) for p in sorted(flat)], 'revision': revision}


def history(db, *, kind, owner=None, item=None):
    """Detached immutable past versions; never used to broadcast truth to NPCs."""
    clauses, values = ["c.kind=?"], [kind]
    for name, value in (("owner", owner), ("item", item)):
        if value is not None:
            clauses.append("c." + name + "=?")
            values.append(value)
    # Keep missing blobs visible: an inner join would silently omit corrupted
    # catalog references. Query-local raw bytes are never retained across calls.
    query = ("SELECT c.digest,c.owner,c.item,c.day,a.codec,a.payload,a.raw_bytes "
             "FROM archive_catalog AS c LEFT JOIN state_archive AS a ON a.digest=c.digest WHERE "
             + " AND ".join(clauses) + " ORDER BY c.day,c.owner,c.item,c.digest")
    result, blobs = [], {}
    for digest, actor, key, day, codec, payload, raw_bytes in db.execute(query, values):
        if digest not in blobs:
            blobs[digest] = _archive_raw(digest, (codec, payload, raw_bytes))
        # Separate decoding preserves isolation even when catalog rows share a
        # content-addressed digest, just as public inspection did before batching.
        value = json.loads(blobs[digest])
        if kind in ('diplomacy_receipts', 'intelligence_receipts'):
            expected = _di_receipt_identity(kind, value)
            if any(current != expected[name] for name, current in
                   (("owner", actor), ("item", key), ("day", day))):
                raise ValueError("D&I historical archive conflicts with receipt identity")
        result.append({"digest": digest, "owner": actor, "item": key, "day": day,
                       "value": value})
    return result


CATALOG_FIELDS = ("kind", "owner", "item", "digest", "country", "region", "day", "active", "parent")


def _di_receipt_identity(kind, value):
    # Stage 1 stores one feature activation receipt in the Diplomacy domain.
    # Intelligence's namespace is reserved, but no receipt workload is supported.
    if kind != 'diplomacy_receipts':
        raise ValueError("D&I Stage 1 does not support intelligence archive receipts")
    from .diplomacy_intelligence import validate_activation_receipt
    validate_activation_receipt(value)
    return {"owner": "", "item": value["id"], "country": None, "region": None,
            "day": value["date"], "active": None, "parent": None}


def export_archive(db):
    """Self-contained human-readable cold history for occasional JSON saves."""
    objects = {digest: json.loads(_archive_raw(digest, (codec, payload, raw_bytes)))
               for digest, codec, payload, raw_bytes in db.execute(
                   "SELECT digest,codec,payload,raw_bytes FROM state_archive ORDER BY digest")}
    if not objects:
        return None
    catalog = [dict(zip(CATALOG_FIELDS, row)) for row in db.execute(
        "SELECT * FROM archive_catalog ORDER BY kind,owner,item,digest")]
    return {"version": VERSION, "objects": objects, "catalog": catalog}


def validate_archive(archive):
    if (not isinstance(archive, dict) or set(archive) != {"version", "objects", "catalog"}
            or type(archive["version"]) is not int or archive["version"] != VERSION
            or not isinstance(archive["objects"], dict) or not isinstance(archive["catalog"], list)):
        raise ValueError("Unsupported historical archive schema")
    for digest, value in archive["objects"].items():
        if not isinstance(digest, str) or hashlib.sha256(_encode(value).encode("utf-8")).hexdigest() != digest:
            raise ValueError("Historical archive checksum mismatch")
    seen = set()
    for row in archive["catalog"]:
        if (not isinstance(row, dict) or set(row) != set(CATALOG_FIELDS)
                or any(not isinstance(row[k], str) for k in ("kind", "owner", "item", "digest"))
                or row["digest"] not in archive["objects"]
                or any(row[k] is not None and not isinstance(row[k], str) for k in ("country", "region", "day", "parent"))
                or row["active"] is not None and (type(row["active"]) is not int or row["active"] not in (0, 1))):
            raise ValueError("Invalid historical archive catalog")
        key = tuple(row[k] for k in ("kind", "owner", "item", "digest"))
        if key in seen:
            raise ValueError("Repeated historical archive catalog item")
        seen.add(key)
        if row['kind'] in ('diplomacy_receipts', 'intelligence_receipts'):
            expected = _di_receipt_identity(row['kind'], archive['objects'][row['digest']])
            if any(row[name] != value for name, value in expected.items()):
                raise ValueError("D&I archive catalog conflicts with receipt identity")
        if row["kind"] == "military_political_decisions":
            # Catalog identity controls historical actor/country queries. A valid
            # blob digest alone cannot authorize a contradictory catalog label.
            value = archive["objects"][row["digest"]]
            body = value.get("body") if isinstance(value, dict) else None
            request = body.get("request") if isinstance(body, dict) else None
            if (not isinstance(request, dict)
                    or any(k not in request for k in ("actor", "country", "origin_region"))
                    or any(k not in body for k in ("id", "date"))
                    or any(not isinstance(v, str) or not v for v in
                           (request["actor"], request["origin_region"], body["id"], body["date"]))
                    or request["country"] is not None and
                       (not isinstance(request["country"], str) or not request["country"])):
                raise ValueError("Malformed military political archive identity")
            expected = {"owner": request["actor"], "item": body["id"],
                        "country": request["country"], "region": request["origin_region"],
                        "day": body["date"]}
            if any(row[k] != v for k, v in expected.items()):
                raise ValueError("Military political archive catalog conflicts with receipt identity")
    return archive


def import_archive(db, archive):
    validate_archive(archive)
    for digest, value in archive["objects"].items():
        raw = _encode(value).encode("utf-8")
        db.execute("INSERT OR IGNORE INTO state_archive VALUES(?,?,?,?)", (digest, "zlib-json-v1", zlib.compress(raw, 3), len(raw)))
    for row in archive["catalog"]:
        db.execute("INSERT OR IGNORE INTO archive_catalog VALUES(?,?,?,?,?,?,?,?,?)", tuple(row[k] for k in CATALOG_FIELDS))
