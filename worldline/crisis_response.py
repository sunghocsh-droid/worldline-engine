from .incremental_audit import items as audit_items, json_check as audit_json
"""Trusted crisis communications and responses, independent of interfaces.

Facts stay in the event table. Only a delivered, frozen report enters an NPC's
view; political judgements use the existing pure decision engine. Real spending
uses Economy v3's finite financing and existing military labour allocations.
"""
import copy
import math
from datetime import timedelta

from .models import canonical_date
from . import credit, guardrails, npc, npc_decision, npc_state, order_execution

VERSION = "1"
DISASTERS = {"drought", "flood", "storm", "typhoon", "earthquake", "fire", "extreme_cold", "extreme_heat", "bad_harvest", "food_shortage", "famine"}
INTERNATIONAL = {"border_tension", "border_incident", "patrol_incident", "diplomatic_crisis", "ultimatum", "partial_mobilization", "full_mobilization", "armed_clash", "state_of_war", "interstate_crisis"}
SOCIAL = {"petition", "protest", "strike", "riot", "tax_resistance", "local_uprising_precursor", "civil_unrest", "labour_unrest", "separatist_agitation", "autonomy_demand"}
FINANCIAL = {"fiscal_crisis", "fiscal_stress", "financial_crisis", "failed_payment", "payment_failure", "recession"}


def _state(world):
    state = getattr(world, "crisis_state", None)
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Enable a supported Crisis & Event Engine first")
    if not world.npc_state:
        raise ValueError("Crisis responses require NPC state")
    return state


def _event(world, event_id):
    state = _state(world)
    if event_id not in state["events"]:
        raise ValueError("Unknown crisis event")
    return state["events"][event_id]


def _number(value, default=0):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else default


def _clip(value, low=0, high=1):
    return min(high, max(low, value))


def _id(world, kind):
    from .crisis_state import next_id
    return next_id(world, kind)


def communication_delay(world, country, region=None):
    """Days from era baseline and diminishing infrastructure improvement.

    This describes a reporting journey, not instantaneous shared national truth.
    Direct witnesses may explicitly request zero days in queue_report.
    """
    if country not in world.countries:
        raise ValueError("Unknown communication country")
    if region is not None and region not in world.regions:
        raise ValueError("Unknown communication region")
    e = world.economy["countries"][country]
    base = {"preindustrial": 14, "early_industrial": 7, "industrial": 3, "advanced_industrial": 1}.get(e["profile"], 7)
    regions = [region] if region is not None else [r for r in world.regions if world.regions[r].owner == country]
    if not regions:
        return base
    infra = [world.regions[r].infrastructure for r in regions]
    telegraph = sum(max(0, _number(v.get("communications", v.get("telegraph", 0)))) for v in infra) / len(infra)
    transport = sum(max(0, _number(v.get("roads", 0))) + max(0, _number(v.get("railways", 0))) for v in infra) / len(infra)
    return max(1, math.ceil(base / (1 + telegraph / 40 + transport / 120)))


def _distort(facts, exaggeration, distortion):
    result = copy.deepcopy(facts)
    # Only explicitly quantitative risk news is multiplied. IDs, dates and facts
    # about allocation never silently become numerical economic truth.
    for field in ("severity", "threat", "alleged_casualties", "enemy_strength", "damage", "loss_estimate"):
        if type(result.get(field)) in (int, float):
            result[field] *= exaggeration
            if field in ("severity", "threat"):
                result[field] = _clip(result[field])
    result.update(copy.deepcopy(distortion))
    return npc_state._json(result)


def queue_report(world, event_id, actor, *, delay=None, source="regional crisis report", reliability=.8,
                 accuracy=.8, confidence=.8, exaggeration=1, intentional_distortion=None,
                 interpretation=None, salience=None, personal_impact=None, group_impact=0,
                 emotional_valence=None, direct=False, record_id=None, sent_at=None, observed_facts=None):
    """Freeze an actor-specific observation now; knowledge appears on receipt.

    record_id is used only for separately recorded government performance. The
    natural occurrence itself is custom:crisis_event, never ruler performance.
    A correction is a new message about the same event and replaces that memory.
    """
    state, event = _state(world), _event(world, event_id)
    person = world.npc_state["persons"].get(actor)
    if person is None:
        raise ValueError("Unknown report recipient")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in (reliability, accuracy, confidence)):
        raise ValueError("Report confidence, accuracy and reliability must be between zero and one")
    if type(exaggeration) not in (int, float) or not math.isfinite(exaggeration) or exaggeration < 0:
        raise ValueError("Invalid report exaggeration")
    distortion = intentional_distortion or {}
    if not isinstance(distortion, dict) or interpretation is not None and not isinstance(interpretation, dict):
        raise ValueError("Report distortion and interpretation must be mappings")
    if observed_facts is not None and not isinstance(observed_facts, dict):
        raise ValueError("Explicit observations must be a mapping")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A report needs a source")
    sent = canonical_date(sent_at or world.date)
    if sent < canonical_date(world.date):
        raise ValueError("Cannot send a newly created report into the past")
    if delay is None:
        delay = 0 if direct else communication_delay(world, person["country"], event.get("region"))
    if type(delay) is not int or delay < 0:
        raise ValueError("Communication delay must be nonnegative whole days")
    if record_id is not None:
        record = world.npc_state["records"].get(record_id)
        if record is None:
            raise ValueError("Unknown ruler evidence record")
        if record["facts"].get("event_id") != event_id:
            raise ValueError("Ruler evidence does not belong to the reported crisis")
        data = {"event_id": record_id, "day": record["day"], "subject": record["subject"],
                "category": record["category"], "facts": _distort(record["facts"], exaggeration, distortion)}
    else:
        data = {"event_id": event_id, "day": event["start_date"], "subject": f"crisis:{event_id}",
            "category": "custom:crisis_event", "facts": _distort({**copy.deepcopy(event["actual_facts"] if observed_facts is None else observed_facts),
                "event_type": event["event_type"], "severity": event["severity"], "region": event.get("region"),
                "countries": list(event["countries"]), "stage": event.get("stage", event.get("escalation_level", 0))},
                exaggeration, distortion)}
    local = direct or person.get("region") == event.get("region") and person.get("region") is not None
    impact = -.7 * event["severity"] if local else 0
    memory = {"salience": (.85 if local else .25) if salience is None else salience,
        "personal_impact": impact if personal_impact is None else personal_impact, "group_impact": group_impact,
        "emotional_valence": -.5 * event["severity"] if emotional_valence is None else emotional_valence,
        "interpretation": copy.deepcopy(interpretation or {"lesson": "local exposure" if local else "reported crisis", "security": -.15}),
        "tags": ["crisis", event["event_type"]], "enduring": local and event["severity"] > .7}
    if any(type(memory[k]) not in (int, float) or not math.isfinite(memory[k]) or not (0 if k == "salience" else -1) <= memory[k] <= 1
           for k in ("salience", "personal_impact", "group_impact", "emotional_valence")):
        raise ValueError("Invalid report memory strengths")
    message_id = _id(world, "message")
    value = {"message_id": message_id, "event_id": event_id, "actor": actor,
        "created_at": world.date, "sent_at": sent.isoformat(), "received_at": None,
        "delivery_date": (sent + timedelta(days=delay)).isoformat(), "communication_delay": delay,
        "as_of": record["day"] if record_id is not None else world.date, "source": source, "reliability": reliability,
        "accuracy": accuracy, "confidence": confidence, "exaggeration": exaggeration,
        "intentional_distortion": copy.deepcopy(distortion), "audience": [actor],
        "status": "queued", "belief_accepted": None, "data": data, "memory": memory}
    state["messages"][message_id] = value
    return copy.deepcopy(value)


def _observation_order(message):
    # IDs are allocated numerically; lexical order would place message 10 before
    # message 9 and make an older same-day report overwrite its correction.
    suffix = message["message_id"].rsplit("-", 1)[-1]
    sequence = int(suffix) if suffix.isdigit() else 0
    return (message["as_of"], message["created_at"], message["sent_at"], sequence)


def deliver_due(world):
    state, events, latest, due = _state(world), [], {}, []
    # A single pass over retained communication history also works after load;
    # no separate index can drift away from the saved messages.
    for message in state["messages"].values():
        key = (message["actor"], message["data"]["event_id"])
        if message["status"] == "delivered" and message.get("belief_accepted") is not False:
            latest[key] = max(latest.get(key, _observation_order(message)), _observation_order(message))
        elif message["status"] == "queued" and message["delivery_date"] <= world.date:
            due.append(message)
    for message in sorted(due, key=lambda m: (m["delivery_date"], _observation_order(m))):
        message_id, actor, data = message["message_id"], message["actor"], message["data"]
        key, order = (actor, data["event_id"]), _observation_order(message)
        accepted = key not in latest or order > latest[key]
        if accepted:
            npc_state.receive_report(world.npc_state, actor, f"event:{data['event_id']}", data,
                message["as_of"], message["source"], message["accuracy"], message["confidence"], message["reliability"])
            world.npc_state["reports"][actor][f"event:{data['event_id']}"]["received_at"] = world.date
            npc_state.remember(world.npc_state, actor, data["event_id"], reliability=message["reliability"], **message["memory"])
            # NPC's existing memory API defaults this to report.as_of. A stale
            # observation remains old news, but its memory starts on receipt.
            world.npc_state["memories"][actor][-1]["received_date"] = world.date
            latest[key] = order
            world.npc_state["adapter"]["pending"].setdefault(actor, []).append("crisis report")
        message.update(status="delivered", received_at=world.date, belief_accepted=accepted)
        events.append({"system": "crisis", "kind": "report_delivered", "event_id": message["event_id"],
            "message_id": message_id, "actor": actor, "observed_data": copy.deepcopy(data),
            "source": message["source"], "communication_delay": message["communication_delay"],
            "as_of": message["as_of"], "received_at": world.date, "belief_accepted": accepted})
    return events


def queue_command(world, event_id, actor, command, *, delay=None, sender=None, source="government command"):
    """Schedule a communicated standing order without rewriting event knowledge.

    Delivery is an information hook, not a national-policy execution shortcut.
    The receiving local officer can obey, delay or propose a limited action under
    existing authority; simply sending a command creates no troops or financing.
    """
    if not isinstance(command, dict) or not isinstance(command.get("intent"), str) or not command["intent"].strip():
        raise ValueError("A communicated command needs an explicit intent")
    if sender is not None and sender not in world.npc_state["persons"]:
        raise ValueError("Unknown command sender")
    message = queue_report(world, event_id, actor, delay=delay, source=source, salience=.5,
                           personal_impact=0, emotional_valence=0, interpretation={"lesson": "received institutional order"})
    stored = _state(world)["messages"][message["message_id"]]
    stored.update(message_kind="command", sender=sender, command=npc_state._json(command))
    stored["data"].update(event_id=event_id + ":command", category="custom:crisis_command",
        facts={"crisis_event_id": event_id, "central_intent": command["intent"], "command": npc_state._json(command)})
    stored["memory"]["tags"] = ["crisis", "command"]
    return copy.deepcopy(stored)


def _known(view, event_id):
    for data in view.get("beliefs", {}).values():
        if isinstance(data, dict) and data.get("event_id") == event_id and isinstance(data.get("facts"), dict):
            return data["facts"]
    return None


def _informed(world, actor, event_id):
    """Check delivered knowledge without constructing an entire decision view.

    This adapter reads only that actor's dated reports. It neither reveals
    event truth nor grants access merely because a crisis exists.
    """
    for report in world.npc_state["reports"].get(actor, {}).values():
        data = report["data"]
        if report["as_of"] <= world.date and isinstance(data, dict) and data.get("event_id") == event_id and isinstance(data.get("facts"), dict):
            return True
    return False


def _known_command(view, event_id):
    return next((data["facts"] for data in view.get("beliefs", {}).values() if isinstance(data, dict)
        and data.get("category") == "custom:crisis_command" and isinstance(data.get("facts"), dict)
        and data["facts"].get("crisis_event_id") == event_id), {})


def _responses(event_type):
    if event_type in INTERNATIONAL:
        return ("mediate", "withdraw", "ultimatum", "mobilize", "ignore")
    if event_type in ("epidemic", "epidemic_introduction"):
        return ("relief", "quarantine", "ignore")
    if event_type in DISASTERS:
        return ("relief", "ignore")
    if event_type in ("fiscal_crisis", "fiscal_stress", "financial_crisis", "failed_payment", "payment_failure", "recession"):
        return ("negotiate", "concession", "ignore")
    return ("negotiate", "concession", "ignore", "limited_repression", "military_deployment")


def _scope(response):
    if response in ("mediate", "withdraw", "ultimatum"):
        return "diplomatic_engagement"
    if response == "mobilize":
        return "war_preparation"
    if response == "military_deployment":
        return "mobilize"
    return "economic_policy"


def _authorized(world, actor, country, response):
    person = world.npc_state["persons"].get(actor)
    gov = world.npc_state["governments"].get(country, {})
    if not person or not person.get("active", True) or person["country"] != country:
        return False
    if actor != gov.get("ruler") and actor not in gov.get("cabinet", []):
        return False
    return _scope(response) in person.get("authority", [])


def _base(world, country):
    c = world.countries[country]
    equipment = sum(s["capital_stock"] for r, v in world.economy["regions"].items()
                    if world.regions[r].owner == country for s in v["sectors"].values())
    return max(1, c.gdp or equipment * world.economy["countries"][country]["calibration"]["productivity"])


def _region(world, event, country):
    region = event.get("region")
    if region in world.regions and world.regions[region].owner == country:
        return region
    return next((r for r in sorted(world.regions) if world.regions[r].owner == country), None)


def _cost(world, event, country, response):
    fraction = {"ignore": 0, "mediate": .0003, "withdraw": .0002, "ultimatum": .0005,
        "negotiate": .0004, "concession": .002, "limited_repression": .001,
        "military_deployment": .003, "relief": .004, "quarantine": .002, "mobilize": .006}[response]
    return math.ceil(_base(world, country) * fraction * (.3 + event["severity"])) if fraction else 0


def _quote(world, event, country, response):
    amount, region = _cost(world, event, country, response), _region(world, event, country)
    if response == "mobilize":
        if region is None:
            quote = guardrails.estimated_fiscal_risk(order_execution.economic_view(world), country, amount, allow_emergency=True)
            quote["physical_feasibility"] = {"feasible": False, "reason": "no domestic recruitment region"}
            quote["manpower"] = 0
        else:
            r = world.economy["regions"][region]
            required = max(1, int(world.regions[region].population * .002 * event["severity"]))
            quote = guardrails.can_fund_war(order_execution.economic_view(world), country, amount, region=region,
                manpower=required, resources={}, allow_emergency=True)
            quote["manpower"] = int(r["labour_state"]["mobilized"]) + required
    else:
        quote = guardrails.estimated_fiscal_risk(order_execution.economic_view(world), country, amount, allow_emergency=True)
        if response in ("limited_repression", "military_deployment"):
            force = sum(a.personnel for a in world.armies.values() if a.owner == country and (response == "military_deployment" or a.region == region))
            force += sum(r["labour_state"]["mobilized"] for rid, r in world.economy["regions"].items()
                         if world.regions[rid].owner == country and (response == "military_deployment" or rid == region))
            quote["physical_feasibility"] = {"feasible": force > 0, "existing_personnel": force,
                "reason": "requires existing armed personnel; no free military labour"}
    quote["region"] = region
    return quote


def responses_for(world, event_id, actor, *, _view=None):
    """Return detached perceived options and an objective execution sidecar.

    An unknown event has no political options. Quote detail never enters decide.
    Opinions may oppose a command; actual response requires a delegated executive.
    """
    event = _event(world, event_id)
    view = npc_state.actor_view(world.npc_state, actor, world.date) if _view is None else _view
    known = _known(view, event_id)
    if known is None:
        return view, [], {}
    country = view["person"]["country"]
    severity = _clip(_number(known.get("severity")))
    economy = view.get("beliefs", {}).get("economy", {})
    economy = economy if isinstance(economy, dict) else {}
    risk = _clip(_number(economy.get("fiscal_risk"), .15))
    scale = max(1, _number(economy.get("revenue"), 1) * 12)
    bias = view["person"]["biases"]
    threat = _clip(_number(known.get("threat"), severity))
    diplomacy = view.get("beliefs", {}).get("diplomacy", {})
    diplomacy = diplomacy if isinstance(diplomacy, dict) else {}
    own_strength = max(1, _number(known.get("own_strength"), _number(diplomacy.get("own_strength"), 1)))
    enemy_strength = max(1, _number(known.get("enemy_strength"), _number(diplomacy.get("enemy_strength"), own_strength)))
    outcomes = {
        "ignore": {"security": -.3 * severity, "prosperity": -.25 * severity, "fiscal": .15, "prestige": -.15 * severity},
        "relief": {"security": .15 * severity, "prosperity": .75 * severity, "prestige": .25 * severity, "fiscal": -.15},
        "quarantine": {"security": .35 * severity, "prosperity": .4 * severity, "fiscal": -.12, "ideology": -.08},
        "negotiate": {"security": .4 * severity, "prosperity": .4 * severity, "fiscal": .1, "prestige": -.12},
        "concession": {"security": .55 * severity, "prosperity": .5 * severity, "fiscal": -.2, "prestige": -.25, "ideology": -.25},
        "limited_repression": {"security": .55 * threat, "prosperity": -.25, "fiscal": -.1, "prestige": .25, "ideology": .3},
        "military_deployment": {"security": .75 * threat, "prosperity": -.4, "fiscal": -.3, "prestige": .4, "ideology": .3},
        "mediate": {"security": .65 * severity, "prosperity": .25, "fiscal": .1, "prestige": -.12},
        "withdraw": {"security": .55 * severity - .35 * threat, "prosperity": .3, "fiscal": .2, "prestige": -.45},
        "ultimatum": {"security": .6 * threat, "prosperity": -.3, "fiscal": -.25, "prestige": .65, "ideology": .3},
        "mobilize": {"security": .8 * threat, "prosperity": -.45, "fiscal": -.5, "prestige": .55, "ideology": .25},
    }
    options, actual = [], {}
    for response in _responses(str(known.get("event_type", "unknown"))):
        quote = _quote(world, event, country, response)
        # Objective GDP and objective damage never determine believed costs.
        fraction = {"ignore": 0, "mediate": .0003, "withdraw": .0002, "ultimatum": .0005,
            "negotiate": .0004, "concession": .002, "limited_repression": .001,
            "military_deployment": .003, "relief": .004, "quarantine": .002, "mobilize": .006}[response]
        perceived_base = max(1, _number(economy.get("gdp"), scale / .2))
        perceived = {"cost": perceived_base * fraction * (.3 + severity) * (1 - .25 * bias.get("optimism", 0)),
            "risk": _clip(risk * (1 - .35 * bias.get("overconfidence", 0))), "cost_scale": scale,
            "basis": "actor's dated crisis and economic reports"}
        financial = {k: {"feasible": quote[k].get("feasible", quote[k].get("possible", False))}
                     for k in ("physical_feasibility", "normal_financial_feasibility", "emergency_financial_feasibility")}
        financial["perceived_state"] = perceived
        tags = ["crisis", response, str(known.get("event_type", "unknown"))]
        if response in ("ultimatum", "mobilize", "military_deployment"):
            tags += ["war", "war_preparation"]
        options.append({"id": response, "kind": "war_preparation" if response == "mobilize" else "crisis_response",
            "country": country, "event_id": event_id, "response": response, "tags": tags,
            "outcomes": copy.deepcopy(outcomes[response]), "financial": financial,
            "belief_context": {"threat": threat, "own_strength": own_strength, "enemy_strength": enemy_strength},
            # Cabinet members may advise on policies they cannot personally
            # execute. The authoritative respond adapter checks the real scope.
            "institutional_feasibility": {"feasible": view["person"].get("active", True)},
            "required_execution_authority": [_scope(response)]})
        actual[response] = quote
    return view, options, actual


def schedule_pending(world, event_id, country, *, deadline=None, available_responses=None, consequences_if_ignored=None):
    state, event = _state(world), _event(world, event_id)
    if country not in state["player_countries"] or country not in event["countries"]:
        raise ValueError("Pending political decisions require an involved player country")
    for pending in state["pending_decisions"].values():
        if pending["event_id"] == event_id and pending["country"] == country and pending["status"] == "pending":
            return copy.deepcopy(pending)
    deadline = deadline or (canonical_date(world.date) + timedelta(days=14)).isoformat()
    if canonical_date(deadline) < canonical_date(world.date):
        raise ValueError("Decision deadline precedes its creation")
    options = list(available_responses or _responses(event["event_type"]))
    if not options or any(r not in _responses(event["event_type"]) for r in options):
        raise ValueError("Unsupported player response")
    pending_id = _id(world, "pending")
    pending = {"pending_id": pending_id, "event_id": event_id, "country": country,
        "requires_player_decision": True, "created_at": world.date, "deadline": deadline,
        "available_responses": options, "consequences_if_ignored": copy.deepcopy(consequences_if_ignored or [
            {"kind": "ignore_response", "meaning": "underlying risk persists and the counterpart can escalate"}]),
        "status": "pending", "resolution": None}
    state["pending_decisions"][pending_id] = pending
    event.update(requires_player_decision=True, deadline=deadline, available_responses=options,
                 consequences_if_ignored=copy.deepcopy(pending["consequences_if_ignored"]))
    return copy.deepcopy(pending)


def _enqueue(world, event_id, kind, payload):
    from .crisis_effects import enqueue
    return enqueue(world, event_id, kind, payload)


def _pressures(event):
    if event["event_type"] in INTERNATIONAL:
        return ("border_tension", "diplomatic_crisis", "interstate_war")
    if event["event_type"] in ("epidemic", "epidemic_introduction"):
        return ("epidemic", "civil_unrest")
    if event["event_type"] in DISASTERS:
        return ("famine", "civil_unrest")
    if event["event_type"] in ("fiscal_crisis", "fiscal_stress", "financial_crisis", "failed_payment", "payment_failure", "recession"):
        return ("fiscal_crisis", "financial_crisis", "civil_unrest")
    return ("civil_unrest", "labour_unrest")


def _paid(world, country, quote, response):
    old_cash, old_debt = world.countries[country].treasury, world.countries[country].debt
    financing = credit.finance_amount(world, country, quote["amount"], allow_emergency=True,
                                     purpose=f"crisis_response:{response}")
    f = world.economy["countries"][country]["finance"]
    f["interim_net_cashflow"] = f.get("interim_net_cashflow", 0) + world.countries[country].treasury - old_cash
    f["interim_debt_change"] = f.get("interim_debt_change", 0) + world.countries[country].debt - old_debt
    f["reserves"] = world.countries[country].treasury
    return financing


def respond(world, event_id, country, response, *, actor=None, player=False, timeout=False):
    """Execute an explicit choice; financial risk alone never cancels politics."""
    state, event = _state(world), _event(world, event_id)
    if country not in event["countries"] or country not in world.countries:
        return {"status": "rejected", "reason": "country is not a crisis party"}
    if not event["ongoing"]:
        return {"status": "rejected", "reason": "crisis is already closed"}
    if response not in _responses(event["event_type"]):
        return {"status": "rejected", "reason": "unsupported response for crisis family"}
    pending = next((p for p in state["pending_decisions"].values() if p["event_id"] == event_id and p["country"] == country and p["status"] == "pending"), None)
    if player:
        if country not in state["player_countries"]:
            return {"status": "rejected", "reason": "country is not player controlled"}
        if pending and response not in pending["available_responses"]:
            return {"status": "rejected", "reason": "response is not offered to the player"}
    elif not timeout:
        if country in state["player_countries"]:
            return {"status": "rejected", "reason": "important political choice remains with player"}
        if not _authorized(world, actor, country, response):
            return {"status": "rejected", "reason": "NPC lacks delegated national authority"}
        if not _informed(world, actor, event_id):
            return {"status": "rejected", "reason": "NPC has not received the event report"}
    elif response != "ignore" or pending is None or pending["deadline"] > world.date:
        return {"status": "rejected", "reason": "no expired player decision"}
    if any(r["country"] == country for r in event["responses"]):
        return {"status": "rejected", "reason": "this crisis stage already has the country's response"}
    quote = _quote(world, event, country, response)
    if not quote["physical_feasibility"].get("feasible", quote["physical_feasibility"].get("possible", False)) or not (
        quote["normal_financial_feasibility"]["feasible"] or quote["emergency_financial_feasibility"]["feasible"]):
        return {"status": "rejected", "reason": "actual personnel or accessible financing unavailable", "actual": quote}
    if response == "mobilize":
        # Shared order validator owns labour withdrawal and real financing.
        target = next((c for c in event["countries"] if c != country), None)
        if target is None:
            return {"status": "rejected", "reason": "mobilization requires a foreign crisis party"}
        option = {"country": country, "kind": "war_preparation", "params": {"target": target,
            "region": quote["region"], "amount": quote["amount"], "manpower": quote["manpower"],
            "resources": {}, "allow_emergency": True}}
        if player:
            financing = _paid(world, country, quote, response)
            from .labour import mobilize
            mobilize(world, country, quote["region"], quote["manpower"])
        else:
            result = npc.execute_intent(world, actor, option)
            if result["status"] != "accepted":
                return result
            financing = result
    else:
        financing = _paid(world, country, quote, response)
    fiscal_aftermath = {}
    if event["event_type"] in FINANCIAL:
        credit_state = world.economy["countries"][country]["credit"]
        if response == "negotiate" and (credit_state["distress_state"] == "default" or credit_state["arrears"]):
            credit.restructure(world, country)
            fiscal_aftermath["restructuring_requested"] = True
        elif response == "concession" and credit_state["arrears"]:
            paid_arrears = min(credit_state["arrears"], quote["amount"])
            credit_state["arrears"] -= paid_arrears
            credit_state["arrears_paid"] += paid_arrears
            fiscal_aftermath["existing_arrears_paid"] = paid_arrears
            credit.refresh(world, country)
    strength = 0
    if response in ("relief", "quarantine", "negotiate", "concession", "mediate", "withdraw"):
        skill = world.npc_state["persons"].get(actor, {}).get("abilities", {}).get("administration", .5)
        strength = .25 + .4 * skill if response in ("relief", "quarantine") else .5 if response in ("negotiate", "mediate") else .65
        if response in ("relief", "quarantine"):
            _enqueue(world, event_id, "mitigation", {"event_id": event_id, "strength": strength, "response": response})
        for category in _pressures(event):
            _enqueue(world, event_id, "pressure_delta", {"category": category, "delta": -(8 + 20 * strength),
                "country": country, "region": event.get("region"),
                **({"pair": list(sorted(event["countries"][:2]))} if event["event_type"] in INTERNATIONAL and len(event["countries"]) >= 2 else {})})
        calm_choices = {"negotiate", "concession", "mediate", "withdraw"}
        answered = {r["country"]: r["response"] for r in event["responses"]}
        answered[country] = response
        joint_agreement = all(answered.get(c) in calm_choices for c in event["countries"])
        fiscal_recovered = event["event_type"] not in FINANCIAL or world.economy["countries"][country]["credit"]["distress_state"] == "normal"
        unilateral_withdrawal = response == "withdraw" and event["event_type"] != "state_of_war"
        if response in calm_choices and fiscal_recovered and (event["event_type"] not in INTERNATIONAL or joint_agreement or unilateral_withdrawal):
            _enqueue(world, event_id, "event_resolution", {"event_id": event_id, "resolution_state": "contained" if response == "withdraw" else "resolved"})
            if event["event_type"] == "state_of_war" and joint_agreement and len(event["countries"]) >= 2:
                _enqueue(world, event_id, "diplomacy", {"a": event["countries"][0], "b": event["countries"][1],
                    "status": "armistice", "contact": {"kind": "mutual ceasefire", "date": world.date},
                    "trust_delta": .02})
        if response in ("mediate", "withdraw") and len(event["countries"]) >= 2:
            _enqueue(world, event_id, "diplomacy", {"a": event["countries"][0], "b": event["countries"][1],
                "trust_delta": .05, "dispute_delta": -.08, "contact": {"kind": response, "date": world.date}})
    if response in ("limited_repression", "military_deployment", "ultimatum", "mobilize", "ignore"):
        escalation = {"limited_repression": .35, "military_deployment": .6, "ultimatum": .5, "mobilize": .65, "ignore": .15}[response]
        for category in _pressures(event):
            _enqueue(world, event_id, "pressure_delta", {"category": category, "delta": escalation * 18,
                "country": country, "region": event.get("region"),
                **({"pair": list(sorted(event["countries"][:2]))} if event["event_type"] in INTERNATIONAL and len(event["countries"]) >= 2 else {})})
        _enqueue(world, event_id, "trigger", {"kind": "response_escalation", "response": response,
            "country": country, "actor": actor, "severity": escalation, "parent_event": event_id})
    response_record = {"country": country, "actor": actor, "response": response, "date": world.date,
        "player": player, "timeout": timeout, "actual_cost": quote["amount"], "actual_risk": quote["true_state"]["risk"],
        "financing": copy.deepcopy(financing), "mitigation": strength}
    if fiscal_aftermath:
        response_record["fiscal_aftermath"] = fiscal_aftermath
    event["responses"].append(response_record)
    if pending:
        pending.update(status="expired" if timeout else "answered", resolution=copy.deepcopy(response_record))
    logs = [{"system": "crisis", "kind": "crisis_response", "event_id": event_id, "response": copy.deepcopy(response_record)}]
    # Occurrence is never political blame. This separate record is the response's
    # bounded observed execution result; later event outcomes can supply a new one.
    ruler = world.npc_state["governments"].get(country, {}).get("ruler")
    if ruler:
        performance = strength if strength else -.25 * event["severity"] if response == "ignore" else 0
        category = "disaster_response" if event["event_type"] in DISASTERS | {"epidemic", "epidemic_introduction"} else "administrative_performance"
        evidence = npc.record_outcome(world, ruler, category,
            {"performance": performance, "event_id": event_id, "response": response,
                "response_capacity_delivered": strength, "damage_prevented_is_estimate": True},
            source=f"paid crisis response:{event_id}", observers=[])
        logs.append(evidence)
        for recipient, person in sorted(world.npc_state["persons"].items()):
            if person["country"] == country:
                queue_report(world, event_id, recipient, record_id=evidence["record_id"],
                    source="government response account", salience=.4, emotional_valence=performance,
                    personal_impact=0, interpretation={"lesson": "government crisis response", "prosperity": performance})
    return {"status": "accepted", "event_id": event_id, "response": response_record, "events": logs}


def record_response_outcome(world, event_id, country, performance, *, facts=None, source="measured crisis aftermath"):
    """Attach an external measured outcome to a response, never the occurrence.

    The root event/effect pipeline can call this after delayed damage is realized.
    It may report failed relief or successful prevention without rewriting the
    earlier paid-response-capacity evidence or adopting an NPC's claimed lesson.
    """
    event = _event(world, event_id)
    if country not in event["countries"] or not any(r["country"] == country for r in event["responses"]):
        raise ValueError("Ruler outcome evidence requires that country's actual response")
    if type(performance) not in (int, float) or not math.isfinite(performance) or not -1 <= performance <= 1 or facts is not None and not isinstance(facts, dict):
        raise ValueError("Invalid measured crisis outcome")
    ruler = world.npc_state["governments"].get(country, {}).get("ruler")
    if ruler is None:
        return None
    result = npc.record_outcome(world, ruler,
        "disaster_response" if event["event_type"] in DISASTERS | {"epidemic", "epidemic_introduction"} else "administrative_performance",
        {**copy.deepcopy(facts or {}), "event_id": event_id, "performance": performance, "measured_aftermath": True},
        source=source, observers=[])
    for actor, person in sorted(world.npc_state["persons"].items()):
        if person["country"] == country:
            queue_report(world, event_id, actor, record_id=result["record_id"], source=source,
                         salience=.5, emotional_valence=performance, personal_impact=0,
                         interpretation={"lesson": "measured government response outcome", "prosperity": performance})
    return result


def _local(world, event, actor):
    person = world.npc_state["persons"][actor]
    if not person.get("active", True) or "local_action" not in person.get("authority", []) or not _at_incident_border(world, event, person):
        return None
    region = person["region"]
    if region not in world.regions or world.regions[region].owner != person["country"] or person["country"] not in event["countries"]:
        return None
    if actor in event.setdefault("local_evaluated", []):
        return None
    # Preserve the original local_evaluated initialization, but unrelated,
    # uninformed or physically unavailable local attempts need no large view.
    if event["event_type"] not in INTERNATIONAL or not _informed(world, actor, event["event_id"]):
        return None
    delay = communication_delay(world, person["country"], region)
    if delay < 2 or not any(a.owner == person["country"] and a.region == region and a.personnel > 0 for a in world.armies.values()):
        return None
    view = npc_state._actor_read_view(world.npc_state, actor, world.date)
    known = _known(view, event["event_id"])
    threat = _clip(_number(known.get("threat"), known.get("severity", 0)))
    scopes = ["local_action"]
    received_command = _known_command(view, event["event_id"])
    avoid = received_command.get("central_intent") in ("avoid_conflict", "withdraw", "hold_fire")
    duty = person["traits"]["institutional_loyalty"]
    options = [{"id": "await_orders", "kind": "local_action", "tags": ["local", "continuity"],
        "required_authority": scopes, "outcomes": {"security": -.35 * threat, "fiscal": .15, "prestige": -.15,
            "institutional_duty": .4 * duty if avoid else 0}},
        {"id": "limited_patrol", "kind": "local_action", "tags": ["local", "war", "war_preparation"],
         "required_authority": scopes, "belief_context": {"threat": threat},
         "outcomes": {"security": .5 * threat, "prestige": .4 * threat, "fiscal": -.12,
            "institutional_duty": -.8 * duty if avoid else 0}}]
    from .crisis_state import stream
    with stream(world, f"local:{event.get('rng_key', event['event_id'])}:{actor}") as rng:
        decision = npc_decision.decide(view, options, rng)
    event["local_evaluated"].append(actor)
    if decision["selected"] != "limited_patrol":
        return {"system": "crisis", "kind": "local_wait", "event_id": event["event_id"], "actor": actor, "decision": decision}
    central_intent = event["actual_facts"].get("central_intent", "await_authorization")
    intent = {"country": person["country"], "kind": "local_action", "params": {"region": region,
        "action": "local_patrol", "details": {"event_id": event["event_id"], "perceived_threat": threat,
            "received_central_order": received_command.get("central_intent", known.get("central_intent", "not_received"))}}}
    result = npc.execute_intent(world, actor, intent)
    if result["status"] == "accepted":
        _enqueue(world, event["event_id"], "trigger", {"kind": "local_unilateral_action", "actor": actor,
            "country": person["country"], "region": region, "response": "limited_patrol", "severity": threat,
            "communication_delay": delay, "central_order": central_intent,
            "misinformation": any(m["actor"] == actor and m["event_id"] == event["event_id"] and m["status"] == "delivered"
                and m["data"].get("facts") == known and (bool(m.get("intentional_distortion")) or m.get("exaggeration", 1) != 1)
                for m in _state(world)["messages"].values()),
            "reported_facts": copy.deepcopy(known)})
    return {"system": "crisis", "kind": "local_action", "event_id": event["event_id"], "actor": actor,
            "decision": decision, "execution": result, "national_mobilization": False, "combat_resolved": False}


def _at_incident_border(world, event, person):
    """Opposite-bank officers may act on the same explicitly configured border.

    This does not authorize a distant officer just because their country is a
    party: both delegated locations must be this border's two known regions.
    """
    region = person.get("region")
    if region is None:
        return False
    if region == event.get("region"):
        return True
    return any(region in border["regions"] and event.get("region") in border["regions"]
        and set(border["countries"]) == set(event["countries"])
        for border in _state(world)["borders"].values())


def process_due(world, rng=None):
    """Deliver reports, evaluate relevant local actors, and run informed councils.

    The caller's game RNG is deliberately unused here: named persisted crisis
    streams keep an unrelated report from shifting every country's random draws.
    """
    state, events = _state(world), deliver_due(world)
    from .crisis_state import stream
    for pending in list(state["pending_decisions"].values()):
        if pending["status"] == "pending" and not state["events"][pending["event_id"]]["ongoing"]:
            pending.update(status="cancelled", resolution={"reason": "crisis resolved before political decision", "date": world.date})
            continue
        if pending["status"] == "pending" and pending["deadline"] <= world.date:
            result = respond(world, pending["event_id"], pending["country"], "ignore", timeout=True)
            if result["status"] == "accepted":
                events.extend(result["events"])
    for event_id, event in list(sorted(state["events"].items())):
        if not event["ongoing"]:
            continue
        for actor, person in sorted(world.npc_state["persons"].items()):
            if _at_incident_border(world, event, person):
                result = _local(world, event, actor)
                if result:
                    events.append(result)
        for country in sorted(event["countries"]):
            if any(r["country"] == country for r in event["responses"]):
                continue
            if country in state["player_countries"]:
                if event["event_type"] != "bad_harvest":
                    government = world.npc_state["governments"].get(country, {})
                    central = [a for a in [government.get("ruler"), *government.get("cabinet", [])] if a in world.npc_state["persons"]]
                    if not any(_informed(world, a, event_id) for a in central):
                        continue
                    pending = schedule_pending(world, event_id, country)
                    stored = state["pending_decisions"][pending["pending_id"]]
                    if not stored.get("announced", False):
                        stored["announced"] = True
                        events.append({"system": "crisis", "kind": "player_decision_pending", "event_id": event_id, "pending": pending})
                continue
            gov = world.npc_state["governments"].get(country)
            if not gov or not gov.get("autonomous", False):
                continue
            schedule = event.setdefault("response_review", {})
            new_information = any(e["kind"] == "report_delivered" and e["event_id"] == event_id
                and world.npc_state["persons"][e["actor"]]["country"] == country for e in events)
            if schedule.get(country, world.date) > world.date and not new_information:
                continue
            schedule[country] = (canonical_date(world.date) + timedelta(days=30)).isoformat()
            members = [a for a in dict.fromkeys([gov.get("ruler"), *gov.get("cabinet", [])]) if a in world.npc_state["persons"]]
            decisions, options_by_actor = {}, {}
            for actor in members:
                view = npc_state._actor_read_view(world.npc_state, actor, world.date)
                view, options, actual = responses_for(world, event_id, actor, _view=view)
                if not options:
                    continue
                with stream(world, f"response:{event.get('rng_key', event_id)}:{actor}") as decision_rng:
                    decision = npc_decision.decide(view, options, decision_rng)
                decisions[actor], options_by_actor[actor] = decision, {o["id"]: o for o in options}
                events.append({"system": "crisis", "kind": "crisis_npc_decision", "event_id": event_id,
                    "actor": actor, "decision": decision, "actual_quotes": actual})
            if not decisions:
                continue
            ruler = gov.get("ruler")
            if gov["decision_rule"] == "executive" and ruler in decisions:
                response = decisions[ruler]["selected"]
            else:
                votes = {}
                for actor, decision in decisions.items():
                    selected = decision["selected"]
                    if selected is not None:
                        votes[selected] = votes.get(selected, 0) + npc._vote_weight(world.npc_state, actor)
                response = max(sorted(votes), key=votes.get) if votes else None
            if response is None:
                continue
            executor = next((a for a in [ruler, *members] if _authorized(world, a, country, response)
                             and _informed(world, a, event_id)), None)
            if executor:
                result = respond(world, event_id, country, response, actor=executor)
                if result["status"] == "accepted":
                    events.extend(result["events"])
    return events


def validation_issues(world, previous=None):
    """Persisted report and political-decision invariants for the Auditor."""
    state = getattr(world, "crisis_state", None)
    if state is None:
        return []
    issues = []
    if not isinstance(state, dict) or not isinstance(state.get("messages"), dict) or not isinstance(state.get("pending_decisions"), dict):
        return ["crisis communication/pending decision storage is malformed"]
    persons = world.npc_state.get("persons", {}) if isinstance(world.npc_state, dict) else {}
    events = state.get("events", {})
    def day(value):
        try:
            return canonical_date(value) if isinstance(value, str) else None
        except (ValueError, TypeError):
            return None
    for key, event in audit_items(events):
        if isinstance(event, dict) and "cooldown_until" in event:
            until, end = day(event["cooldown_until"]), day(event.get("end_date"))
            if until is None or end is None or until < end or event.get("ongoing"):
                issues.append(f"crisis invalid event resolution cooldown {key}")
    for key, message in audit_items(state["messages"]):
        if not isinstance(message, dict):
            issues.append(f"crisis invalid message {key}")
            continue
        required = {"message_id", "event_id", "actor", "created_at", "sent_at", "received_at", "delivery_date", "communication_delay",
                    "as_of", "source", "reliability", "accuracy", "confidence", "exaggeration", "intentional_distortion", "audience", "status", "data", "memory"}
        if not required <= set(message):
            issues.append(f"crisis missing message fields {key}")
            continue
        if not all(isinstance(message[k], str) and message[k] for k in ("message_id", "event_id", "actor", "source")):
            issues.append(f"crisis invalid message identifiers/source {key}")
            continue
        if message["message_id"] != key or message["event_id"] not in events or message["actor"] not in persons or message["audience"] != [message["actor"]]:
            issues.append(f"crisis message references unknown event/NPC or mismatched audience {key}")
        dates = [day(message[k]) for k in ("created_at", "sent_at", "delivery_date", "as_of")]
        if any(d is None for d in dates) or not dates[0] <= dates[1] <= dates[2] or dates[3] > dates[0]:
            issues.append(f"crisis invalid message chronology {key}")
        if type(message["communication_delay"]) is not int or message["communication_delay"] < 0 or all(d is not None for d in dates) and (dates[2] - dates[1]).days != message["communication_delay"]:
            issues.append(f"crisis invalid communication delay {key}")
        if message["status"] not in ("queued", "delivered"):
            issues.append(f"crisis invalid message status {key}")
        if "belief_accepted" in message and (message["status"] == "queued" and message["belief_accepted"] is not None or
            message["status"] == "delivered" and type(message["belief_accepted"]) is not bool):
            issues.append(f"crisis invalid report acceptance status {key}")
        if message["status"] == "queued" and message["received_at"] is not None or message["status"] == "delivered" and (
            day(message["received_at"]) is None or dates[2] is not None and day(message["received_at"]) < dates[2] or
            day(message["received_at"]) is not None and day(message["received_at"]) > day(world.date)):
            issues.append(f"crisis message received before delivery or in future {key}")
        if any(type(message[k]) not in (int, float) or not math.isfinite(message[k]) or not 0 <= message[k] <= 1
               for k in ("reliability", "accuracy", "confidence")) or type(message["exaggeration"]) not in (int, float) or not math.isfinite(message["exaggeration"]) or message["exaggeration"] < 0:
            issues.append(f"crisis invalid report quality {key}")
        if not isinstance(message["intentional_distortion"], dict) or not isinstance(message["data"], dict) or not isinstance(message["memory"], dict):
            issues.append(f"crisis malformed report snapshot {key}")
        else:
            try:
                npc_state._validate_event_report(message["data"], message["delivery_date"])
                npc_state._json(message["data"])
            except (ValueError, TypeError, KeyError):
                issues.append(f"crisis malformed event report {key}")
            data, memory = message["data"], message["memory"]
            if not {"event_id", "day", "subject", "category", "facts"} <= set(data) or not all(isinstance(data.get(k), str) for k in ("event_id", "day", "subject", "category")) or not isinstance(data.get("facts"), dict):
                issues.append(f"crisis incomplete event report body {key}")
            expected_memory = {"salience", "personal_impact", "group_impact", "emotional_valence", "interpretation", "tags", "enduring"}
            if set(memory) != expected_memory or any(type(memory.get(k)) not in (int, float) or not math.isfinite(memory[k]) or not (0 if k == "salience" else -1) <= memory[k] <= 1
                for k in ("salience", "personal_impact", "group_impact", "emotional_valence")) or not isinstance(memory.get("interpretation"), dict) or not isinstance(memory.get("tags"), list) or not all(isinstance(t, str) for t in memory.get("tags", [])) or type(memory.get("enduring")) is not bool:
                issues.append(f"crisis invalid queued memory parameters {key}")
            record = world.npc_state.get("records", {}).get(data.get("event_id"), {}) if isinstance(world.npc_state, dict) and isinstance(data.get("event_id"), str) else {}
            if data.get("category") == "custom:crisis_event":
                proper = data.get("event_id") == message["event_id"] and data.get("subject") == "crisis:" + message["event_id"]
            elif data.get("category") == "custom:crisis_command":
                proper = data.get("event_id") == message["event_id"] + ":command" and isinstance(data.get("facts"), dict) and data["facts"].get("crisis_event_id") == message["event_id"]
            else:
                proper = (record.get("facts", {}).get("event_id") == message["event_id"] and data.get("subject") == record.get("subject") and data.get("category") == record.get("category"))
            if not proper:
                issues.append(f"crisis report event/ruler evidence reference mismatch {key}")
    for key, pending in audit_items(state["pending_decisions"]):
        if not isinstance(pending, dict) or not {"pending_id", "event_id", "country", "requires_player_decision", "created_at", "deadline", "available_responses", "consequences_if_ignored", "status", "resolution"} <= set(pending):
            issues.append(f"crisis invalid pending decision {key}")
            continue
        if not all(isinstance(pending[k], str) and pending[k] for k in ("pending_id", "event_id", "country")):
            issues.append(f"crisis invalid player decision identifiers {key}")
            continue
        if pending["pending_id"] != key or pending["event_id"] not in events or pending["country"] not in state.get("player_countries", []) or pending["country"] not in events.get(pending["event_id"], {}).get("countries", []):
            issues.append(f"crisis pending decision has unknown event/player country {key}")
        if pending["requires_player_decision"] is not True or pending["status"] not in ("pending", "answered", "expired", "cancelled"):
            issues.append(f"crisis pending decision has invalid status {key}")
        if day(pending["created_at"]) is None or day(pending["deadline"]) is None or day(pending["created_at"]) > day(pending["deadline"]):
            issues.append(f"crisis invalid player decision deadline {key}")
        choices = pending["available_responses"]
        supported = _responses(events.get(pending["event_id"], {}).get("event_type", "unknown"))
        if not isinstance(choices, list) or not choices or not all(isinstance(v, str) and v in supported for v in choices) or len(set(choices)) != len(choices):
            issues.append(f"crisis invalid pending political options {key}")
        if pending["status"] == "pending" and pending["resolution"] is not None or pending["status"] != "pending" and not isinstance(pending["resolution"], dict):
            issues.append(f"crisis invalid pending decision resolution {key}")
        elif pending["status"] in ("answered", "expired") and (pending["resolution"].get("country") != pending["country"] or pending["resolution"].get("response") not in (choices if pending["status"] == "answered" and isinstance(choices, list) else ["ignore"])):
            issues.append(f"crisis decision resolution disagrees with country/options {key}")
    # Knowledge of a crisis requires a delivered report with matching frozen
    # facts. Objective event existence alone never grants a valid memory.
    if isinstance(world.npc_state, dict):
        delivered, latest = {}, {}
        for m in state["messages"].values():
            if isinstance(m, dict) and m.get("status") == "delivered" and isinstance(m.get("data"), dict):
                delivered.setdefault((m.get("actor"), m["data"].get("event_id")), []).append(m["data"].get("facts"))
                if m.get("belief_accepted") is not False and all(isinstance(m.get(k), str) for k in ("message_id", "as_of", "created_at", "sent_at")):
                    key = (m.get("actor"), m["data"].get("event_id"))
                    if key not in latest or _observation_order(m) > _observation_order(latest[key]):
                        latest[key] = m
        for actor, memories in world.npc_state.get("memories", {}).items():
            for memory in memories:
                relates = memory.get("event_id") in events or memory.get("observed_facts", {}).get("crisis_event_id") in events
                if memory.get("category") in ("custom:crisis_event", "custom:crisis_command") and relates and not any(
                    memory.get("observed_facts") == facts for facts in delivered.get((actor, memory["event_id"]), [])):
                    issues.append(f"crisis NPC remembers an undelivered event truth {actor}:{memory['event_id']}")
                current = latest.get((actor, memory.get("event_id")))
                if current is not None and memory.get("observed_facts") != current["data"].get("facts"):
                    issues.append(f"crisis NPC memory disagrees with latest accepted report {actor}:{memory['event_id']}")
        for (actor, event_id), current in audit_items(latest):
            report = world.npc_state.get("reports", {}).get(actor, {}).get("event:" + event_id)
            if not isinstance(report, dict) or report.get("data") != current["data"] or report.get("as_of") != current["as_of"]:
                issues.append(f"crisis NPC belief disagrees with latest accepted report {actor}:{event_id}")
    old = getattr(previous, "crisis_state", None) if previous is not None else None
    if old:
        for key, prior in old.get("messages", {}).items():
            current = state["messages"].get(key)
            immutable = set(prior) - {"status", "received_at", "belief_accepted"}
            if current is None or any(current.get(k) != prior[k] for k in immutable):
                issues.append(f"crisis report snapshot removed or rewritten {key}")
            elif prior.get("status") == "delivered" and (current.get("status") != "delivered" or current.get("received_at") != prior.get("received_at") or current.get("belief_accepted") != prior.get("belief_accepted")):
                issues.append(f"crisis received report history rewritten {key}")
        for key, prior in old.get("pending_decisions", {}).items():
            current = state["pending_decisions"].get(key)
            immutable = set(prior) - {"status", "resolution", "announced"}
            if current is None or any(current.get(k) != prior[k] for k in immutable):
                issues.append(f"crisis player decision history removed or rewritten {key}")
            elif prior.get("status") != "pending" and (current.get("status") != prior.get("status") or current.get("resolution") != prior.get("resolution")):
                issues.append(f"crisis answered player decision rewritten {key}")
        for key, prior in old.get("events", {}).items():
            current = events.get(key, {})
            recorded = prior.get("responses", [])
            if current.get("responses", [])[:len(recorded)] != recorded:
                issues.append(f"crisis political response history removed or rewritten {key}")
            if "cooldown_until" in prior and current.get("cooldown_until") != prior["cooldown_until"]:
                issues.append(f"crisis event resolution cooldown history removed or rewritten {key}")
    return issues
