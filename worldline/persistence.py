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
        intelligence_events = []
        diplomacy_events = []
        for index, event in enumerate(events, 1):
            day = canonical_date(event["day"])
            if (type(event["id"]) is not int or event["id"] != index or day > canonical_date(world.date)
                or previous_day is not None and day < previous_day or not isinstance(event["kind"], str)
                or not isinstance(event["payload"], dict)):
                raise ValueError("Invalid ledger sequence/date/schema")
            previous_day = day
            if event['kind'] == 'diplomacy_intelligence_enabled':
                diplomacy_activations.append((event['day'], event['payload']))
            if event['kind'] == 'system_event' and event['payload'].get('system') == 'intelligence':
                intelligence_events.append((event['day'], event['payload']))
            if event['kind'] == 'system_event' and event['payload'].get('system') == 'diplomacy':
                diplomacy_events.append((event['day'], event['payload']))
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
        validate_diplomacy_intelligence_ledger(world, diplomacy_activations, intelligence_events, diplomacy_events)
        if world.diplomacy_state is not None and 'state_archive' not in payload:
            raise ValueError('Diplomacy & Intelligence save requires its activation archive')
        if "state_archive" in payload:
            from .state_storage import validate_archive
            validate_archive(payload["state_archive"])
            validate_diplomacy_intelligence_archive(world, payload['state_archive'], diplomacy_events)
        return payload
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed snapshot") from exc


def validate_diplomacy_intelligence_ledger(world, activations, intelligence_events=None, diplomacy_events=None):
    """One explicit activation must match its permanent domain receipt."""
    if world.diplomacy_state is None:
        if activations or intelligence_events or diplomacy_events:
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
    _validate_intelligence_ledger(world, intelligence_events or [])
    _validate_diplomacy_ledger(world, diplomacy_events or [])


def _intelligence_queued_snapshot(dispatch):
    queued = copy.deepcopy(dispatch)
    queued.update(status='queued', received_at=None, delivery_receipt_id=None,
                  application_receipt_id=None, belief_accepted=False)
    return queued


def _intelligence_payload(observation):
    return {('observation_id' if key == 'id' else key): copy.deepcopy(value)
            for key, value in observation.items() if key != 'audit'}


def _validate_intelligence_ledger(world, events):
    """Replay only immutable delivery evidence, never objective source truth."""
    from .intelligence import (validate_observation_record, validate_dispatch_record,
                               validate_receipt_record, same_json)
    observations, dispatches, receipts, applications, latest = {}, {}, {}, {}, {}
    sequences = set()
    for entry in events:
        if isinstance(entry, tuple) and len(entry) == 2:
            ledger_day, event = entry
            canonical_date(ledger_day)
        else:
            ledger_day, event = None, entry
        if not isinstance(event, dict) or event.get('system') != 'intelligence':
            raise ValueError('Invalid intelligence immutable ledger event')
        kind = event.get('kind')
        if kind == 'observation_ingested':
            if set(event) != {'system', 'kind', 'observation'}:
                raise ValueError('Invalid intelligence ingestion ledger schema')
            observation = event['observation']
            validate_observation_record(observation)
            key = observation['id']
            if key in observations or ledger_day is not None and observation['collected_at'] > ledger_day:
                raise ValueError('Repeated or future intelligence ingestion ledger record')
            observations[key] = observation
        elif kind == 'report_queued':
            if set(event) != {'system', 'kind', 'dispatch'}:
                raise ValueError('Invalid intelligence dispatch ledger schema')
            dispatch = event['dispatch']
            validate_dispatch_record(dispatch)
            key = dispatch['id']
            if (dispatch['status'] != 'queued' or key in dispatches
                    or dispatch['sequence'] in sequences
                    or dispatch['observation_id'] not in observations
                    or ledger_day is not None and dispatch['sent_at'] != ledger_day):
                raise ValueError('Repeated or invalid intelligence dispatch ledger record')
            if not same_json(dispatch['payload'], _intelligence_payload(observations[dispatch['observation_id']])):
                raise ValueError('Intelligence dispatch differs from frozen observation')
            dispatches[key] = dispatch
            sequences.add(dispatch['sequence'])
        elif kind == 'report_delivered':
            if set(event) != {'system', 'kind', 'dispatch', 'delivery_receipt', 'application_receipt'}:
                raise ValueError('Invalid intelligence delivery ledger schema')
            dispatch, delivery, application = (event[name] for name in
                                                ('dispatch', 'delivery_receipt', 'application_receipt'))
            validate_dispatch_record(dispatch)
            validate_receipt_record(delivery)
            key = dispatch['id']
            old = dispatches.get(key)
            if (old is None or old['status'] != 'queued' or dispatch['status'] != 'delivered'
                    or not same_json(old, _intelligence_queued_snapshot(dispatch))
                    or ledger_day is not None and dispatch['received_at'] != ledger_day):
                raise ValueError('Intelligence delivery conflicts with queued immutable ledger record')
            recipient = dispatch['recipient']
            application_key = recipient['kind'], recipient['id'], dispatch['observation_id']
            latest_key = recipient['kind'], recipient['id'], dispatch['payload']['subject'], dispatch['payload']['topic']
            order = dispatch['payload']['as_of'], dispatch['sent_at'], dispatch['sequence'], key
            if application_key in applications:
                if application is not None or dispatch['belief_accepted'] is not False:
                    raise ValueError('Intelligence perception application repeated in immutable ledger')
                application = applications[application_key]
            else:
                validate_receipt_record(application)
                accepted = latest_key not in latest or order > latest[latest_key]
                if (application['kind'] != 'application' or application['id'] in receipts
                        or application['dispatch_id'] != key
                        or application['observation_id'] != dispatch['observation_id']
                        or not same_json(application['recipient'], recipient)
                        or application['date'] != dispatch['received_at']
                        or application['applied'] is not accepted
                        or application['reason'] != ('applied' if accepted else 'stale')
                        or dispatch['belief_accepted'] is not accepted):
                    raise ValueError('Intelligence perception receipt conflicts with immutable ledger freshness')
                applications[application_key] = application
                receipts[application['id']] = application
                if accepted:
                    latest[latest_key] = order
            if (delivery['kind'] != 'delivery' or delivery['id'] in receipts
                    or delivery['dispatch_id'] != key
                    or delivery['observation_id'] != dispatch['observation_id']
                    or not same_json(delivery['recipient'], recipient)
                    or delivery['date'] != dispatch['received_at']
                    or delivery['id'] != dispatch['delivery_receipt_id']
                    or application['id'] != dispatch['application_receipt_id']
                    or delivery['application_receipt_id'] != application['id']
                    or delivery['belief_accepted'] is not dispatch['belief_accepted']):
                raise ValueError('Intelligence delivery receipt conflicts with immutable ledger')
            receipts[delivery['id']] = delivery
            dispatches[key] = dispatch
        else:
            raise ValueError('Unsupported intelligence immutable ledger event')
    state = world.intelligence_state
    for bucket, recorded in (('observations', observations), ('dispatches', dispatches), ('receipts', receipts)):
        if not same_json(state[bucket], recorded):
            raise ValueError('Intelligence ' + bucket + ' differ from immutable ledger')


def _validate_diplomacy_ledger(world, events):
    """Replay contact obligations and terminal transitions in the one Ledger."""
    from .diplomacy_intelligence import ACTIVATION_ID
    from .diplomacy_validation import (validate_proposal_record, validate_dispatch_record,
        validate_receipt_record, validate_trace_records, proposal_payload, response_payload, queued_projection)
    from .intelligence import same_json
    proposals, dispatches = {}, {}
    receipts = {ACTIVATION_ID: world.diplomacy_state['receipts'][ACTIVATION_ID]}
    sequences, requests, applications, traces = set(), set(), {}, {}

    def add_request(receipt, kind, day):
        validate_receipt_record(receipt)
        key = receipt['principal_ref']['kind'], receipt['principal_ref']['id'], receipt['request_id']
        if (receipt['kind'] != kind or receipt['id'] in receipts or key in requests
                or day is not None and receipt['date'] != day):
            raise ValueError('Repeated or invalid diplomacy execution receipt in immutable ledger')
        requests.add(key)
        receipts[receipt['id']] = receipt

    def add_dispatches(values, proposal, receipt, kind, day):
        if not isinstance(values, list) or [value.get('id') for value in values if isinstance(value, dict)] != receipt['dispatch_ids']:
            raise ValueError('Diplomacy immutable ledger omits its promised dispatches')
        expected = proposal_payload(proposal) if kind == 'proposal' else response_payload(proposal, receipt)
        for dispatch in values:
            validate_dispatch_record(dispatch)
            if (dispatch['status'] != 'queued' or dispatch['id'] in dispatches
                    or dispatch['sequence'] in sequences or dispatch['proposal_id'] != proposal['id']
                    or dispatch['message_kind'] != kind or not same_json(dispatch['payload'], expected)
                    or dispatch['sent_at'] != receipt['date'] or day is not None and dispatch['sent_at'] != day):
                raise ValueError('Repeated or invalid queued diplomacy immutable ledger record')
            dispatches[dispatch['id']] = dispatch
            sequences.add(dispatch['sequence'])

    for entry in events:
        if isinstance(entry, tuple) and len(entry) == 2:
            day, event = entry
            canonical_date(day)
        else:
            day, event = None, entry
        if not isinstance(event, dict) or event.get('system') != 'diplomacy':
            raise ValueError('Invalid diplomacy immutable ledger event')
        kind = event.get('kind')
        if kind == 'proposal_submitted':
            if set(event) != {'system', 'kind', 'proposal', 'submission_receipt', 'dispatches'}:
                raise ValueError('Invalid diplomacy submission ledger schema')
            proposal, submission = event['proposal'], event['submission_receipt']
            validate_proposal_record(proposal)
            if (proposal['id'] in proposals or proposal['status'] != 'pending'
                    or proposal['submission_receipt_id'] != submission['id']
                    or proposal['body']['created_at'] != submission['date']):
                raise ValueError('Repeated or invalid proposal creation in immutable ledger')
            add_request(submission, 'submission', day)
            add_dispatches(event['dispatches'], proposal, submission, 'proposal', day)
            proposals[proposal['id']] = proposal
        elif kind == 'contact_responded':
            if set(event) != {'system', 'kind', 'proposal_id', 'response_receipt', 'dispatches', 'decisions', 'action'}:
                raise ValueError('Invalid diplomacy response ledger schema')
            proposal = proposals.get(event['proposal_id'])
            response = event['response_receipt']
            validate_receipt_record(response)
            if (proposal is None or proposal['status'] != 'pending' or response['kind'] != 'response'
                    or response['proposal_id'] != proposal['id']):
                raise ValueError('Diplomacy immutable ledger contains a second or orphan terminal response')
            proof = receipts.get(response['proposal_delivery_receipt_id'])
            if (not proof or proof['kind'] != 'delivery' or proof['message_kind'] != 'proposal'
                    or proof['proposal_id'] != proposal['id'] or proof['date'] > response['date']
                    or proof['recipient'] != {'kind': 'person', 'id': response['executing_person_id']}):
                raise ValueError('Diplomacy immutable ledger responds without executor delivery evidence')
            add_request(response, 'response', day)
            validate_trace_records(proposal, response, event['decisions'], event['action'], receipts)
            for trace_kind, values in (('decision', event['decisions']),
                                        ('action', [] if event['action'] is None else [event['action']])):
                for trace in values:
                    key = trace_kind, trace['id']
                    if key in traces:
                        raise ValueError('Repeated diplomacy NPC trace in immutable ledger')
                    traces[key] = trace
            terminal = copy.deepcopy(proposal)
            terminal.update(status=response['result'], response_receipt_id=response['id'])
            proposals[proposal['id']] = terminal
            add_dispatches(event['dispatches'], terminal, response, 'response', day)
        elif kind == 'dispatch_delivered':
            if set(event) != {'system', 'kind', 'dispatch', 'delivery_receipt', 'application_receipt'}:
                raise ValueError('Invalid diplomacy delivery ledger schema')
            dispatch, delivery, application = (event[key] for key in ('dispatch', 'delivery_receipt', 'application_receipt'))
            validate_dispatch_record(dispatch)
            validate_receipt_record(delivery)
            old = dispatches.get(dispatch['id'])
            if (old is None or old['status'] != 'queued' or dispatch['status'] != 'delivered'
                    or not same_json(old, queued_projection(dispatch))
                    or day is not None and dispatch['received_at'] != day):
                raise ValueError('Diplomacy delivery conflicts with its queued immutable ledger record')
            due = [value for value in dispatches.values()
                   if value['status'] == 'queued' and value['delivery_date'] <= dispatch['received_at']]
            if not due or min(due, key=lambda value: (value['delivery_date'], value['sequence'], value['id']))['id'] != dispatch['id']:
                raise ValueError('Diplomacy immutable ledger delivery order differs from deterministic queue')
            recipient = dispatch['recipient']
            key = recipient['kind'], recipient['id'], dispatch['proposal_id'], dispatch['message_kind']
            if key in applications:
                if application is not None or dispatch['belief_accepted'] is not False:
                    raise ValueError('Repeated diplomacy perception application in immutable ledger')
                application = applications[key]
            else:
                validate_receipt_record(application)
                if (application['kind'] != 'application' or application['id'] in receipts
                        or application['dispatch_id'] != dispatch['id'] or application['proposal_id'] != dispatch['proposal_id']
                        or application['message_kind'] != dispatch['message_kind']
                        or not same_json(application['recipient'], recipient)
                        or application['date'] != dispatch['received_at'] or application['applied'] is not True
                        or dispatch['belief_accepted'] is not True):
                    raise ValueError('Invalid first diplomacy perception receipt in immutable ledger')
                receipts[application['id']] = application
                applications[key] = application
            if (delivery['kind'] != 'delivery' or delivery['id'] in receipts
                    or delivery['dispatch_id'] != dispatch['id'] or delivery['proposal_id'] != dispatch['proposal_id']
                    or delivery['message_kind'] != dispatch['message_kind'] or not same_json(delivery['recipient'], recipient)
                    or delivery['date'] != dispatch['received_at'] or delivery['id'] != dispatch['delivery_receipt_id']
                    or application['id'] != dispatch['application_receipt_id']
                    or delivery['application_receipt_id'] != application['id']
                    or delivery['belief_accepted'] is not dispatch['belief_accepted']):
                raise ValueError('Invalid diplomacy delivery receipt in immutable ledger')
            receipts[delivery['id']] = delivery
            dispatches[dispatch['id']] = dispatch
        else:
            raise ValueError('Unsupported diplomacy immutable ledger event')
    state = world.diplomacy_state
    for bucket, recorded in (('proposals', proposals), ('dispatches', dispatches), ('receipts', receipts)):
        if not same_json(state[bucket], recorded):
            raise ValueError('Diplomacy ' + bucket + ' differ from immutable ledger')
    for trace_kind, bucket in (('decision', 'decisions'), ('action', 'actions')):
        for current in world.npc_state[bucket]:
            recorded = traces.get((trace_kind, current.get('id')))
            if recorded is not None and not same_json(current, recorded):
                raise ValueError('Diplomacy NPC trace differs from immutable ledger')


def validate_diplomacy_intelligence_archive(world, archive, diplomacy_events=None):
    """All Stage 2 records retain complete immutable archival evidence."""
    from .state_storage import DIPLOMACY_INTELLIGENCE_ARCHIVE_KINDS
    rows = [row for row in archive['catalog']
            if row['kind'] in DIPLOMACY_INTELLIGENCE_ARCHIVE_KINDS]
    if world.diplomacy_state is None:
        if rows:
            raise ValueError('Diplomacy & Intelligence archive requires activated state')
        return
    from .diplomacy_intelligence import ACTIVATION_ID
    from .intelligence import same_json
    receipt = world.diplomacy_state['receipts'][ACTIVATION_ID]
    activation_rows = [row for row in rows if row['kind'] == 'diplomacy_receipts' and row['item'] == ACTIVATION_ID]
    if (len(activation_rows) != 1 or not same_json(
            archive['objects'][activation_rows[0]['digest']], receipt)):
        raise ValueError('Diplomacy & Intelligence archive differs from its immutable activation receipt')
    state = world.intelligence_state
    for bucket in ('observations', 'dispatches', 'receipts'):
        kind = 'intelligence_' + bucket
        records = state[bucket]
        matching = [row for row in rows if row['kind'] == kind]
        if {row['item'] for row in matching} != set(records):
            raise ValueError('Intelligence archive identity differs from ' + bucket)
        grouped = {}
        for row in matching:
            grouped.setdefault(row['item'], []).append(archive['objects'][row['digest']])
        for key, current in records.items():
            versions = grouped[key]
            expected = [current]
            if bucket == 'dispatches' and current['status'] == 'delivered':
                expected.append(_intelligence_queued_snapshot(current))
            if (len(versions) != len(expected)
                    or any(not any(same_json(body, saved) for saved in versions) for body in expected)):
                raise ValueError('Intelligence archive differs from complete ' + bucket + ' history')
    from .diplomacy_validation import pending_projection, queued_projection
    state = world.diplomacy_state
    for bucket in ('proposals', 'dispatches', 'receipts'):
        kind, records = 'diplomacy_' + bucket, state[bucket]
        matching = [row for row in rows if row['kind'] == kind]
        if {row['item'] for row in matching} != set(records):
            raise ValueError('Diplomacy archive identity differs from ' + bucket)
        grouped = {}
        for row in matching:
            grouped.setdefault(row['item'], []).append(archive['objects'][row['digest']])
        for identity, current in records.items():
            versions, expected = grouped[identity], [current]
            if bucket == 'proposals' and current['status'] != 'pending':
                expected.append(pending_projection(current))
            if bucket == 'dispatches' and current['status'] == 'delivered':
                expected.append(queued_projection(current))
            if (len(versions) != len(expected)
                    or any(not any(same_json(body, saved) for saved in versions) for body in expected)):
                raise ValueError('Diplomacy archive differs from complete ' + bucket + ' history')
    _validate_diplomacy_trace_archive(world, archive, diplomacy_events or [])


def _validate_diplomacy_trace_archive(world, archive, events):
    from .intelligence import same_json
    expected = {}
    for entry in events:
        event = entry[1] if isinstance(entry, tuple) else entry
        if event.get('kind') != 'contact_responded':
            continue
        for kind, values in (('decision', event['decisions']),
                             ('action', [] if event['action'] is None else [event['action']])):
            for trace in values:
                expected[kind, trace['id']] = trace
    promised = set()
    for receipt in world.diplomacy_state['receipts'].values():
        if receipt['kind'] == 'response':
            promised.update(('decision', identity) for identity in receipt['decision_ids'])
            if receipt['action_id'] is not None:
                promised.add(('action', receipt['action_id']))
    if events and promised != set(expected):
        raise ValueError('Diplomacy archived trace promises differ from immutable ledger')
    for kind, identity in promised:
        rows = [row for row in archive['catalog'] if row['kind'] == kind and row['item'] == identity]
        if len(rows) != 1:
            raise ValueError('Diplomacy response requires one complete NPC trace archive')
        row, body = rows[0], archive['objects'][rows[0]['digest']]
        if (row['owner'] != body.get('actor') or row['day'] != body.get('date') or row['country'] != body.get('country')
                or body.get('id') != identity or (kind, identity) in expected and not same_json(body, expected[kind, identity])):
            raise ValueError('Diplomacy NPC trace archive conflicts with immutable ledger identity/body')


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
