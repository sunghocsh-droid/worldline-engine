"""Pure contact advice from directly received proposals and private experience.

This adapter supplies two perceived alternatives to the existing NPC scorer.
It executes no order, schedules no council, and consumes no RNG.
"""
import copy

from . import intelligence, npc, npc_decision, npc_state


def _contact_history(world, actor, payload):
    view = intelligence.view(world, {"kind": "person", "id": actor}, "contact_history")
    subjects = {payload["proposer_country_id"], payload["sender_person_id"]}
    return {dispatch["payload"]["observation_id"]: dispatch["payload"]
            for dispatch in view["history"] if dispatch["belief_accepted"]
            and dispatch["payload"]["topic"] == "contact_history"
            and dispatch["payload"]["subject"] in subjects}


def _scoped_view(world, actor, payload):
    from . import diplomacy
    view = npc_state.actor_view(world.npc_state, actor, world.date)
    subjects = {payload["proposer_country_id"], payload["sender_person_id"]}
    history = _contact_history(world, actor, payload)
    contacts = {(dispatch["proposal_id"], dispatch["message_kind"]): dispatch["payload"]
                for dispatch in diplomacy.view(world, {"kind": "person", "id": actor})["history"]}

    def contact_envelope(value):
        return (isinstance(value, dict) and value.get("kind") == "contact"
                and isinstance(value.get("proposal_id"), str))

    def received_contact(contact, kind):
        if kind not in ("proposal", "response"):
            return False
        expected = contacts.get((contact["proposal_id"], kind))
        return expected is not None and intelligence.same_json(contact, expected)

    def related_memory(memory):
        observed = memory.get("interpretation", {}).get("intelligence")
        if memory.get("category") == "custom:intelligence" or observed is not None:
            return (isinstance(observed, dict) and observed.get("observation_id") in history
                    and intelligence.same_json(observed, history[observed["observation_id"]]))
        if memory.get("category") == "custom:diplomacy":
            contact = memory.get("interpretation", {}).get("contact")
            if contact_envelope(contact):
                kind = "response" if "response" in memory.get("tags", []) else "proposal"
                return (memory.get("subject") in subjects
                        and received_contact(contact, kind)
                        and (contact["proposal_id"] == payload["proposal_id"] or kind == "response"))
        return memory.get("subject") in subjects

    reports = {}
    for topic, report in view["beliefs"].items():
        observed = report.get("intelligence") if isinstance(report, dict) else None
        if observed is not None:
            if (isinstance(observed, dict) and observed.get("observation_id") in history
                    and intelligence.same_json(observed, history[observed["observation_id"]])):
                reports[topic] = report
        elif isinstance(report, dict) and report.get("subject") in subjects:
            if report.get("category") == "custom:diplomacy":
                contact = report.get("diplomacy", {})
                if (not contact_envelope(contact)
                        or received_contact(contact, report.get("message_kind"))
                        and (contact["proposal_id"] == payload["proposal_id"]
                             or report.get("message_kind") == "response")):
                    reports[topic] = report
            else:
                reports[topic] = report
    # Direct delivery supplies this one proposal snapshot. It does not expose
    # any pending proposals, hidden responses or another recipient's inbox.
    reports["contact:" + payload["proposal_id"]] = copy.deepcopy(payload)
    view["beliefs"] = reports
    view["report_metadata"] = {topic: metadata for topic, metadata in view["report_metadata"].items()
                               if topic in reports}
    view["memories"] = [memory for memory in view["memories"] if related_memory(memory)]
    view["institutional_memories"] = {
        group: [memory for memory in memories if related_memory(memory)]
        for group, memories in view["institutional_memories"].items()}
    sender = payload["sender_person_id"]
    view["relationships"] = {sender: view["relationships"][sender]} if sender in view["relationships"] else {}
    return view


def _options(view, payload):
    options = [
        {"id": "accept_contact", "kind": "contact_response", "outcomes": {},
         "tags": ["diplomacy", "contact", "intelligence", payload["content"]["topic"]],
         "institutional_feasibility": {"feasible": True}},
        {"id": "reject_contact", "kind": "contact_response", "outcomes": {},
         "tags": ["continuity", "contact_rejection"],
         "institutional_feasibility": {"feasible": True}},
    ]
    sender = payload["sender_person_id"]
    relationship = view["relationships"].get(sender)
    if relationship is not None:
        # A known neutral relationship adds zero support; an unknown sender
        # has no synthesized relationship or automatic acceptance bonus.
        options[0]["reactions"] = {sender: {"expected_support": 2 * relationship["trust"] - 1}}
    return options


def recommend(world, proposal_id):
    """Return current government advice, without any mutation or execution."""
    from . import diplomacy
    if world.diplomacy_state is None or world.npc_state is None:
        return None
    proposal = world.diplomacy_state["proposals"].get(proposal_id)
    if proposal is None or proposal["status"] != "pending":
        return None
    body = proposal["body"]
    country = body["recipient_country_id"]
    state = world.npc_state
    government = state["governments"].get(country)
    if (government is None or government.get("autonomous") is not True
            or country in world.crisis_state["player_countries"]):
        return None
    recommendations = {}
    for actor in dict.fromkeys([government["ruler"], *government.get("cabinet", [])]):
        person = state["persons"].get(actor)
        if person is None or person["country"] != country or person.get("active") is not True:
            continue
        dispatch = diplomacy.received_proposal(world, actor, proposal_id)
        if dispatch is None:
            continue
        payload = dispatch["payload"]
        view = _scoped_view(world, actor, payload)
        recommendations[actor] = npc_decision.decide_deterministic(view, _options(view, payload))
    choice = npc.government_choice(state, country, recommendations)
    if choice is None or choice["chosen"] not in ("accept_contact", "reject_contact"):
        return None
    members = choice["members"]
    ruler = government["ruler"]
    executor = ruler if ruler in recommendations and npc._authority(
        state, ruler, country, "diplomatic_engagement") else next(
            (actor for actor in members if npc._authority(state, actor, country, "diplomatic_engagement")), None)
    if executor is None:
        return None
    return copy.deepcopy({"proposal_id": proposal_id, "country": country,
                          **choice, "executor": executor, "recommendations": recommendations})
