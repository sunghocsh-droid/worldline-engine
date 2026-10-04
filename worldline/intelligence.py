"""Trusted, deterministic delivery of frozen observations, never source truth.

The host supplies an allowed JSON snapshot. This module does not collect facts,
adjudicate accuracy, or automatically relay Crisis/Military reports.
"""
import copy
import json
import math
from datetime import timedelta

from .models import canonical_date
from . import runtime_queries

OBSERVATION_KEYS = {"id", "source_ref", "subject", "topic", "collector_ref", "as_of",
                    "collected_at", "observed_data", "provenance", "audit"}
PAYLOAD_KEYS = (OBSERVATION_KEYS - {"id", "audit"}) | {"observation_id"}
DISPATCH_KEYS = {"id", "observation_id", "recipient", "payload", "sequence", "sent_at",
                 "delivery_date", "status", "received_at", "delivery_receipt_id",
                 "application_receipt_id", "belief_accepted"}
DELIVERY_KEYS = {"id", "kind", "dispatch_id", "observation_id", "recipient", "date",
                 "application_receipt_id", "belief_accepted"}
APPLICATION_KEYS = {"id", "kind", "dispatch_id", "observation_id", "recipient", "date",
                    "applied", "reason"}


def same_json(left, right):
    """JSON intent equality preserves booleans, numeric types and signed zero."""
    return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Invalid intelligence " + label + " keys")


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Intelligence identities must be nonempty bounded strings")
    return value


def freeze(value, limit=65536):
    """Reject non-JSON inputs rather than silently coerce keys or Python objects."""
    def check(item, depth=0):
        if depth > 16:
            raise ValueError("Intelligence JSON nesting exceeds limit")
        if item is None or type(item) in (bool, str, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, dict) and all(type(k) is str for k in item):
            for child in item.values():
                check(child, depth + 1)
            return
        if isinstance(item, list):
            for child in item:
                check(child, depth + 1)
            return
        raise ValueError("Intelligence data must be JSON-safe")
    check(value)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("Intelligence JSON exceeds size limit")
    return json.loads(encoded)


def source_reference(reference):
    _object(reference, {"domain", "id"}, "source reference")
    _id(reference["domain"])
    _id(reference["id"])
    return freeze(reference, 2048)


def _typed(reference):
    _object(reference, {"kind", "id"}, "recipient")
    if reference["kind"] not in ("person", "player"):
        raise ValueError("Intelligence supports explicit person/player recipients only")
    _id(reference["id"])


def _quality(value):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError("Invalid intelligence provenance quality")


def validate_observation_record(record):
    _object(record, OBSERVATION_KEYS, "observation")
    for field in ("id", "subject", "topic"):
        _id(record[field])
    source_reference(record["source_ref"])
    source_reference(record["collector_ref"])
    if canonical_date(record["as_of"]) > canonical_date(record["collected_at"]):
        raise ValueError("Intelligence as_of must precede collection")
    if not isinstance(record["observed_data"], dict):
        raise ValueError("Intelligence observed_data must be an object")
    freeze(record["observed_data"])
    provenance = record["provenance"]
    _object(provenance, {"label", "confidence", "reliability"}, "provenance")
    _id(provenance["label"])
    _quality(provenance["confidence"])
    _quality(provenance["reliability"])
    audit = record["audit"]
    if not isinstance(audit, dict) or set(audit) - {"accuracy", "notes"}:
        raise ValueError("Unknown intelligence audit metadata")
    if "accuracy" in audit:
        _quality(audit["accuracy"])
    if "notes" in audit and (not isinstance(audit["notes"], str) or len(audit["notes"]) > 2048):
        raise ValueError("Invalid intelligence audit notes")
    freeze(audit, 4096)


def allowed_payload(observation):
    return {"observation_id": observation["id"], **{
        key: copy.deepcopy(value) for key, value in observation.items() if key not in ("id", "audit")}}


def validate_dispatch_record(record):
    _object(record, DISPATCH_KEYS, "dispatch")
    for field in ("id", "observation_id"):
        _id(record[field])
    _typed(record["recipient"])
    payload = record["payload"]
    _object(payload, PAYLOAD_KEYS, "payload")
    validate_observation_record({"id": payload["observation_id"], "audit": {}, **{
        k: v for k, v in payload.items() if k != "observation_id"}})
    if record["observation_id"] != payload["observation_id"]:
        raise ValueError("Intelligence dispatch observation identity mismatch")
    if type(record["sequence"]) is not int or record["sequence"] < 1:
        raise ValueError("Invalid intelligence dispatch sequence")
    dates = [canonical_date(payload["collected_at"]), canonical_date(record["sent_at"]),
             canonical_date(record["delivery_date"])]
    if dates != sorted(dates):
        raise ValueError("Invalid intelligence collection/send/delivery order")
    if type(record["belief_accepted"]) is not bool:
        raise ValueError("Invalid intelligence application flag")
    if record["status"] == "queued":
        if any(record[k] is not None for k in ("received_at", "delivery_receipt_id", "application_receipt_id")) or record["belief_accepted"]:
            raise ValueError("Queued intelligence cannot have receipt or belief effects")
    elif record["status"] == "delivered":
        if canonical_date(record["received_at"]) < dates[-1]:
            raise ValueError("Intelligence received before delivery date")
        _id(record["delivery_receipt_id"])
        _id(record["application_receipt_id"])
    else:
        raise ValueError("Invalid intelligence dispatch status")


def validate_receipt_record(record):
    kind = record.get("kind") if isinstance(record, dict) else None
    _object(record, DELIVERY_KEYS if kind == "delivery" else APPLICATION_KEYS, "receipt")
    if kind not in ("delivery", "application"):
        raise ValueError("Invalid intelligence receipt kind")
    for field in ("id", "dispatch_id", "observation_id"):
        _id(record[field])
    _typed(record["recipient"])
    canonical_date(record["date"])
    if kind == "delivery":
        _id(record["application_receipt_id"])
        if type(record["belief_accepted"]) is not bool:
            raise ValueError("Invalid intelligence receipt application flag")
    elif type(record["applied"]) is not bool or record["reason"] != ("applied" if record["applied"] else "stale"):
        raise ValueError("Invalid intelligence perception receipt")


def _state(world):
    if world.intelligence_state is None:
        raise ValueError("Intelligence delivery requires explicit D&I activation")
    return world.intelligence_state


def _recipient(world, reference):
    from .diplomacy_intelligence import _recipient as validate_recipient
    _typed(reference)
    validate_recipient(world, reference, world.diplomacy_state["config"]["player_bindings"])


def _source(state, reference):
    source_reference(reference)
    sources = state["config"]["sources"]
    if sources and reference not in sources.values():
        raise ValueError("Intelligence source is outside configured sources")


def _allocate(state, kind):
    sequence = state["next_ids"][kind]
    state["next_ids"][kind] += 1
    return "intelligence-" + kind + "-" + str(sequence)


def _available_identity(state, kind, records):
    sequence = state["next_ids"][kind]
    while "intelligence-" + kind + "-" + str(sequence) in records:
        sequence += 1
    return "intelligence-" + kind + "-" + str(sequence), sequence


def ingest(world, source_ref, observed_data, *, subject, topic, as_of, collected_at=None,
           collector_ref=None, provenance=None, audit=None, observation_id=None):
    state = _state(world)
    _source(state, source_ref)
    if observation_id is not None:
        _id(observation_id)
    generated_id, sequence = _available_identity(state, "observation", state["observations"])
    old = state["observations"].get(observation_id) if observation_id is not None else None
    record = {"id": generated_id if observation_id is None else observation_id,
              "source_ref": source_reference(source_ref), "subject": subject, "topic": topic,
              "collector_ref": source_reference(source_ref if collector_ref is None else collector_ref),
              "as_of": as_of, "collected_at": collected_at if collected_at is not None else (old["collected_at"] if old else world.date),
              "observed_data": freeze(observed_data),
              "provenance": freeze(provenance if provenance is not None else {
                  "label": source_ref["id"], "confidence": .8, "reliability": .8}, 4096),
              "audit": freeze({} if audit is None else audit, 4096)}
    validate_observation_record(record)
    if record["collected_at"] > world.date:
        raise ValueError("Future intelligence observations are not allowed")
    if old is not None:
        if not same_json(old, record):
            raise ValueError("Intelligence observation ID conflict: different intent")
        return copy.deepcopy(old), []
    state["next_ids"]["observation"] = (sequence if observation_id is None else state["next_ids"]["observation"]) + 1
    state["observations"][record["id"]] = record
    return copy.deepcopy(record), [{"system": "intelligence", "kind": "observation_ingested", "observation": copy.deepcopy(record)}]


def queued_projection(dispatch):
    result = copy.deepcopy(dispatch)
    result.update(status="queued", received_at=None, delivery_receipt_id=None,
                  application_receipt_id=None, belief_accepted=False)
    return result


def queue(world, observation_id, recipient_ref, *, message_id=None, sent_at=None):
    state = _state(world)
    _id(observation_id)
    if message_id is not None:
        _id(message_id)
    _recipient(world, recipient_ref)
    audience = state["config"]["audience"]
    if audience and recipient_ref not in audience:
        raise ValueError("Intelligence recipient is outside configured audience")
    if observation_id not in state["observations"]:
        raise ValueError("Unknown intelligence observation")
    old = state["dispatches"].get(message_id) if message_id is not None else None
    generated_id, sequence = _available_identity(state, "dispatch", state["dispatches"])
    sent_at = sent_at if sent_at is not None else (old["sent_at"] if old else world.date)
    canonical_date(sent_at)
    if old is None and sent_at != world.date:
        raise ValueError("Intelligence dispatch must use the current simulation date")
    record = {"id": generated_id if message_id is None else message_id,
              "observation_id": observation_id, "recipient": freeze(recipient_ref, 2048),
              "payload": allowed_payload(state["observations"][observation_id]),
              "sequence": old["sequence"] if old else state["next_ids"]["sequence"],
              "sent_at": sent_at, "delivery_date": (canonical_date(sent_at) + timedelta(days=state["config"]["delay_days"])).isoformat(),
              "status": "queued", "received_at": None, "delivery_receipt_id": None,
              "application_receipt_id": None, "belief_accepted": False}
    validate_dispatch_record(record)
    if old is not None:
        if not same_json(queued_projection(old), record):
            raise ValueError("Intelligence dispatch ID conflict: different intent")
        return record, []
    state["next_ids"]["dispatch"] = (sequence if message_id is None else state["next_ids"]["dispatch"]) + 1
    state["next_ids"]["sequence"] += 1
    state["dispatches"][record["id"]] = record
    state["outbox"].append(record["id"])
    return copy.deepcopy(record), [{"system": "intelligence", "kind": "report_queued", "dispatch": copy.deepcopy(record)}]


def delivery_order(dispatch):
    return dispatch["delivery_date"], dispatch["sequence"], dispatch["id"]


def freshness(dispatch):
    return dispatch["payload"]["as_of"], dispatch["sent_at"], dispatch["sequence"], dispatch["id"]


def recipient_key(dispatch):
    return dispatch["recipient"]["kind"], dispatch["recipient"]["id"]


def subject_key(dispatch):
    return (*recipient_key(dispatch), dispatch["payload"]["subject"], dispatch["payload"]["topic"])


def due(world):
    state = _state(world)
    return [dispatch for dispatch in runtime_queries.ordered_values(
        state["dispatches"], "intelligence.queued", lambda d: d["status"] == "queued", delivery_order)
        if dispatch["delivery_date"] <= world.date]


def deliver_due(world):
    from .intelligence_perception import apply_person
    state = _state(world)
    events = []
    for dispatch in due(world):
        _recipient(world, dispatch["recipient"])
        key = subject_key(dispatch)
        latest = runtime_queries.latest_by_group(state["dispatches"], "intelligence.latest", subject_key,
            freshness, lambda d: d["status"] == "delivered" and d["belief_accepted"], [key]).get(key)
        matching = runtime_queries.grouped_items(state["receipts"], "intelligence.applications",
            lambda r: (r["kind"], r["recipient"]["kind"], r["recipient"]["id"], r["observation_id"]),
            ("application", *recipient_key(dispatch), dispatch["observation_id"]))
        application = matching[0][1] if matching else None
        accepted = application is None and (latest is None or freshness(dispatch) > freshness(latest))
        new_application = None
        if application is None:
            application = {"id": _allocate(state, "receipt"), "kind": "application",
                "dispatch_id": dispatch["id"], "observation_id": dispatch["observation_id"],
                "recipient": copy.deepcopy(dispatch["recipient"]), "date": world.date,
                "applied": accepted, "reason": "applied" if accepted else "stale"}
            state["receipts"][application["id"]] = application
            new_application = copy.deepcopy(application)
        if accepted and dispatch["recipient"]["kind"] == "person":
            apply_person(world, dispatch)
        receipt = {"id": _allocate(state, "receipt"), "kind": "delivery", "dispatch_id": dispatch["id"],
            "observation_id": dispatch["observation_id"], "recipient": copy.deepcopy(dispatch["recipient"]),
            "date": world.date, "application_receipt_id": application["id"], "belief_accepted": accepted}
        state["receipts"][receipt["id"]] = receipt
        dispatch.update(status="delivered", received_at=world.date, delivery_receipt_id=receipt["id"],
                        application_receipt_id=application["id"], belief_accepted=accepted)
        state["outbox"].remove(dispatch["id"])
        events.append({"system": "intelligence", "kind": "report_delivered", "dispatch": copy.deepcopy(dispatch),
                       "delivery_receipt": copy.deepcopy(receipt), "application_receipt": new_application})
    return events


def view(world, reference, subject_or_topic=None):
    state = _state(world)
    _recipient(world, reference)
    if subject_or_topic is not None:
        _id(subject_or_topic)
    candidates = runtime_queries.grouped_items(state["dispatches"], "intelligence.recipient", recipient_key,
                                              (reference["kind"], reference["id"]))
    history = [d for _, d in candidates if d["status"] == "delivered" and
               (subject_or_topic is None or subject_or_topic in (d["payload"]["subject"], d["payload"]["topic"]))]
    history.sort(key=delivery_order)
    latest = runtime_queries.latest_by_group(state["dispatches"], "intelligence.latest", subject_key,
        freshness, lambda d: d["status"] == "delivered" and d["belief_accepted"], [subject_key(d) for d in history])
    return copy.deepcopy({"recipient": reference, "history": history,
                          "latest": [latest[key] for key in sorted(latest)]})


def validate_state(world, previous=None):
    state = _state(world)
    observations, dispatches, receipts = (state[field] for field in ("observations", "dispatches", "receipts"))
    if any(not isinstance(value, dict) for value in (observations, dispatches, receipts)):
        raise ValueError("Malformed intelligence record tables")
    for identity, record in observations.items():
        validate_observation_record(record)
        if identity != record["id"] or record["collected_at"] > world.date:
            raise ValueError("Invalid intelligence observation identity/date")
        _source(state, record["source_ref"])
    sequences = set()
    for identity, dispatch in dispatches.items():
        validate_dispatch_record(dispatch)
        _recipient(world, dispatch["recipient"])
        if (identity != dispatch["id"] or dispatch["observation_id"] not in observations
                or not same_json(dispatch["payload"], allowed_payload(observations[dispatch["observation_id"]]))
                or dispatch["sent_at"] > world.date or dispatch["sequence"] in sequences
                or dispatch["sequence"] >= state["next_ids"]["sequence"]):
            raise ValueError("Invalid intelligence frozen dispatch or sequence")
        if state["config"]["audience"] and dispatch["recipient"] not in state["config"]["audience"]:
            raise ValueError("Intelligence dispatch audience mismatch")
        expected_date = (canonical_date(dispatch["sent_at"]) + timedelta(days=state["config"]["delay_days"])).isoformat()
        if dispatch["delivery_date"] != expected_date:
            raise ValueError("Intelligence delivery differs from configured delay")
        sequences.add(dispatch["sequence"])
        if dispatch["status"] == "delivered":
            delivery = receipts.get(dispatch["delivery_receipt_id"])
            application = receipts.get(dispatch["application_receipt_id"])
            if not delivery or not application or delivery["kind"] != "delivery" or application["kind"] != "application":
                raise ValueError("Missing intelligence delivery/application receipt")
            for receipt in (delivery, application):
                if receipt["observation_id"] != dispatch["observation_id"] or receipt["recipient"] != dispatch["recipient"]:
                    raise ValueError("Intelligence receipt recipient/observation mismatch")
            if (delivery["dispatch_id"] != identity or delivery["date"] != dispatch["received_at"]
                    or delivery["application_receipt_id"] != application["id"]
                    or delivery["belief_accepted"] != dispatch["belief_accepted"]
                    or dispatch["received_at"] > world.date or application["date"] > dispatch["received_at"]
                    or dispatch["belief_accepted"] != (application["dispatch_id"] == identity and application["applied"])):
                raise ValueError("Invalid intelligence receipt lifecycle")
    applications = set()
    for identity, receipt in receipts.items():
        validate_receipt_record(receipt)
        dispatch = dispatches.get(receipt["dispatch_id"])
        if identity != receipt["id"] or not dispatch or dispatch["status"] != "delivered":
            raise ValueError("Orphan intelligence receipt")
        if receipt["kind"] == "application":
            key = (*recipient_key(dispatch), receipt["observation_id"])
            if key in applications or dispatch["application_receipt_id"] != identity:
                raise ValueError("Duplicate intelligence perception application")
            applications.add(key)
        elif dispatch["delivery_receipt_id"] != identity:
            raise ValueError("Orphan intelligence delivery receipt")
    if not isinstance(state["outbox"], list) or len(set(state["outbox"])) != len(state["outbox"]) or set(state["outbox"]) != {
            identity for identity, dispatch in dispatches.items() if dispatch["status"] == "queued"}:
        raise ValueError("Intelligence outbox differs from queued dispatches")
    for kind, table in (("observation", observations), ("dispatch", dispatches), ("receipt", receipts)):
        if state["next_ids"][kind] <= len(table):
            raise ValueError("Intelligence ID counter precedes allocated records")
    receipt_prefix = "intelligence-receipt-"
    allocated = [int(identity[len(receipt_prefix):]) for identity in receipts
                 if identity.startswith(receipt_prefix) and identity[len(receipt_prefix):].isascii()
                 and identity[len(receipt_prefix):].isdigit()]
    if allocated and state["next_ids"]["receipt"] <= max(allocated):
        raise ValueError("Intelligence receipt counter precedes allocated identity")
    old = previous.intelligence_state if previous is not None else None
    if old is not None:
        for field in ("observations", "receipts", "dispatches"):
            for identity, record in old[field].items():
                new = state[field].get(identity)
                if new is None or (field != "dispatches" and not same_json(new, record)):
                    raise ValueError("Immutable intelligence record changed or removed")
                if field == "dispatches" and (not same_json(queued_projection(new), queued_projection(record))
                        or record["status"] == "delivered" and not same_json(new, record)):
                    raise ValueError("Immutable intelligence dispatch changed")
