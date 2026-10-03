"""Explicit opt-in storage scaffold for Diplomacy & Intelligence v1.

Stage 1 supports activation and empty domain state only. The scheduler stages
are deliberate no-ops: contact, NPC choice, observations and delivery are not
implemented here. Activation is a trusted-host operation, not authentication.
"""
import copy
import json

from .models import canonical_date

VERSION = "1"
STATE_VERSION = 1
ACTIVATION_ID = "diplomacy-intelligence-activation-1"
BUILTIN_PREFIX = ("worldline.projects", "worldline.economy", "worldline.npc",
                  "worldline.crisis", "worldline.military")
STAGES = ("worldline.intelligence", "worldline.diplomacy")
_DIPLOMACY_KEYS = {"version", "config", "proposals", "dispatches", "receipts", "outbox", "next_ids"}
_INTELLIGENCE_KEYS = {"version", "config", "observations", "dispatches", "receipts", "outbox", "next_ids"}
_RECEIPT_KEYS = {"id", "kind", "date", "version", "config"}


def builtin_manifest():
    return [{"name": name, "version": VERSION} for name in BUILTIN_PREFIX]


def enabled_manifest():
    return builtin_manifest() + [{"name": name, "version": VERSION} for name in STAGES]


def _identifier(value):
    return isinstance(value, str) and bool(value)


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("Invalid " + label + " keys")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _profile(world, schema):
    if type(world.schema_version) is not int or world.schema_version != schema:
        raise ValueError("D&I Stage 1 requires World schema " + str(schema))
    for name, version in (("economy", 3), ("npc_state", 1), ("crisis_state", 1), ("military_state", 1)):
        value = getattr(world, name, None)
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != version):
            raise ValueError("D&I requires Economy v3 / NPC v1 / Crisis v1 / Military v1")
    for key, value in (("model_revision", 3), ("stabilization_version", 1)):
        if type(world.economy.get(key)) is not int or world.economy[key] != value:
            raise ValueError("D&I requires stabilized Economy model revision 3")


def _recipient(world, reference, bindings, *, activation=False, country=None, player=None):
    if not isinstance(reference, dict):
        raise ValueError("D&I recipient must be a typed reference")
    kind = reference.get("kind")
    if kind == "person":
        _object(reference, {"kind", "id"}, "person recipient")
        person_id = reference["id"]
        if not _identifier(person_id) or person_id not in world.npc_state["persons"]:
            raise ValueError("D&I recipient references an unknown person")
        person = world.npc_state["persons"][person_id]
        if not isinstance(person, dict) or person.get("country") not in world.countries:
            raise ValueError("D&I recipient has an unknown country")
        if country is not None and person["country"] != country:
            raise ValueError("D&I reply person must belong to the bound country")
        if activation and person.get("active") is not True:
            raise ValueError("D&I activation recipient must be active")
        return kind, person_id
    if kind == "player":
        _object(reference, {"kind", "id"}, "player recipient")
        player_id = reference["id"]
        if not _identifier(player_id) or player_id not in bindings:
            raise ValueError("D&I recipient references an unbound player")
        if player is not None and player_id != player:
            raise ValueError("D&I reply player must be the bound requesting player")
        return kind, player_id
    raise ValueError("D&I Stage 1 supports person and player recipients only")


def _recipients(world, references, bindings, *, activation=False, country=None, player=None, nonempty=False):
    if not isinstance(references, list) or nonempty and not references:
        raise ValueError("D&I recipient list is malformed")
    identities = [_recipient(world, ref, bindings, activation=activation, country=country, player=player)
                  for ref in references]
    if len(set(identities)) != len(identities):
        raise ValueError("D&I recipient list contains duplicate identities")


def normalize_config(world, config=None, *, activation=False):
    """Validate known configuration only; never infer audiences or change NPCs."""
    if config is None:
        config = {}
    if not isinstance(config, dict) or set(config) - {"diplomacy", "intelligence"}:
        raise ValueError("Unknown or malformed D&I configuration")
    result = {}
    defaults = {
        "diplomacy": {"proposal_delay_days": 1, "response_delay_days": 1,
                      "player_bindings": {}, "reply_recipients": {}},
        "intelligence": {"delay_days": 1, "sources": {}, "audience": []},
    }
    for domain, default in defaults.items():
        supplied = config.get(domain, {})
        if not isinstance(supplied, dict) or set(supplied) - set(default):
            raise ValueError("Unknown or malformed D&I " + domain + " configuration")
        result[domain] = {**copy.deepcopy(default), **copy.deepcopy(supplied)}
    diplomacy, intelligence = result["diplomacy"], result["intelligence"]
    for value in (diplomacy["proposal_delay_days"], diplomacy["response_delay_days"], intelligence["delay_days"]):
        if type(value) is not int or value < 0:
            raise ValueError("D&I delays must be nonnegative integer simulation days")
    bindings = diplomacy["player_bindings"]
    if not isinstance(bindings, dict):
        raise ValueError("D&I player_bindings must be an object")
    for player_id, binding in bindings.items():
        if not _identifier(player_id):
            raise ValueError("D&I player IDs must be nonempty strings")
        _object(binding, {"country_id", "representative_person_id"}, "player binding")
        country, person_id = binding["country_id"], binding["representative_person_id"]
        if not _identifier(country) or country not in world.countries:
            raise ValueError("D&I player binding references an unknown country")
        if not _identifier(person_id) or person_id not in world.npc_state["persons"]:
            raise ValueError("D&I player binding references an unknown person")
        person = world.npc_state["persons"][person_id]
        if not isinstance(person, dict) or person.get("country") != country:
            raise ValueError("D&I representative must belong to the bound country")
        government = world.npc_state["governments"].get(country)
        if not isinstance(government, dict) or government.get("country") != country:
            raise ValueError("D&I bound country requires an existing government")
        if activation:
            from .npc import _authority
            if (country not in world.crisis_state["player_countries"]
                    or government.get("autonomous") is not False):
                raise ValueError("D&I binding must respect the existing player-country policy")
            if not _authority(world.npc_state, person_id, country, "diplomatic_engagement"):
                raise ValueError("D&I representative lacks current diplomatic execution authority")
    replies = diplomacy["reply_recipients"]
    if not isinstance(replies, dict) or any(not _identifier(key) or key not in bindings for key in replies):
        raise ValueError("D&I reply routes require known bound players")
    for player_id, refs in replies.items():
        _recipients(world, refs, bindings, activation=activation,
                    country=bindings[player_id]["country_id"], player=player_id, nonempty=True)
    if not isinstance(intelligence["sources"], dict) or intelligence["sources"]:
        raise ValueError("D&I Stage 1 does not support intelligence source workloads")
    _recipients(world, intelligence["audience"], bindings, activation=activation)
    _json(result)
    return result


def validate_activation_receipt(receipt):
    """Validate structural identity without requiring a current World or NPC."""
    _object(receipt, _RECEIPT_KEYS, "D&I activation receipt")
    if (receipt["id"] != ACTIVATION_ID or receipt["kind"] != "activation"
            or type(receipt["version"]) is not int or receipt["version"] != STATE_VERSION):
        raise ValueError("Invalid D&I activation receipt identity")
    canonical_date(receipt["date"])
    _object(receipt["config"], {"diplomacy", "intelligence"}, "D&I activation config")
    _object(receipt["config"]["diplomacy"],
            {"proposal_delay_days", "response_delay_days", "player_bindings", "reply_recipients"},
            "D&I activation diplomacy config")
    _object(receipt["config"]["intelligence"], {"delay_days", "sources", "audience"},
            "D&I activation intelligence config")
    _json(receipt)


def initialize(world, config=None):
    """Trusted host activation; no authentication, RNG, dispatch or NPC mutation."""
    if (getattr(world, "diplomacy_state", None) is not None
            or getattr(world, "intelligence_state", None) is not None):
        raise ValueError("Diplomacy & Intelligence is already enabled")
    _profile(world, 5)
    normalized = normalize_config(world, config, activation=True)
    receipt = {"id": ACTIVATION_ID, "kind": "activation", "date": world.date,
               "version": STATE_VERSION, "config": copy.deepcopy(normalized)}
    candidate = copy.copy(world)
    candidate.schema_version = 6
    candidate.diplomacy_state = {
        "version": STATE_VERSION, "config": normalized["diplomacy"], "proposals": {},
        "dispatches": {}, "receipts": {ACTIVATION_ID: receipt}, "outbox": [],
        "next_ids": {"proposal": 1, "dispatch": 1, "receipt": 2, "sequence": 1},
    }
    candidate.intelligence_state = {
        "version": STATE_VERSION, "config": normalized["intelligence"], "observations": {},
        "dispatches": {}, "receipts": {}, "outbox": [],
        "next_ids": {"observation": 1, "dispatch": 1, "receipt": 1, "sequence": 1},
    }
    issues = validate(candidate)
    if issues:
        raise ValueError("; ".join(issues))
    world.diplomacy_state = candidate.diplomacy_state
    world.intelligence_state = candidate.intelligence_state
    world.schema_version = 6
    return copy.deepcopy(receipt)


def _validate(world, previous=None):
    diplomacy = getattr(world, "diplomacy_state", None)
    intelligence = getattr(world, "intelligence_state", None)
    old = getattr(previous, "diplomacy_state", None) if previous is not None else None
    if diplomacy is None and intelligence is None:
        if world.schema_version == 6 or old is not None:
            raise ValueError("D&I authoritative state cannot be missing or removed")
        return []
    _profile(world, 6)
    for state, keys, workload, id_keys in (
            (diplomacy, _DIPLOMACY_KEYS, ("proposals", "dispatches"), {"proposal", "dispatch", "receipt", "sequence"}),
            (intelligence, _INTELLIGENCE_KEYS, ("observations", "dispatches"), {"observation", "dispatch", "receipt", "sequence"})):
        _object(state, keys, "D&I state")
        if type(state["version"]) is not int or state["version"] != STATE_VERSION:
            raise ValueError("Unsupported D&I state version")
        for field in workload:
            if not isinstance(state[field], dict) or state[field]:
                raise ValueError("D&I Stage 1 does not support nonempty " + field)
        if not isinstance(state["outbox"], list) or state["outbox"]:
            raise ValueError("D&I Stage 1 does not support outbox workloads")
        _object(state["next_ids"], id_keys, "D&I next_ids")
        if any(type(value) is not int or value < 1 for value in state["next_ids"].values()):
            raise ValueError("D&I next_ids must be positive integers")
    if diplomacy["next_ids"]["receipt"] < 2:
        raise ValueError("D&I activation has already consumed receipt 1")
    _object(diplomacy["receipts"], {ACTIVATION_ID}, "D&I activation receipts")
    _object(intelligence["receipts"], set(), "D&I intelligence receipts")
    receipt = diplomacy["receipts"][ACTIVATION_ID]
    validate_activation_receipt(receipt)
    if canonical_date(receipt["date"]) > canonical_date(world.date):
        raise ValueError("D&I activation receipt cannot be in the future")
    current_config = {"diplomacy": diplomacy["config"], "intelligence": intelligence["config"]}
    normalized = normalize_config(world, current_config)
    if _json(normalized) != _json(current_config) or _json(receipt["config"]) != _json(current_config):
        raise ValueError("D&I state configuration differs from its immutable activation receipt")
    if old is not None:
        old_receipt = old["receipts"][ACTIVATION_ID]
        if _json(old_receipt) != _json(receipt):
            raise ValueError("D&I immutable activation receipt changed")
        for field, state in (("diplomacy_state", diplomacy), ("intelligence_state", intelligence)):
            old_ids = getattr(previous, field)["next_ids"]
            if any(value < old_ids[key] for key, value in state["next_ids"].items()):
                raise ValueError("D&I next_ids cannot move backwards")
    return []


def validate(world, previous=None):
    """Fail closed on unsupported Stage 1 state and malformed saved settings."""
    try:
        return _validate(world, previous)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        return ["diplomacy/intelligence: " + str(exc)]


def audit(world, previous=None):
    return validate(world, previous)


def tick_intelligence(world, tick_event, rng):
    """Reserved scheduler slot; Stage 1 does not ingest or deliver information."""
    return []


def tick_diplomacy(world, tick_event, rng):
    """Reserved scheduler slot; Stage 1 does not submit or judge contacts."""
    return []
