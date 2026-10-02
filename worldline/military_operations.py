from .incremental_audit import items as audit_items, json_check as audit_json
"""Strategic military operations, communicated orders and local decisions.

This module schedules real formations. It neither invents supplies/personnel nor
applies casualties: actual adjudication and the idempotent military bridge own
those accounts. Political deliberation sees only an NPC's received information.
"""
import copy
import math
from datetime import timedelta

from .models import canonical_date
from . import npc_decision, npc_state
from .military_active import active_operations, command_group, queued_commands, latest_by_group

VERSION = 1
MOVEMENT = {"move", "reinforce", "withdraw", "raid", "attack", "siege", "naval_attack", "amphibious", "blockade", "escort", "naval_patrol"}
OFFENSIVE = {"raid", "attack", "siege", "naval_attack", "amphibious"}
NAVAL = {"naval_attack", "amphibious", "blockade", "escort", "naval_patrol"}
KINDS = MOVEMENT | {"defend", "hold", "reserve"}
ACTIVE = {"planned", "issued", "received", "in_progress", "paused", "disrupted"}
STATUSES = ACTIVE | {"completed", "cancelled"}
INTENTS = {"execute", "hold", "cancel", "withdraw", "avoid_conflict", "hold_fire", "continue"}
ALIASES = {"offensive": "attack", "defensive": "defend", "withdrawal": "withdraw",
    "pursuit": "raid", "relief": "reinforce", "amphibious_operation": "amphibious",
    "naval_interception": "naval_attack", "convoy_escort": "escort"}


def _state(world):
    from .military_state import state
    return state(world)


def _emit(world, kind, **fields):
    from .military_state import emit
    emit(world, kind, **fields)


def _number(value, default=0):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else default


def _clip(value):
    return min(1., max(0., _number(value)))


def _after(day, days):
    return (canonical_date(day) + timedelta(days=days)).isoformat()


def _operation(world, operation_id):
    state = _state(world)
    if operation_id not in state["operations"]:
        raise ValueError("Unknown military operation")
    return state["operations"][operation_id]


def _scope(kind):
    return "naval" if kind in NAVAL else "offensive" if kind in OFFENSIVE else "defensive" if kind in {"defend", "hold", "reserve"} else "movement"


def _sea_formation(formation):
    return formation.get("service_type") in {"navy", "naval"} or formation.get("mobility", {}).get("mode") == "sea" or (
        formation.get("service_type") == "marine" and "ships" in formation.get("equipment_state", {}))


def _mode(formation, kind, operation=None):
    return "sea" if kind in NAVAL or (operation or {}).get("transport_mode") == "sea" or _sea_formation(formation) else "land"


def _transport_capacity(fleet):
    ships = fleet.get("equipment_state", {}).get("ships", {})
    quantity, crew_per_ship = _number(ships.get("quantity")), _number(ships.get("crew_per_ship", 1))
    equipment_capacity = quantity * _number(ships.get("troop_capacity"))
    override = fleet.get("mobility", {}).get("transport_capacity")
    capacity = equipment_capacity if override is None else min(_number(override), equipment_capacity) if "troop_capacity" in ships else _number(override)
    crew_ratio = min(1., fleet["manpower"] / (quantity * crew_per_ship)) if quantity > 0 and crew_per_ship > 0 else 0.
    return capacity * _clip(ships.get("condition", 1)) * crew_ratio if fleet["manpower"] > 0 else 0.


def _covered(state, delegated, formation_id):
    """A headquarters delegation covers descendants, not foreign formations."""
    visited = set()
    while formation_id is not None and formation_id in state["formations"] and formation_id not in visited:
        if formation_id in delegated:
            return True
        visited.add(formation_id)
        formation_id = state["formations"][formation_id].get("parent")
    return False


def authority(world, actor, formations, kind, issuer, target=None):
    """Institutional jurisdiction, separate from financial/physical feasibility."""
    state = _state(world)
    if actor not in state["actors"] or kind not in KINDS or not formations:
        return {"feasible": False, "reason": "unknown actor, operation or formations"}
    if any(f not in state["formations"] or state["formations"][f]["actor"] != actor for f in formations):
        return {"feasible": False, "reason": "formation outside actor jurisdiction"}
    # The actor principal is an explicit trusted interface authorization. It is
    # never inferred from an NPC opinion, or supplied to political deliberation.
    if issuer == actor:
        return {"feasible": True, "scope": "actor_principal", "issuer": issuer}
    npc = getattr(world, "npc_state", None) or {}
    person = npc.get("persons", {}).get(issuer)
    country = state["actors"][actor].get("country")
    government = npc.get("governments", {}).get(country, {})
    if person and person.get("active", True) and person.get("country") == country and issuer == government.get("ruler"):
        return {"feasible": True, "scope": "government_ruler", "issuer": issuer}
    delegation = state["delegations"].get(issuer)
    if not isinstance(delegation, dict) or delegation.get("actor") != actor:
        return {"feasible": False, "reason": "no military delegation"}
    if delegation.get("expires") is not None and delegation["expires"] < world.date:
        return {"feasible": False, "reason": "military delegation expired"}
    if not all(_covered(state, delegation.get("formations", []), f) for f in formations):
        return {"feasible": False, "reason": "delegation does not cover these formations"}
    scope = _scope(kind)
    scopes = delegation.get("scopes", [])
    if scope not in scopes:
        return {"feasible": False, "reason": "operation exceeds delegated scope", "required_scope": scope}
    allowed = delegation.get("regions", [])
    locations = [state["formations"][f]["location"] for f in formations]
    if allowed and any(r not in allowed for r in locations + ([target] if target else [])):
        return {"feasible": False, "reason": "operation exceeds delegated theatre"}
    if issuer in npc.get("persons", {}) and not npc["persons"][issuer].get("active", True):
        return {"feasible": False, "reason": "inactive commander"}
    return {"feasible": True, "scope": scope, "issuer": issuer}


def _recipient_camp(world, actor, formations, target, parameters):
    """An actual own recipient permits the final camp leg, never transit rights."""
    from .military_logistics import is_stationary
    recipient = parameters.get("recipient_formation")
    if recipient is None:
        return {"feasible": False, "reason": "no bound reinforcement recipient"}
    if not isinstance(recipient, str):
        return {"feasible": False, "reason": "reinforcement recipient must be an existing formation ID"}
    camp = _state(world)["formations"].get(recipient)
    if not camp or camp.get("actor") != actor or recipient in formations:
        return {"feasible": False, "reason": "reinforcement recipient is not a distinct own formation"}
    if camp["manpower"] <= 0 or camp["location"] != target or not is_stationary(world, camp):
        return {"feasible": False, "reason": "reinforcement recipient is not a live stationary camp at the destination"}
    return {"feasible": True, "recipient_formation": recipient, "region": target}


def _movement_permission(world, operation, formation, leg):
    """Recheck the same real authority/access constraints before real progress."""
    from .military_logistics import _access
    state = _state(world)
    command = state["commands"].get(operation.get("latest_command"), {})
    issuer = command.get("issuer", operation["issuer"])
    permission = authority(world, operation["actor"], operation["formations"],
        operation["kind"], issuer, leg["to"])
    if not permission["feasible"]:
        return permission
    # Cancellation reverses only distance already physically travelled. It does
    # not create a new route into a hostile region or a camp access permission.
    if operation.get("cancel_return"):
        return {"feasible": True, "reason": "actual previously travelled edge return"}
    camp = None
    if operation["kind"] == "reinforce" and operation["params"].get("recipient_formation") is not None:
        camp = _recipient_camp(world, operation["actor"], operation["formations"],
            operation["target"], operation["params"])
        if not camp["feasible"]:
            return camp
    if _access(world, leg["to"], operation["actor"]):
        return {"feasible": True, "reason": "current actual region access"}
    journey = formation["journey"]
    final_leg = journey["leg_index"] == len(journey["legs"]) - 1 and leg["to"] == operation["target"]
    if final_leg and (operation["kind"] in OFFENSIVE or operation["kind"] == "blockade" or camp and camp["feasible"]):
        return {"feasible": True, "reason": "authorized final destination only", "recipient": camp}
    return {"feasible": False, "reason": "current military access does not permit this transit region"}


def _blocked_movement(world, operation, formation, permission):
    operation["reason_trace"].append({"date": world.date, "factor": "movement_constraint",
        "formation": formation["id"], "distance_km": 0., "detail": copy.deepcopy(permission)})
    operation["reason_trace"] = operation["reason_trace"][-64:]
    _emit(world, "military_movement_delayed", operation_id=operation["id"], formation=formation["id"],
        physical_constraint=copy.deepcopy(permission), travelled_km=0.)


def plan(world, operation_id, actor, formations, kind, target=None, route=None, *,
         commander=None, issuer=None, player=False, params=None):
    """Persist a plan; planning causes no instant movement or military result."""
    state = _state(world)
    kind = ALIASES.get(kind, kind)
    if not isinstance(operation_id, str) or not operation_id or operation_id in state["operations"]:
        raise ValueError("Military operation needs a new stable ID")
    if not isinstance(formations, (list, tuple)) or not formations or len(set(formations)) != len(formations):
        raise ValueError("Operation requires unique existing formations")
    formations = list(formations)
    issuer = issuer or commander
    permission = authority(world, actor, formations, kind, issuer, target)
    if not permission["feasible"]:
        raise ValueError(permission["reason"])
    if type(player) is not bool or params is not None and not isinstance(params, dict):
        raise ValueError("Invalid operation input")
    if route is not None and (not isinstance(route, (list, tuple)) or not all(isinstance(r, str) for r in route)):
        raise ValueError("Explicit route must contain existing region IDs")
    if commander is not None and commander not in (getattr(world, "npc_state", None) or {}).get("persons", {}):
        raise ValueError("Unknown operation commander")
    if target is not None and target not in world.regions:
        raise ValueError("Operation target is not an existing region")
    for formation_id in formations:
        formation = state["formations"][formation_id]
        if _number(formation["manpower"]) <= 0:
            raise ValueError("An empty formation cannot conduct an operation")
        assigned = formation.get("current_order")
        if assigned in state["operations"] and state["operations"][assigned]["status"] in ACTIVE:
            raise ValueError("Formation already committed to an active operation")
        if assigned is not None and assigned not in state["operations"]:
            raise ValueError("Formation already committed to military training or another physical assignment")
        if any(other != formation_id and _covered(state, [other], formation_id) for other in formations):
            raise ValueError("Parent and subordinate cannot be counted twice in an operation")
        for convoy in state["convoys"].values():
            committed = formation_id in convoy.get("formations", []) or formation_id in convoy.get("escorts", [])
            active_shipment = any(ship.get("convoy") == convoy.get("id") and ship["status"] not in {"delivered", "cancelled", "lost"}
                for ship in state["shipments"].values())
            same_escort = kind == "escort" and (params or {}).get("convoy") == convoy.get("id") and formation_id in convoy.get("escorts", [])
            if committed and active_shipment and not same_escort:
                raise ValueError("Formation is already physically committed to an active convoy")
    if kind in MOVEMENT and target is None:
        raise ValueError("Movement operation requires an actual destination")
    parameters = copy.deepcopy(params or {})
    if "transport_mode" in parameters and (not isinstance(parameters["transport_mode"], str) or parameters["transport_mode"] not in {"land", "sea"}):
        raise ValueError("Military transport mode must be land or sea")
    sea_reinforcement = kind == "reinforce" and parameters.get("transport_mode") == "sea"
    if kind == "reinforce" and parameters.get("recipient_formation") is None:
        recipients = {item["recipient"] for item in state["replacements"].values()
            if item.get("cohort") in formations}
        if len(recipients) > 1:
            raise ValueError("Reinforcement cohorts require one actual recipient")
        if recipients:
            parameters["recipient_formation"] = next(iter(recipients))
    camp = _recipient_camp(world, actor, formations, target, parameters) if kind == "reinforce" else None
    if kind == "reinforce" and parameters.get("recipient_formation") is not None and not camp["feasible"]:
        raise ValueError(camp["reason"])
    fleets = [state["formations"][f] for f in formations if state["formations"][f].get("service_type") in {"navy", "naval", "marine"}
        and _number(state["formations"][f].get("equipment_state", {}).get("ships", {}).get("quantity")) > 0
        and _number(state["formations"][f].get("equipment_state", {}).get("ships", {}).get("condition", 1)) > 0]
    if kind in NAVAL and (not fleets or kind != "amphibious" and len(fleets) != len(formations)):
        raise ValueError("A naval mission requires actual ships, crew and naval formations")
    if sea_reinforcement and not fleets:
        raise ValueError("Sea reinforcement requires actual co-located transport ships and crew")
    if kind in MOVEMENT and kind != "amphibious" and any(_mode(state["formations"][f], kind) == "sea" and
        state["formations"][f] not in fleets for f in formations):
        raise ValueError("Sea movement requires actual ships and crew")
    embarkation = {}
    if kind == "amphibious" or sea_reinforcement:
        transport = {}
        for fleet in fleets:
            transport[fleet["location"]] = transport.get(fleet["location"], 0.) + _transport_capacity(fleet)
        for origin in {state["formations"][f]["location"] for f in formations}:
            ground = [f for f in formations if state["formations"][f] not in fleets and state["formations"][f]["location"] == origin]
            crewed = [f["id"] for f in fleets if f["location"] == origin]
            if sum(state["formations"][f]["manpower"] for f in ground) > transport.get(origin, 0):
                raise ValueError("Amphibious troops exceed actual co-located ship transport capacity")
            embarkation[origin] = {"fleets": crewed, "troops": ground}
    if kind == "escort" and parameters.get("convoy") is not None:
        convoy = state["convoys"].get(parameters["convoy"])
        if not convoy or convoy["actor"] != actor or not set(formations) <= set(convoy.get("escorts", [])):
            raise ValueError("Convoy escort requires the actual assigned escort formations")
        active = [shipment for shipment in state["shipments"].values() if shipment.get("convoy") == convoy["id"] and shipment["status"] not in {"delivered", "cancelled", "lost"}]
        if active and target != active[0]["target_region"]:
            raise ValueError("Escort destination must match the actual convoy destination")
    for field in ("duration_days", "investment_days", "required_delay_days"):
        if field in parameters and (type(parameters[field]) is not int or parameters[field] <= 0):
            raise ValueError("Military durations must be positive integer days")
    if "forced_march" in parameters and type(parameters["forced_march"]) is not bool:
        raise ValueError("Forced march must be an explicit boolean order")
    if parameters.get("opponent") is not None and (parameters["opponent"] not in state["actors"] or parameters["opponent"] == actor):
        raise ValueError("Invalid military opponent")
    if "defenders" in parameters and (not isinstance(parameters["defenders"], list) or
        any(f not in state["formations"] or state["formations"][f]["actor"] == actor for f in parameters["defenders"])):
        raise ValueError("Invalid defending formation references")
    paths = {}
    if kind in MOVEMENT:
        from .military_logistics import route_quote
        for formation_id in formations:
            formation = state["formations"][formation_id]
            mode = _mode(formation, kind, {"transport_mode": "sea" if sea_reinforcement else None})
            quote = route_quote(world, formation["location"], target, mode=mode, actor=actor,
                formation=formation, purpose="movement", allow_hostile_destination=kind in OFFENSIVE or kind == "blockade" or bool(camp and camp["feasible"]))
            if not quote["physical_feasibility"]["feasible"]:
                raise ValueError("No physically usable military route: " + str(quote.get("bottlenecks", [])))
            if route is not None and list(route) != quote["path"]:
                raise ValueError("Explicit route differs from the usable graph route")
            paths[formation_id] = copy.deepcopy(quote)
    operation = {"id": operation_id, "actor": actor, "kind": kind, "formations": formations,
        "commander": commander, "issuer": issuer, "target": target, "opponent": parameters.get("opponent"),
        "params": parameters, "route": copy.deepcopy(route), "route_quote": paths,
        "status": "planned", "created_at": world.date, "issued_at": None, "transmitted_at": None,
        "received_at": None, "execution_started_at": None, "completed_at": None,
        "last_tick": None, "elapsed_days": 0, "progress": 0., "results": [],
        "objective_completion": None, "tactical_result": None, "cancel_return": False,
        "standing_intent": "await_orders", "latest_command": None, "next_autonomy_review": world.date,
        "embarkation": embarkation, "transport_mode": "sea" if kind == "amphibious" or sea_reinforcement else None,
        "reason_trace": [{"factor": "institutional_authority", "detail": permission}],
        "player_order": player}
    state["operations"][operation_id] = operation
    for formation_id in formations:
        state["formations"][formation_id]["current_order"] = operation_id
        state["formations"][formation_id]["operational_objective"] = {"kind": kind, "target": target}
    _emit(world, "operation_planned", operation_id=operation_id, operation=copy.deepcopy(operation))
    return copy.deepcopy(operation)


def _delay(world, actor, region):
    country = _state(world)["actors"].get(actor, {}).get("country", actor)
    if country in world.countries and getattr(world, "economy", None):
        from .crisis_response import communication_delay
        return communication_delay(world, country, region)
    # A nonstate actor has no fictional national telegraph administration.
    return 3


def _message_id(state):
    sequence = len(state["commands"]) + 1
    while "military-message-" + str(sequence) in state["commands"]:
        sequence += 1
    return "military-message-" + str(sequence), sequence


def send_command(world, operation_id, issuer, recipient=None, command=None, *, delay=None):
    operation, state = _operation(world, operation_id), _state(world)
    permission = authority(world, operation["actor"], operation["formations"], operation["kind"], issuer, operation["target"])
    if not permission["feasible"]:
        raise ValueError(permission["reason"])
    recipient = recipient or operation["commander"]
    if recipient is not None and recipient not in (getattr(world, "npc_state", None) or {}).get("persons", {}):
        raise ValueError("Unknown command recipient")
    if operation["commander"] is not None and recipient != operation["commander"]:
        raise ValueError("Command recipient does not command this operation")
    data = copy.deepcopy(command or {"intent": "execute"})
    if not isinstance(data, dict) or data.get("intent") not in INTENTS:
        raise ValueError("Unsupported military command intent")
    if data.get("intent") == "withdraw":
        destination = data.get("target")
        if destination is None:
            origins = {state["formations"][f]["origin_region"] for f in operation["formations"]}
            if len(origins) != 1:
                raise ValueError("Withdrawal of formations with different origins requires a destination")
            destination = next(iter(origins))
        if destination not in world.regions:
            raise ValueError("Withdrawal destination is not an existing region")
        permission = authority(world, operation["actor"], operation["formations"], "withdraw", issuer, destination)
        if not permission["feasible"]:
            raise ValueError(permission["reason"])
        data["target"] = destination
    if operation["status"] not in ACTIVE:
        raise ValueError("Cannot command an ended operation")
    if delay is None:
        delay = _delay(world, operation["actor"], state["formations"][operation["formations"][0]]["location"])
    if type(delay) is not int or delay < 0:
        raise ValueError("Command delay must be a nonnegative integer")
    message_id, sequence = _message_id(state)
    message = {"id": message_id, "sequence": sequence, "message_kind": "command",
        "operation_id": operation_id, "issuer": issuer, "recipient": recipient,
        "created_at": world.date, "issued_at": world.date, "transmitted_at": world.date,
        "expected_received_at": _after(world.date, delay), "received_at": None,
        "execution_at": None, "communication_delay": delay, "as_of": world.date,
        "status": "queued", "belief_accepted": None, "data": data}
    state["commands"][message_id] = message
    # A fresh dispatch cannot undo the currently received order before receipt.
    # Operation timestamps mark first activation; each message retains its own
    # complete chronology for later corrections and command changes.
    if operation["status"] == "planned":
        operation["status"] = "issued"
    operation["issued_at"] = operation["issued_at"] or world.date
    operation["transmitted_at"] = operation["transmitted_at"] or world.date
    _emit(world, "command_transmitted", operation_id=operation_id, message=copy.deepcopy(message))
    return copy.deepcopy(message)


def queue_report(world, operation_id, recipient, facts, *, delay=None, source="military field observation",
                 as_of=None, confidence=.8, reliability=.8, accuracy=.8, interpretation=None,
                 salience=.5, personal_impact=0., emotional_valence=0.):
    """An explicit frozen observation, never an automatic truth broadcast."""
    operation, state = _operation(world, operation_id), _state(world)
    persons = (getattr(world, "npc_state", None) or {}).get("persons", {})
    if recipient not in persons or not isinstance(facts, dict):
        raise ValueError("A military report needs a known NPC and observed facts")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in (confidence, reliability, accuracy, salience)):
        raise ValueError("Invalid military report quality")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in (personal_impact, emotional_valence)):
        raise ValueError("Invalid military memory impact")
    observed = as_of or world.date
    if canonical_date(observed) > canonical_date(world.date):
        raise ValueError("Cannot report a future military observation")
    if delay is None:
        delay = _delay(world, persons[recipient]["country"], operation["target"])
    if type(delay) is not int or delay < 0 or not isinstance(source, str) or not source:
        raise ValueError("Invalid report delivery")
    if interpretation is not None and not isinstance(interpretation, dict):
        raise ValueError("Military report interpretation must be a mapping")
    message_id, sequence = _message_id(state)
    message = {"id": message_id, "sequence": sequence, "message_kind": "report",
        "operation_id": operation_id, "issuer": None, "recipient": recipient,
        "created_at": world.date, "issued_at": world.date, "transmitted_at": world.date,
        "expected_received_at": _after(world.date, delay), "received_at": None,
        "execution_at": None, "communication_delay": delay, "as_of": observed,
        "status": "queued", "belief_accepted": None, "source": source,
        "confidence": confidence, "reliability": reliability, "accuracy": accuracy,
        "data": {"event_id": "military-observation:" + operation_id, "day": observed,
            "subject": "military:" + operation_id, "category": "custom:military_event",
            "facts": npc_state._json(facts)},
        "memory": {"interpretation": npc_state._json(interpretation or {}), "salience": salience,
            "personal_impact": personal_impact, "group_impact": 0., "emotional_valence": emotional_valence,
            "tags": ["military", "war", operation["kind"]], "enduring": salience >= .8}}
    state["commands"][message_id] = message
    _emit(world, "military_report_queued", operation_id=operation_id, message_id=message_id,
        recipient=recipient, as_of=observed, expected_received_at=message["expected_received_at"])
    return copy.deepcopy(message)


def _observation_order(message):
    return message["as_of"], message["issued_at"], message["transmitted_at"], message["sequence"]


def _release(world, operation):
    state = _state(world)
    for formation_id in operation["formations"]:
        formation = state["formations"][formation_id]
        if formation.get("current_order") == operation["id"]:
            formation["current_order"] = None
            formation["operational_objective"] = None
            formation["journey"] = None


def _cancel(world, operation, message, *, withdrawal=None):
    """A formation already on an edge must physically return to its last node."""
    state, returning = _state(world), False
    for formation_id in operation["formations"]:
        formation = state["formations"][formation_id]
        journey = formation.get("journey")
        progress = _number((journey or {}).get("leg_progress_km"))
        if journey is None or progress <= 1e-9 or journey["leg_index"] >= len(journey["legs"]):
            formation["journey"] = None
            continue
        leg = journey["legs"][journey["leg_index"]]
        reverse = {**copy.deepcopy(leg), "from": leg["to"], "to": leg["from"],
            "origin": leg["to"], "destination": leg["from"], "distance_km": progress}
        formation["journey"] = {"operation_id": operation["id"], "phase": "cancel-return:" + str(message["sequence"]),
            "origin": formation["location"], "destination": formation["location"],
            "departure": world.date, "path": [formation["location"]], "legs": [reverse],
            "leg_index": 0, "leg_progress_km": 0., "distance_travelled_km": 0.,
            "leg_departure": world.date, "arrival": None,
            "turnaround": {"link": leg["link"], "original_from": leg["from"], "original_to": leg["to"],
                "original_progress_km": progress}}
        returning = True
    message["execution_at"] = world.date
    operation["cancel_return"] = returning
    operation["queued_withdrawal"] = copy.deepcopy(withdrawal)
    if returning:
        operation["status"] = "in_progress"
        operation["cancel_return_started_at"] = world.date
        _emit(world, "operation_cancel_return", operation_id=operation["id"],
            reason="already travelled edge distance must be returned under real movement constraints")
    elif withdrawal is not None:
        _activate_withdrawal(world, operation)
    else:
        operation.update(status="cancelled", completed_at=world.date)
        _release(world, operation)
        _emit(world, "operation_cancelled", operation_id=operation["id"], physical_return_required=False)


def _activate_withdrawal(world, operation):
    """Replace the objective only after any already travelled edge is returned."""
    from .military_logistics import route_quote
    state, request = _state(world), operation["queued_withdrawal"]
    quotes = {}
    for formation_id in operation["formations"]:
        f = state["formations"][formation_id]
        quote = route_quote(world, f["location"], request["target"], mode=_mode(f, "withdraw", operation),
            actor=operation["actor"], formation=f, purpose="movement")
        if not quote["physical_feasibility"]["feasible"]:
            operation.update(kind="withdraw", target=request["target"], route_quote={}, status="paused",
                cancel_return=False, disruption="withdrawal_route_unavailable")
            _emit(world, "operation_disrupted", operation_id=operation["id"], reason="received withdrawal has no usable physical route")
            return
        quotes[formation_id] = copy.deepcopy(quote)
    operation.setdefault("objective_history", []).append({"date": world.date, "kind": operation["kind"], "target": operation["target"]})
    operation.update(kind="withdraw", target=request["target"], route_quote=quotes, route=None,
        status="received", cancel_return=False, queued_withdrawal=None, progress=0.)
    for key in ("on_target_since", "on_target_last_tick", "investment_days", "next_engagement_review"):
        operation.pop(key, None)
    for formation_id in operation["formations"]:
        f = state["formations"][formation_id]
        f["journey"] = None
        f["operational_objective"] = {"kind": "withdraw", "target": request["target"]}
    _emit(world, "operation_withdrawal_started", operation_id=operation["id"], destination=request["target"],
        reason="received order replaces offensive objective after physical turnaround")


def deliver_due(world):
    state, latest, due, delivered = _state(world), {}, [], []
    due = [message for message in queued_commands(state["commands"])
           if message["expected_received_at"] <= world.date]
    accepted_history = latest_by_group(state["commands"], 'accepted-command-history',
        command_group, _observation_order,
        lambda message: message["status"] == "delivered" and message.get("belief_accepted") is True,
        (command_group(message) for message in due))
    latest = {key: _observation_order(message) for key, message in accepted_history.items()}
    for message in sorted(due, key=lambda m: (m["expected_received_at"], _observation_order(m))):
        key = message["operation_id"], message["recipient"], message["message_kind"]
        accepted = key not in latest or _observation_order(message) > latest[key]
        operation = state["operations"][message["operation_id"]]
        if accepted:
            latest[key] = _observation_order(message)
            if message["message_kind"] == "report":
                actor, data = message["recipient"], message["data"]
                topic = "military-operation:" + operation["id"]
                npc_state.receive_report(world.npc_state, actor, topic, data, message["as_of"],
                    message["source"], message["accuracy"], message["confidence"], message["reliability"])
                world.npc_state["reports"][actor][topic]["received_at"] = world.date
                npc_state.remember(world.npc_state, actor, data["event_id"], reliability=message["reliability"], **message["memory"])
                world.npc_state["memories"][actor][-1]["received_date"] = world.date
                world.npc_state["adapter"]["pending"].setdefault(actor, []).append("military report")
            elif operation["status"] in ACTIVE:
                intent = message["data"]["intent"]
                operation.update(received_at=operation["received_at"] or world.date,
                    latest_command=message["id"], standing_intent=intent)
                if intent == "cancel":
                    _cancel(world, operation, message)
                elif intent == "withdraw":
                    _cancel(world, operation, message, withdrawal={"target": message["data"]["target"]})
                elif intent in {"hold", "avoid_conflict", "hold_fire"}:
                    operation["status"] = "paused"
                    message["execution_at"] = world.date
                else:
                    operation["status"] = "received"
            else:
                # Historical commands remain auditable after their operation
                # ends, but cannot restart it or resurrect dead formations.
                accepted = False
        message.update(status="delivered", received_at=world.date, belief_accepted=accepted)
        entry = {"operation_id": operation["id"], "message_id": message["id"],
            "recipient": message["recipient"], "as_of": message["as_of"], "received_at": world.date,
            "belief_accepted": accepted, "message_kind": message["message_kind"]}
        _emit(world, "military_message_received", **entry)
        delivered.append(entry)
    return delivered


def _begin(world, operation):
    state = _state(world)
    operation.update(status="in_progress", execution_started_at=operation["execution_started_at"] or world.date)
    if operation.get("latest_command") in state["commands"]:
        state["commands"][operation["latest_command"]]["execution_at"] = world.date
    for formation_id, quote in operation["route_quote"].items():
        formation = state["formations"][formation_id]
        if operation["kind"] == "escort" and operation["params"].get("convoy") is not None:
            continue  # The cargo scheduler owns this single physical voyage.
        if formation.get("journey") is None and formation["location"] != operation["target"]:
            formation["journey"] = {"operation_id": operation["id"], "origin": formation["location"],
                "destination": operation["target"], "departure": world.date, "path": copy.deepcopy(quote["path"]),
                "legs": copy.deepcopy(quote["legs"]), "leg_index": 0, "leg_progress_km": 0.,
                "distance_travelled_km": 0., "leg_departure": world.date, "arrival": None}
    _emit(world, "operation_execution_started", operation_id=operation["id"], intent=operation["standing_intent"])


def _movement(world, operation, formation, *, _rate=None, _reservation=None):
    journey = formation.get("journey")
    if journey is None or journey["leg_index"] >= len(journey["legs"]):
        return (journey.get("arrival") is not None and formation["location"] == journey["destination"]) if journey else formation["location"] == operation["target"]
    from .military_logistics import movement_rate, reserve_capacity
    leg = journey["legs"][journey["leg_index"]]
    permission = _movement_permission(world, operation, formation, leg)
    if not permission["feasible"]:
        _blocked_movement(world, operation, formation, permission)
        return False
    rate = movement_rate(world, formation, leg) if _rate is None else _rate
    if leg["mode"] == "sea" and _sea_formation(formation) and (
        _number(formation.get("equipment_state", {}).get("ships", {}).get("quantity")) <= 0 or formation["manpower"] <= 0):
        rate = {**rate, "speed_km_day": 0., "reasons": [*rate.get("reasons", []), {"factor": "actual_ships_or_crew_missing", "value": 0}]}
    reservation = _reservation if _reservation is not None else reserve_capacity(world, leg["link"], max(1., _number(formation["manpower"])),
        actor=formation["actor"], reservation_id=f"movement:{operation['id']}:{formation['id']}:{journey['leg_index']}", date=world.date)
    capacity_ratio = _clip(reservation.get("ratio", 0))
    remaining = max(0., _number(leg["distance_km"]) - journey["leg_progress_km"])
    marching = leg["mode"] in {"trail", "road"} and not _sea_formation(formation)
    forced = marching and operation["params"].get("forced_march", False)
    speed = max(0., _number(rate.get("speed_km_day"))) * (1.35 if forced else 1.)
    travel = min(remaining, speed * capacity_ratio)
    march_cost = None
    if marching and travel > 0:
        fatigue_before, cohesion_before = formation["fatigue"], formation["cohesion"]
        formation["fatigue"] = _clip(fatigue_before + travel / 1000 * (2 if forced else 1))
        if forced:
            from .military_logistics import consume_supply, FOOD_PER_PERSON_DAY
            supplied = formation["supply_state"]
            previous_ratio = supplied["last_ratio"]
            previous_components = copy.deepcopy(supplied.get("component_ratios", {}))
            requested = formation["manpower"] * FOOD_PER_PERSON_DAY * .35 * travel / 20
            receipt = consume_supply(world, formation["id"], {"food": requested},
                receipt_id=f"forced-march:{operation['id']}:{formation['id']}:{world.date}")
            # Extra rations cannot erase today's ordinary fuel/ammunition/food
            # shortages. Keep this separate finite receipt and merge the deficit.
            supplied["last_ratio"] = min(previous_ratio, receipt["ratio"])
            supplied["component_ratios"] = {**previous_components, "food": min(
                previous_components.get("food", 1.), receipt["component_ratios"].get("food", 1.))}
            shortage = receipt["shortages"].get("food", 0) / requested if requested else 0.
            formation["cohesion"] = _clip(cohesion_before - travel / 2000 * (1 + shortage))
            march_cost = {"extra_food_requested": requested, "extra_food_consumed": receipt["actual"].get("food", 0.),
                "extra_food_shortage": receipt["shortages"].get("food", 0.), "receipt_id": receipt["id"]}
        march_cost = {**(march_cost or {}), "forced": bool(forced), "speed_multiplier": 1.35 if forced else 1.,
            "fatigue_before": fatigue_before, "fatigue_after": formation["fatigue"],
            "cohesion_before": cohesion_before, "cohesion_after": formation["cohesion"]}
    journey["leg_progress_km"] += travel
    journey["distance_travelled_km"] += travel
    operation["reason_trace"].append({"date": world.date, "factor": "movement_progress",
        "formation": formation["id"], "distance_km": travel, "capacity_ratio": capacity_ratio,
        "reasons": copy.deepcopy(rate.get("reasons", [])), "march_cost": march_cost})
    # Retain a bounded current explanation; full progress is emitted permanently.
    operation["reason_trace"] = operation["reason_trace"][-64:]
    _emit(world, "formation_marched", operation_id=operation["id"], formation=formation["id"],
        origin=leg["from"], destination=leg["to"], travelled_km=travel, remaining_km=max(0., remaining-travel),
        capacity_ratio=capacity_ratio, progress_reasons=copy.deepcopy(rate.get("reasons", [])), march_cost=march_cost)
    if journey["leg_progress_km"] + 1e-9 >= _number(leg["distance_km"]):
        origin = formation["location"]
        formation["location"] = leg["to"]
        phase = journey.get("phase", "advance")
        receipt_id = f"movement:{operation['id']}:{formation['id']}:{phase}:{journey['leg_index']}"
        _state(world)["movement_receipts"][receipt_id] = {"id": receipt_id,
            "operation_id": operation["id"], "formation": formation["id"], "date": world.date,
            "origin": origin, "destination": formation["location"], "departure": journey["leg_departure"],
            "distance_km": leg["distance_km"], "link": leg["link"], "mode": leg["mode"],
            "elapsed_days": (canonical_date(world.date)-canonical_date(journey["leg_departure"])).days,
            "phase": phase, "turnaround": copy.deepcopy(journey.get("turnaround"))}
        journey["leg_index"] += 1
        journey["leg_progress_km"] = 0.
        journey["leg_departure"] = world.date
        _emit(world, "formation_arrived", operation_id=operation["id"], formation=formation["id"],
            origin=origin, destination=formation["location"], departure=journey["departure"],
            distance_travelled_km=journey["distance_travelled_km"], route=copy.deepcopy(journey["path"]))
        if journey["leg_index"] == len(journey["legs"]):
            journey["arrival"] = world.date
            return True
    return False


def _amphibious_movement(world, operation):
    """Ships and embarked troops share one voyage and one capacity reservation."""
    from .military_logistics import movement_rate, reserve_capacity
    state, arrivals = _state(world), []
    for origin, group in sorted(operation["embarkation"].items()):
        ids = [*group["fleets"], *group["troops"]]
        active = [state["formations"][f] for f in ids if state["formations"][f].get("journey") is not None
            and state["formations"][f]["journey"]["leg_index"] < len(state["formations"][f]["journey"]["legs"])]
        if not active:
            arrivals.extend(True if operation.get("cancel_return") else state["formations"][f]["location"] == operation["target"] for f in ids)
            continue
        journey = active[0]["journey"]
        leg = journey["legs"][journey["leg_index"]]
        permissions = [(f, _movement_permission(world, operation, f,
            f["journey"]["legs"][f["journey"]["leg_index"]])) for f in active]
        if any(not permission["feasible"] for f, permission in permissions):
            for f, permission in permissions:
                if not permission["feasible"]:
                    _blocked_movement(world, operation, f, permission)
            arrivals.extend(False for f in ids)
            continue
        # Embarked soldiers travel on the transport ships; their walking speed
        # or personal fuel stock is not a second naval propulsion system.
        rates = [movement_rate(world, state["formations"][f], leg) for f in group["fleets"]]
        capacity = math.fsum(_transport_capacity(state["formations"][f]) for f in group["fleets"])
        needed = sum(state["formations"][f]["manpower"] for f in group["troops"])
        speed = min((_number(r["speed_km_day"]) for r in rates), default=0.)
        if capacity + 1e-9 < needed or not group["fleets"]:
            speed = 0.
        rate = {"speed_km_day": speed, "reasons": [{"factor": "embarked_shared_fleet_pace", "value": speed},
            {"factor": "actual_ship_transport_capacity", "available": capacity, "required": needed}]}
        reservation = reserve_capacity(world, leg["link"], max(1, sum(state["formations"][f]["manpower"] for f in ids)),
            actor=operation["actor"], reservation_id=f"amphibious:{operation['id']}:{origin}:{journey.get('phase', 'advance')}:{journey['leg_index']}", date=world.date) if speed > 0 else {"ratio": 0.}
        for formation_id in ids:
            formation = state["formations"][formation_id]
            arrivals.append(True if operation.get("cancel_return") and formation.get("journey") is None else
                _movement(world, operation, formation, _rate=rate, _reservation=reservation))
    return arrivals


def _known(view, operation_id):
    for report in view.get("beliefs", {}).values():
        if isinstance(report, dict) and report.get("event_id") == "military-observation:" + operation_id:
            return report.get("facts", {})
    return None


def _autonomy(world, operation):
    commander = operation.get("commander")
    npc = getattr(world, "npc_state", None) or {}
    if commander not in npc.get("persons", {}) or operation["next_autonomy_review"] > world.date:
        return
    operation["next_autonomy_review"] = _after(world.date, 7)
    state = _state(world)
    delegation = state["delegations"].get(commander, {})
    if "local" not in delegation.get("scopes", []) or delegation.get("actor") != operation["actor"] or (
        delegation.get("expires") is not None and delegation["expires"] < world.date) or not npc["persons"][commander].get("active", True):
        return
    view = npc_state._actor_read_view(world.npc_state, commander, world.date)
    known = _known(view, operation["id"])
    if not isinstance(known, dict):
        return
    threat = _clip(known.get("threat", 0))
    intent = operation["standing_intent"]
    options = [{"id": "obey", "kind": "status_quo", "tags": ["local", "military"],
        "outcomes": {"security": -.25 * threat if intent in {"hold", "avoid_conflict", "hold_fire"} else .2,
            "institutional_duty": .5}},
        {"id": "request_reconsideration", "kind": "local_action", "tags": ["local", "military"],
         "outcomes": {"security": .1 * threat, "fiscal": .1, "prestige": -.1},
         "institutional_feasibility": {"feasible": True}},
        {"id": "limited_local_action", "kind": "local_action", "tags": ["local", "war", "war_preparation"],
         "belief_context": {"threat": threat, "own_strength": _number(known.get("own_strength")),
             "enemy_strength": _number(known.get("enemy_strength"))},
         "outcomes": {"security": .65 * threat, "prestige": .4 * threat,
             "fiscal": -.15, "institutional_duty": -.6 if intent in {"hold", "avoid_conflict", "hold_fire"} else 0},
         "institutional_feasibility": {"feasible": True}}]
    from .military_state import stream
    with stream(world, f"operation-autonomy:{operation['id']}:{commander}") as rng:
        decision = npc_decision.decide(view, options, rng)
    _emit(world, "military_commander_decision", operation_id=operation["id"], commander=commander,
        decision=copy.deepcopy(decision), received_intent=intent, observed_threat=threat)
    if decision["selected"] == "request_reconsideration":
        _emit(world, "military_reconsideration_requested", operation_id=operation["id"], commander=commander)
    elif decision["selected"] == "limited_local_action":
        # A limited delegated action is a trigger for the causal Crisis layer,
        # not an unauthorised national mobilization or declaration of war.
        locations = [state["formations"][f]["location"] for f in operation["formations"]]
        if all(r in delegation.get("regions", []) for r in locations) and all(
            _covered(state, delegation.get("formations", []), f) for f in operation["formations"]) and any(
                state["formations"][f]["manpower"] > 0 for f in operation["formations"]):
            _emit(world, "military_local_action", operation_id=operation["id"], actor=operation["actor"],
                commander=commander, formations=list(operation["formations"]), locations=locations,
                central_received_intent=intent, perceived_threat=threat,
                actual_national_policy_changed=False, combat_resolved=False,
                observed_facts=copy.deepcopy(known), decision=copy.deepcopy(decision))


def _complete(world, operation, reason):
    if operation.get("cancel_return") and operation.get("queued_withdrawal") is not None:
        _activate_withdrawal(world, operation)
        return
    operation.update(status="cancelled" if operation.get("cancel_return") else "completed", completed_at=world.date, progress=1.)
    _release(world, operation)
    _emit(world, "operation_cancelled" if operation.get("cancel_return") else "operation_completed",
        operation_id=operation["id"], reason=reason, results=list(operation["results"]))


def _hostile(world, own, other, operation):
    if operation.get("opponent") is not None:
        return operation["opponent"] == other
    state = _state(world)
    a, b = state["actors"][own].get("country"), state["actors"][other].get("country")
    return any({relation.get("a"), relation.get("b")} == {a, b} and relation.get("status") == "war"
        for relation in world.relations.values()) if a is not None and b is not None else False


def _nonoffensive_contact(world, operation, days):
    """Physical contact adjudication, never a political enemy-information lookup."""
    if operation["kind"] not in {"defend", "hold", "reserve", "withdraw", "reinforce"} or operation.get("cancel_return"):
        return False
    state = _state(world)
    if operation.get("contact_resolved"):
        return False
    locations = sorted({state["formations"][f]["location"] for f in operation["formations"]
        if state["formations"][f]["manpower"] > 0 and
        not _number((state["formations"][f].get("journey") or {}).get("leg_progress_km"))})
    for region in locations:
        explicit = operation["params"].get("defenders")
        defenders = sorted(key for key, f in state["formations"].items() if f["location"] == region and f["manpower"] > 0
            and f["actor"] != operation["actor"] and (key in explicit if explicit is not None else
                _hostile(world, operation["actor"], f["actor"], operation)))
        if not defenders:
            continue
        contact = operation.setdefault("contact", {"region": region, "first_date": world.date, "days": 0, "last_date": None})
        if contact["region"] != region:
            contact.update(region=region, first_date=world.date, days=0, last_date=None)
        if contact["last_date"] != world.date:
            contact["days"] += days
            contact["last_date"] = world.date
        required = operation["params"].get("required_delay_days", 7) if operation["params"].get("war_aim") == "delay" else 1
        if contact["days"] < required:
            _emit(world, "military_contact_delay", operation_id=operation["id"], region=region,
                actual_contact_days=contact["days"], required_days=required)
            return True
        projected = copy.deepcopy(operation)
        projected.update(target=region, formations=[f for f in operation["formations"]
            if state["formations"][f]["location"] == region], investment_days=contact["days"])
        projected["params"]["defenders"] = defenders
        if projected["kind"] == "reinforce":
            projected["kind"] = "relief"
        from .military_combat import resolve
        from .military_bridge import apply_result
        result = resolve(world, projected)
        apply_result(world, result)
        operation["results"].append(result["id"])
        operation.update(contact_resolved=True, objective_completion=result.get("objective_completion", 0.),
            tactical_result=result.get("tactical_result"))
        _emit(world, "military_engagement_resolved", operation_id=operation["id"], result=copy.deepcopy(result))
        if operation["kind"] in {"defend", "hold", "reserve"}:
            _complete(world, operation, "actual defensive contact adjudicated")
            return True
        return False
    return False


def tick(world, tick_event, rng=None):
    """Daily active-operation scheduler; all publications use the shared outbox."""
    if type(tick_event.get("days")) is not int or tick_event["days"] <= 0:
        raise ValueError("Military operations require positive elapsed days")
    state = _state(world)
    deliver_due(world)
    for operation in active_operations(state["operations"]):
        if operation["status"] not in ACTIVE or operation["last_tick"] == world.date:
            continue
        operation["last_tick"] = world.date
        if not any(state["formations"][f]["manpower"] > 0 for f in operation["formations"]):
            if operation.get("disruption") != "formations_unavailable":
                operation.update(status="disrupted", disruption="formations_unavailable")
                _emit(world, "operation_disrupted", operation_id=operation["id"], reason="no surviving available formation manpower")
            continue
        _autonomy(world, operation)
        if operation["status"] == "received":
            _begin(world, operation)
            # Reception is not a free elapsed march day.
            continue
        if operation["status"] != "in_progress":
            continue
        if operation.get("cancel_return_started_at") == world.date:
            continue
        operation["elapsed_days"] += int(tick_event["days"])
        if _nonoffensive_contact(world, operation, tick_event["days"]):
            continue
        if operation["kind"] == "escort" and operation["params"].get("convoy") is not None:
            convoy_id = operation["params"]["convoy"]
            latest = latest_by_group(state["shipments"], 'latest-convoy-voyage',
                lambda shipment: shipment.get("convoy"), lambda shipment: (shipment.get("created_at", ""), shipment["id"]),
                lambda shipment: True, [convoy_id]).get(convoy_id)
            if latest and latest["status"] in {"delivered", "cancelled", "lost"}:
                _complete(world, operation, "actual escorted convoy voyage ended: " + latest["status"])
            else:
                _emit(world, "military_mission_active", operation_id=operation["id"], mission="escort",
                    actor=operation["actor"], convoy=convoy_id, shipment=latest["id"] if latest else None,
                    formations=list(operation["formations"]), basis="cargo scheduler owns shared physical progress")
            continue
        shared_voyage = bool(operation.get("embarkation"))
        arrivals = _amphibious_movement(world, operation) if shared_voyage else []
        if not shared_voyage:
            for formation_id in operation["formations"]:
                formation = state["formations"][formation_id]
                arrivals.append(True if operation.get("cancel_return") and formation.get("journey") is None else
                    _movement(world, operation, formation) if operation["kind"] in MOVEMENT else True)
        if operation["kind"] in MOVEMENT:
            quotes = operation["route_quote"]
            total = sum(_number(q.get("distance_km")) for q in quotes.values())
            travelled = sum(_number(state["formations"][f].get("journey", {}).get("distance_travelled_km"))
                if state["formations"][f].get("journey") else _number(quotes[f].get("distance_km")) for f in quotes)
            operation["progress"] = min(.95, travelled / total) if total else .95
        if not all(arrivals):
            continue
        if operation.get("cancel_return"):
            _complete(world, operation, "actual reverse edge distance completed after cancellation")
            continue
        kind = operation["kind"]
        if kind in OFFENSIVE:
            minimum = max(1, int(_number(operation["params"].get("investment_days"), 7 if kind == "siege" else 1)))
            on_target = operation.setdefault("on_target_since", world.date)
            last = operation.get("on_target_last_tick", world.date)
            invested = operation.get("investment_days", 0) + min(int(tick_event["days"]),
                (canonical_date(world.date) - canonical_date(last)).days)
            operation["investment_days"] = invested
            operation["on_target_last_tick"] = world.date
            if invested < minimum:
                _emit(world, "siege_investment" if kind == "siege" else "engagement_preparation",
                    operation_id=operation["id"], elapsed_days=invested, minimum_days=minimum)
                continue
            if operation.get("next_engagement_review", world.date) > world.date:
                continue
            from .military_combat import resolve
            from .military_bridge import apply_result
            result = resolve(world, operation)
            if result["id"] not in operation["results"]:
                apply_result(world, result)
                operation["results"].append(result["id"])
                operation["objective_completion"] = result.get("objective_completion", 0.)
                operation["tactical_result"] = result.get("tactical_result")
                _emit(world, "military_engagement_resolved", operation_id=operation["id"], result=copy.deepcopy(result))
            if kind == "siege" and not result.get("strategic_objective_achieved", False) and not result.get("retreat", False):
                duration = operation["params"].get("duration_days")
                alive = any(state["formations"][f]["manpower"] > 0 for f in operation["formations"])
                if alive and (duration is None or operation["elapsed_days"] < duration):
                    operation["next_engagement_review"] = _after(world.date, 7)
                    _emit(world, "siege_continues", operation_id=operation["id"],
                        investment_days=invested, next_review=operation["next_engagement_review"],
                        objective_completion=operation["objective_completion"], result_id=result["id"])
                    continue
            _complete(world, operation, "adjudicated military engagement")
        elif kind in {"blockade", "escort", "naval_patrol", "defend", "hold", "reserve"}:
            duration = operation["params"].get("duration_days")
            if duration is not None and operation["elapsed_days"] >= duration:
                _complete(world, operation, "mission duration elapsed")
            else:
                _emit(world, "military_mission_active", operation_id=operation["id"], mission=kind,
                    actor=operation["actor"], target=operation["target"], formations=list(operation["formations"]))
        else:
            _complete(world, operation, "formations physically arrived")
    return []


def blockade_coverage(world, link_id, actor=None):
    """Actual active fleet coverage; a paper blockade never closes every port."""
    state, coverage = _state(world), []
    if link_id not in state["links"]:
        raise ValueError("Unknown blockade route link")
    link = state["links"][link_id]
    endpoints = (link["origin"], link["destination"])
    if link["mode"] != "sea":
        return {"disruption": 0., "reasons": [], "formations": []}
    for operation in active_operations(state["operations"]):
        if operation["kind"] != "blockade" or operation["status"] != "in_progress" or operation["target"] not in endpoints or operation["actor"] == actor:
            continue
        if operation.get("opponent") is not None and actor is not None and operation["opponent"] != actor:
            continue
        targets = operation["params"].get("targets")
        if isinstance(targets, list) and actor is not None and actor not in targets:
            continue
        fleets = [state["formations"][f] for f in operation["formations"]
            if state["formations"][f]["location"] == operation["target"] and state["formations"][f].get("service_type") in {"naval", "navy", "marine"}]
        if not fleets:
            continue
        readiness = min(_clip(f.get("readiness")) * _clip(f.get("supply_state", {}).get("last_ratio", 0)) for f in fleets)
        quantity = math.fsum(max(0., _number(f.get("equipment_state", {}).get("ships", {}).get("quantity"))) *
            _clip(f.get("equipment_state", {}).get("ships", {}).get("condition", 1)) for f in fleets)
        strength = quantity / (quantity + 20) * readiness if quantity else 0.
        coverage.append({"operation_id": operation["id"], "actor": operation["actor"], "port": operation["target"],
            "strength": strength, "formations": [f["id"] for f in fleets],
            "reason": "present naval equipment, readiness and actual supply"})
    disruption = 1 - math.prod(1 - c["strength"] for c in coverage)
    return {"disruption": _clip(disruption), "reasons": coverage,
        "formations": sorted({f for c in coverage for f in c["formations"]})}


def explain(world, operation_id):
    operation = copy.deepcopy(_operation(world, operation_id))
    operation["actual_results"] = [copy.deepcopy(_state(world)["loss_receipts"][key]["result"])
        for key in operation["results"] if key in _state(world)["loss_receipts"]]
    return operation


def _embarkation_issues(world, operation):
    """Saved voyage identities survive losses, but cannot invent transport assets."""
    state, issues = _state(world), []
    groups, mode = operation.get("embarkation", {}), operation.get("transport_mode")
    if not isinstance(groups, dict) or mode not in (None, "sea"):
        return ["military: malformed saved embarkation/transport mode"]
    shared = bool(groups)
    if shared != (mode == "sea") or shared and operation["kind"] not in {"amphibious", "reinforce", "withdraw"}:
        issues.append("military: saved voyage conflicts with operation transport mode/kind")
    if operation["kind"] == "amphibious" and not shared or operation["kind"] == "reinforce" and (
        operation["params"].get("transport_mode") == "sea") != shared:
        issues.append("military: seaborne operation lacks its actual embarkation")
    if not shared:
        return issues
    allocated = []
    for origin, group in groups.items():
        if origin not in world.regions or not isinstance(group, dict) or set(group) != {"fleets", "troops"} or any(
            not isinstance(group.get(field), list) or not all(isinstance(f, str) for f in group[field]) for field in ("fleets", "troops")):
            issues.append("military: malformed embarkation group/origin")
            continue
        members = [*group["fleets"], *group["troops"]]
        allocated.extend(members)
        if not group["fleets"] or any(f not in operation["formations"] or f not in state["formations"] or
            state["formations"][f]["actor"] != operation["actor"] for f in members):
            issues.append("military: embarkation lacks actual assigned own transport formations")
            continue
        # Quantity/crew/capacity can decrease after a battle. The persistent
        # assignment remains valid; daily physical constraints stop the voyage.
        if any(not _sea_formation(state["formations"][f]) for f in group["fleets"]) or any(
            _sea_formation(state["formations"][f]) for f in group["troops"]):
            issues.append("military: embarked fleet/ground identities are inconsistent")
        quotes = [operation["route_quote"].get(f) for f in members]
        if operation["kind"] == "withdraw" and not operation["route_quote"] and operation.get("disruption") == "withdrawal_route_unavailable":
            continue  # A received, physically blocked withdrawal retains its prior assignment.
        if any(not isinstance(q, dict) or not isinstance(q.get("path"), list) or not q["path"] or
            q["path"][-1] != operation["target"] for q in quotes):
            issues.append("military: embarked formation lacks its actual route/destination")
        else:
            route_origins = {q["path"][0] for q in quotes}
            if len(route_origins) != 1 or operation["kind"] != "withdraw" and route_origins != {origin}:
                issues.append("military: embarkation groups do not share their actual route origin")
    if len(allocated) != len(set(allocated)) or set(allocated) != set(operation["formations"]):
        issues.append("military: embarkation must partition assigned formations exactly once")
    return issues


def validation_issues(world, old=None):
    """Military-state audit extension for persisted operations and messages."""
    state, issues, allocated = _state(world), [], {}
    persons = (getattr(world, "npc_state", None) or {}).get("persons", {})
    try:
        for key, operation in audit_items(state["operations"]):
            if operation["id"] != key or operation["actor"] not in state["actors"] or operation["kind"] not in KINDS or operation["status"] not in STATUSES:
                issues.append("military: invalid operation identity, kind or status")
            if not isinstance(operation["formations"], list) or not operation["formations"] or len(set(operation["formations"])) != len(operation["formations"]) or any(
                f not in state["formations"] or state["formations"][f]["actor"] != operation["actor"] for f in operation["formations"]):
                issues.append("military: invalid operation formation references")
            if operation["target"] is not None and operation["target"] not in world.regions:
                issues.append("military: nonexistent operation destination")
            if operation.get("commander") is not None and operation["commander"] not in persons:
                issues.append("military: nonexistent operation commander")
            if operation.get("standing_intent") not in INTENTS | {"await_orders"}:
                issues.append("military: invalid received operational intent")
            if operation.get("opponent") is not None and operation["opponent"] not in state["actors"]:
                issues.append("military: nonexistent operational opponent")
            if not isinstance(operation.get("params"), dict) or not isinstance(operation.get("route_quote"), dict):
                issues.append("military: malformed operation parameters/routes")
            for name in ("duration_days", "investment_days", "required_delay_days"):
                if name in operation["params"] and (type(operation["params"][name]) is not int or operation["params"][name] <= 0):
                    issues.append("military: invalid persisted military duration")
            if "forced_march" in operation["params"] and type(operation["params"]["forced_march"]) is not bool:
                issues.append("military: invalid persisted forced-march order")
            if "transport_mode" in operation["params"] and (not isinstance(operation["params"]["transport_mode"], str) or operation["params"]["transport_mode"] not in {"land", "sea"}):
                issues.append("military: invalid persisted transport mode")
            recipient = operation["params"].get("recipient_formation")
            if recipient is not None and (not isinstance(recipient, str) or recipient not in state["formations"] or
                state["formations"][recipient]["actor"] != operation["actor"] or recipient in operation["formations"]):
                issues.append("military: invalid reinforcement recipient reference")
            issues.extend(_embarkation_issues(world, operation))
            if operation["status"] in ACTIVE:
                for formation in operation["formations"]:
                    if formation in allocated:
                        issues.append("military: formation assigned to multiple active operations")
                    allocated[formation] = key
            dates = [canonical_date(operation[k]) for k in ("created_at", "issued_at", "transmitted_at", "received_at", "execution_started_at", "completed_at") if operation[k] is not None]
            if any(d > canonical_date(world.date) for d in dates) or dates != sorted(dates):
                issues.append("military: operation chronology invalid")
            if type(operation["elapsed_days"]) is not int or operation["elapsed_days"] < 0 or not 0 <= _number(operation["progress"], -1) <= 1:
                issues.append("military: impossible operation progress")
            if operation["execution_started_at"] is not None and operation["elapsed_days"] > (canonical_date(world.date) - canonical_date(operation["execution_started_at"])).days:
                issues.append("military: operation progress exceeds elapsed time")
            if not isinstance(operation["results"], list) or len(set(operation["results"])) != len(operation["results"]):
                issues.append("military: repeated military result")
            elif any(r not in state["loss_receipts"] or state["loss_receipts"][r]["result"].get("operation_id") != key for r in operation["results"]):
                issues.append("military: nonexistent or unrelated military result")
        for key, formation in audit_items(state["formations"]):
            journey = formation.get("journey")
            if journey is None:
                continue
            if journey["operation_id"] not in state["operations"] or key not in state["operations"][journey["operation_id"]]["formations"]:
                issues.append("military: journey has no actual operation/formation assignment")
            if journey["origin"] not in world.regions or journey["destination"] not in world.regions or not isinstance(journey["legs"], list):
                issues.append("military: journey region/path invalid")
            if type(journey["leg_index"]) is not int or not 0 <= journey["leg_index"] <= len(journey["legs"]):
                issues.append("military: impossible journey leg")
            if not 0 <= _number(journey["distance_travelled_km"], -1) <= sum(_number(leg.get("distance_km")) for leg in journey["legs"]) + 1e-7:
                issues.append("military: impossible cumulative journey distance")
            if journey["leg_index"] < len(journey["legs"]) and not 0 <= _number(journey["leg_progress_km"], -1) <= _number(journey["legs"][journey["leg_index"]].get("distance_km")) + 1e-7:
                issues.append("military: impossible journey progress")
            for leg in journey["legs"]:
                link = state["links"].get(leg["link"])
                if link is None or {link["origin"], link["destination"]} != {leg["from"], leg["to"]} or leg["mode"] != link["mode"]:
                    issues.append("military: journey uses nonexistent transport connection")
        for key, message in audit_items(state["commands"]):
            if key != message["id"] or message["message_kind"] not in {"command", "report"} or message["operation_id"] not in state["operations"] or message["status"] not in {"queued", "delivered"}:
                issues.append("military: invalid communicated message")
            created, issued, sent, due, observed = (canonical_date(message[k]) for k in ("created_at", "issued_at", "transmitted_at", "expected_received_at", "as_of"))
            if not observed <= created <= issued <= sent <= due or (due - sent).days != message["communication_delay"]:
                issues.append("military: message dates/delay invalid")
            if type(message["sequence"]) is not int or message["sequence"] <= 0 or type(message["communication_delay"]) is not int or message["communication_delay"] < 0:
                issues.append("military: invalid communication sequence/delay")
            if message["status"] == "delivered" and (message["received_at"] is None or not due <= canonical_date(message["received_at"]) <= canonical_date(world.date) or type(message["belief_accepted"]) is not bool):
                issues.append("military: invalid message receipt")
            if message["status"] == "queued" and (message["received_at"] is not None or message["belief_accepted"] is not None):
                issues.append("military: unreceived message claims delivery")
            if message["message_kind"] == "command" and message["data"].get("intent") not in INTENTS:
                issues.append("military: invalid stored command intent")
            if message["message_kind"] == "command":
                op = state["operations"][message["operation_id"]]
                if message["issuer"] != op["actor"] and message["issuer"] not in persons or message["recipient"] is not None and message["recipient"] not in persons:
                    issues.append("military: command has nonexistent issuer/recipient")
                if message["data"].get("intent") == "withdraw" and message["data"].get("target") not in world.regions:
                    issues.append("military: withdrawal has nonexistent destination")
            if message["message_kind"] == "report":
                if message["recipient"] not in (getattr(world, "npc_state", None) or {}).get("persons", {}) or not isinstance(message["data"].get("facts"), dict):
                    issues.append("military: invalid military observation recipient/facts")
                data, memory = message["data"], message["memory"]
                if data.get("event_id") != "military-observation:" + message["operation_id"] or data.get("category") != "custom:military_event" or data.get("day") != message["as_of"]:
                    issues.append("military: military observation identity/date invalid")
                if not isinstance(message["source"], str) or not message["source"] or any(
                    not 0 <= _number(message[name], -1) <= 1 for name in ("accuracy", "confidence", "reliability")):
                    issues.append("military: military report quality invalid")
                if not isinstance(memory, dict) or set(memory) != {"interpretation", "salience", "personal_impact", "group_impact", "emotional_valence", "tags", "enduring"}:
                    issues.append("military: malformed queued report memory")
                elif not isinstance(memory["interpretation"], dict) or not isinstance(memory["tags"], list) or not all(isinstance(tag, str) for tag in memory["tags"]) or type(memory["enduring"]) is not bool or not 0 <= _number(memory["salience"], -1) <= 1 or any(
                    not -1 <= _number(memory[name], -2) <= 1 for name in ("personal_impact", "group_impact", "emotional_valence")):
                    issues.append("military: invalid queued report memory values")
        previous = getattr(old, "military_state", None) if old is not None else None
        if previous:
            for key, message in audit_items(previous["commands"]):
                current = state["commands"].get(key)
                if current is None or any(current.get(k) != v for k, v in message.items() if k not in {"status", "received_at", "belief_accepted", "execution_at"}):
                    issues.append("military: communicated observation/command history changed")
                elif message["status"] == "delivered" and (current["status"] != "delivered" or current["received_at"] != message["received_at"] or current["belief_accepted"] != message["belief_accepted"]):
                    issues.append("military: delivered history changed")
    except (KeyError, TypeError, ValueError, AttributeError):
        issues.append("military: malformed operation or command state")
    return issues
