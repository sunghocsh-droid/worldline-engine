"""Version-one persistence contract, independent of simulation execution."""
import math
import copy
import random
import json
from .audit import Auditor
from .models import World, canonical_date

SAVE_SCHEMA_VERSION = 2
STABILIZED_SAVE_SCHEMA_VERSION = 3
NPC_SAVE_SCHEMA_VERSION = 4
CRISIS_SAVE_SCHEMA_VERSION = 5
MILITARY_SAVE_SCHEMA_VERSION = 6
DIPLOMACY_INTELLIGENCE_SAVE_SCHEMA_VERSION = 7


def migrate_snapshot(payload):
    version = payload.get("schema_version")
    if type(version) is not int or version not in (1, 2, 3, 4, 5, 6, 7):
        raise ValueError(f"Unsupported save schema version: {version}")
    economy = payload.get("engine", {}).get("world", {}).get("economy")
    stabilized = economy is not None and "stabilization_version" in economy
    npc = payload.get("engine", {}).get("world", {}).get("npc_state") is not None
    crisis = payload.get("engine", {}).get("world", {}).get("crisis_state") is not None
    military = payload.get('engine', {}).get('world', {}).get('military_state') is not None
    raw_world = payload.get('engine', {}).get('world', {})
    diplomacy = 'diplomacy_state' in raw_world or 'intelligence_state' in raw_world
    if diplomacy or version == 7:
        if (version != 7 or raw_world.get('schema_version') != 6
                or raw_world.get('diplomacy_state') is None or raw_world.get('intelligence_state') is None
                or not all((stabilized, npc, crisis, military))):
            raise ValueError('Save schema does not match Diplomacy & Intelligence rules')
    else:
        if military and (version != 6 or not crisis) or version == 6 and not military:
            raise ValueError('Save schema does not match military/logistics rules')
        if crisis and (version != (6 if military else 5) or not npc or not stabilized) or version == 5 and not crisis:
            raise ValueError("Save schema does not match crisis/event rules")
        if npc and (version != (6 if military else 5 if crisis else 4) or not stabilized) or version == 4 and not npc:
            raise ValueError("Save schema does not match NPC decision rules")
        if not npc and ((version == 3 and not stabilized) or (version != 3 and stabilized)):
            raise ValueError("Save schema does not match stabilized economic rules")
    migrated = copy.deepcopy(payload)
    migrated["engine"]["world"] = World.from_dict(payload["engine"]["world"]).to_dict()
    migrated["schema_version"] = DIPLOMACY_INTELLIGENCE_SAVE_SCHEMA_VERSION if diplomacy else (MILITARY_SAVE_SCHEMA_VERSION if military else (CRISIS_SAVE_SCHEMA_VERSION if crisis else (NPC_SAVE_SCHEMA_VERSION if npc else (STABILIZED_SAVE_SCHEMA_VERSION if stabilized else SAVE_SCHEMA_VERSION))))
    return migrated


def tuples(value):
    return tuple(tuples(v) for v in value) if isinstance(value, list) else value


def normalize_engine(payload):
    """Fill supported v1 defaults without mistaking them for a concurrent writer."""
    # Validation plus to_dict already detaches the entire World. The former
    # full-payload deepcopy immediately discarded a second copy of that history.
    # Preserve the same validation and detached result; copy only the metadata.
    world = validate_engine(payload)
    metadata = copy.deepcopy({key: value for key, value in payload.items() if key != 'world'})
    normalized = {key: world.to_dict() if key == 'world' else metadata[key] for key in payload}
    if not normalized.get("systems"):
        normalized.pop("systems", None)
    return normalized


def validate_engine(payload, *, audit=True):
    """Validate all resumable state, including queues and reports, not just World."""
    try:
        if not isinstance(payload, dict) or set(payload) - {"world", "seed", "rng", "observed", "orders", "journeys", "systems"}:
            raise ValueError("Unsupported engine payload fields")
        world = World.from_dict(payload["world"])
        if audit:
            Auditor().check(world)
        today = canonical_date(world.date)
        if type(payload["seed"]) is not int:
            raise ValueError("Seed must be an integer")
        random.Random().setstate(tuples(payload["rng"]))
        if not all(isinstance(payload[k], dict) for k in ("observed", "orders", "journeys")):
            raise ValueError("Invalid persisted mappings")
        for player, reports in payload["observed"].items():
            if not isinstance(player, str) or not isinstance(reports, dict):
                raise ValueError("Invalid observer")
            for key, report in reports.items():
                if not isinstance(key, str) or set(report) != {"as_of", "source", "confidence", "data"}:
                    raise ValueError("Invalid report schema")
                confidence = report["confidence"]
                if (canonical_date(report["as_of"]) > today or not isinstance(report["source"], str)
                    or not isinstance(report["data"], dict) or type(confidence) not in (int, float)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                    raise ValueError("Invalid report contents")
        for key, result in payload["orders"].items():
            order = result["order"]
            if not isinstance(key, str) or not key or order["id"] != key:
                raise ValueError("Invalid persisted order ID")
            if set(order) != {"id", "actor", "kind", "params"} or not all(isinstance(order[k], str) for k in ("actor", "kind")):
                raise ValueError("Invalid persisted order schema")
            # Rejected orders may have invalid parameters: preserve their original input.
            if result["status"] == "accepted":
                if type(result["cost"]) is not int or result["cost"] < 0 or not isinstance(order["params"], dict):
                    raise ValueError("Invalid accepted order")
            elif result["status"] != "rejected" or not isinstance(result["reason"], str):
                raise ValueError("Invalid order decision")
        for army, journey in payload["journeys"].items():
            a = world.armies[army]
            start, end = world.regions[journey["origin"]], world.regions[journey["destination"]]
            duration, remaining = journey["duration"], journey["remaining"]
            elapsed = (today - canonical_date(journey["departure"])).days
            minimum = max(1, math.ceil(math.hypot(end.x-start.x, end.y-start.y) / a.speed_km_day))
            if (type(duration) is not int or type(remaining) is not int or not 0 < remaining <= duration
                or elapsed < 0 or elapsed != duration - remaining or duration < minimum
                or a.region != journey["origin"]):
                raise ValueError("Invalid pending army journey")
        systems = payload.get("systems", [])
        if not isinstance(systems, list):
            raise ValueError("Invalid system manifest")
        names = []
        for spec in systems:
            if set(spec) != {"name", "version"} or not all(isinstance(v, str) and v for v in spec.values()):
                raise ValueError("Invalid system manifest entry")
            names.append(spec["name"])
        if len(set(names)) != len(names):
            raise ValueError("Duplicate system names")
        if world.crisis_state is not None:
            required = [{"name": name, "version": "1"} for name in
                ("worldline.projects", "worldline.economy", "worldline.npc", "worldline.crisis")]
            if systems[:4] != required:
                raise ValueError("Crisis save requires the ordered, supported system manifest")
        if world.military_state is not None and systems[:5] != required + [{'name': 'worldline.military', 'version': '1'}]:
            raise ValueError('Military save requires the ordered, supported system manifest')
        if world.diplomacy_state is not None:
            from .diplomacy_intelligence import enabled_manifest
            if systems != enabled_manifest():
                raise ValueError('Diplomacy & Intelligence save requires the complete supported system manifest')
        elif any(name in ('worldline.intelligence', 'worldline.diplomacy') for name in names):
            raise ValueError('Diplomacy & Intelligence systems require explicit activation')
        return world
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed engine save data") from exc


def validate_snapshot(payload):
    try:
        payload = migrate_snapshot(payload)
        world = validate_engine(payload["engine"])
        events = payload["ledger"]
        if not isinstance(events, list) or not events:
            raise ValueError("Missing event ledger")
        previous_day = None
        decisions = {}
        crisis_records = {kind: {} for kind in
            ("event_started", "event_resolved", "effect_applied", "effect_queued", "response_outcome_measured")}
        diplomacy_activations = []
        for index, event in enumerate(events, 1):
            day = canonical_date(event["day"])
            if (type(event["id"]) is not int or event["id"] != index or day > canonical_date(world.date)
                or previous_day is not None and day < previous_day or not isinstance(event["kind"], str)
                or not isinstance(event["payload"], dict)):
                raise ValueError("Invalid ledger sequence/date/schema")
            previous_day = day
            if event['kind'] == 'diplomacy_intelligence_enabled':
                diplomacy_activations.append((event['day'], event['payload']))
            if event["kind"] == "order_decided":
                order_id = event["payload"]["order"]["id"]
                if order_id in decisions:
                    raise ValueError("Repeated order decision in ledger")
                decisions[order_id] = event["payload"]
            record = event["payload"]
            if event["kind"] == "system_event" and record.get("system") == "crisis" and record.get("kind") in crisis_records:
                kind = record["kind"]
                key = _crisis_record_key(record)
                if key in crisis_records[kind]:
                    raise ValueError("Repeated crisis record in immutable ledger")
                crisis_records[kind][key] = record
        first = events[0]
        if (first["kind"] != "game_created" or first["payload"]["seed"] != payload["engine"]["seed"]
            or first["payload"]["scenario"] != world.scenario_id):
            raise ValueError("Ledger origin differs from world")
        if decisions != payload["engine"]["orders"]:
            raise ValueError("Order state differs from immutable ledger")
        _validate_crisis_ledger(world, crisis_records)
        validate_diplomacy_intelligence_ledger(world, diplomacy_activations)
        if world.diplomacy_state is not None and 'state_archive' not in payload:
            raise ValueError('Diplomacy & Intelligence save requires its activation archive')
        if "state_archive" in payload:
            from .state_storage import validate_archive
            validate_archive(payload["state_archive"])
            validate_diplomacy_intelligence_archive(world, payload['state_archive'])
        return payload
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed snapshot") from exc


def validate_diplomacy_intelligence_ledger(world, activations):
    """One explicit activation must match its permanent domain receipt."""
    if world.diplomacy_state is None:
        if activations:
            raise ValueError('Diplomacy & Intelligence state missing from recorded world')
        return
    from .diplomacy_intelligence import ACTIVATION_ID, enabled_manifest
    if len(activations) != 1:
        raise ValueError('Diplomacy & Intelligence requires exactly one activation in the immutable ledger')
    day, record = activations[0]
    receipt = world.diplomacy_state['receipts'][ACTIVATION_ID]
    expected = {'receipt': receipt, 'systems': enabled_manifest()}
    if (not isinstance(record, dict) or json.dumps(record, sort_keys=True, allow_nan=False)
            != json.dumps(expected, sort_keys=True, allow_nan=False) or day != receipt['date']):
        raise ValueError('Diplomacy & Intelligence activation conflicts with immutable ledger')


def validate_diplomacy_intelligence_archive(world, archive):
    """Historical activation identity and body must match its live receipt."""
    rows = [row for row in archive['catalog']
            if row['kind'] in ('diplomacy_receipts', 'intelligence_receipts')]
    if world.diplomacy_state is None:
        if rows:
            raise ValueError('Diplomacy & Intelligence archive requires activated state')
        return
    from .diplomacy_intelligence import ACTIVATION_ID
    receipt = world.diplomacy_state['receipts'][ACTIVATION_ID]
    if (len(rows) != 1 or json.dumps(archive['objects'][rows[0]['digest']], sort_keys=True, allow_nan=False)
            != json.dumps(receipt, sort_keys=True, allow_nan=False)):
        raise ValueError('Diplomacy & Intelligence archive differs from its immutable activation receipt')


def _validate_crisis_ledger(world, records):
    """Cross-check mutable crisis state against permanent creation/application facts."""
    state = world.crisis_state
    if state is None:
        if any(records.values()):
            raise ValueError("Crisis state missing from recorded world")
        return
    # Outbox entries have not been published yet; they are saved obligations, not lost history.
    pending = {kind: {} for kind in records}
    for item in state["outbox"]:
        kind = item.get("kind")
        if kind in pending:
            key = _crisis_record_key(item)
            if key in pending[kind] or key in records[kind]:
                raise ValueError("Repeated pending crisis record")
            pending[kind][key] = item
    merged = {kind: {**records[kind], **pending[kind]} for kind in records}
    if set(merged["event_started"]) != set(state["events"]):
        raise ValueError("Crisis events differ from immutable ledger")
    fields = ("event_id", "event_type", "start_date", "created_at", "region", "countries",
              "parent_event", "actual_facts", "causes", "triggering_conditions", "rng_key")
    for key, record in merged["event_started"].items():
        current = state["events"][key]
        if any(record["event"].get(f) != current.get(f) for f in fields):
            raise ValueError("Crisis creation facts conflict with immutable ledger")
    closed = {key for key, event in state["events"].items() if not event["ongoing"]}
    if set(merged["event_resolved"]) != closed:
        raise ValueError("Crisis resolution state differs from immutable ledger")
    for key, record in merged["event_resolved"].items():
        if any(record["event"].get(f) != state["events"][key].get(f)
               for f in ("end_date", "resolution_state")):
            raise ValueError("Crisis resolution facts conflict with immutable ledger")
    if set(merged["effect_queued"]) != set(state["effects"]):
        raise ValueError("Crisis consequence queue differs from immutable ledger")
    for key, record in merged["effect_queued"].items():
        if any(record["effect"].get(f) != state["effects"][key].get(f)
               for f in ("effect_id", "event_id", "kind", "payload", "created_at", "due_date", "idempotency_key")):
            raise ValueError("Crisis scheduled consequence conflicts with immutable ledger")
    applied = {key for key, effect in state["effects"].items() if effect["applied"]}
    if set(merged["effect_applied"]) != applied:
        raise ValueError("Crisis application history differs from immutable ledger")
    for key, record in merged["effect_applied"].items():
        effect = state["effects"][key]
        if record.get("event_id") != effect["event_id"] or record.get("receipt") != effect["receipt"]:
            raise ValueError("Crisis damage receipt conflicts with immutable ledger")
    measured = {(key, country): outcome for key, event in state["events"].items()
                for country, outcome in event.get("measured_response_outcomes", {}).items()}
    if set(merged["response_outcome_measured"]) != set(measured):
        raise ValueError("Crisis measured outcomes differ from immutable ledger")
    for key, record in merged["response_outcome_measured"].items():
        if record.get("record_id") != measured[key]["record_id"] or record.get("facts") != measured[key]["facts"]:
            raise ValueError("Crisis measured response conflicts with immutable ledger")


def _crisis_record_key(record):
    if record["kind"] == "response_outcome_measured":
        return record["event_id"], record["country"]
    return record["effect_id" if record["kind"].startswith("effect_") else "event_id"]
