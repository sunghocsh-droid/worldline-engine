"""Reproducible fictional crisis research using the production simulation ticks.

The harness adds initial conditions, never date-triggered outcomes. A hash chain
and optional compressed trace preserve evidence without retaining every large
NPC option tree in memory. Runtime is measurement metadata, not game state.
"""
import argparse
import copy
import csv
import gzip
import hashlib
import json
import math
import random
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import economy, npc, npc_state, projects
from .audit import Auditor
from .models import World, canonical_date
from .npc_benchmark import COUNTRIES, campaign_world as npc_campaign_world, encode, verify_decision
from .persistence import tuples

SEED = 941
NAMES = {"a": "가상 개발국 A", "b": "가상 위신국 B", "c": "가상 신중국 C", "d": "가상 연합국 D"}
HAZARDS = ("drought", "flood", "storm", "earthquake", "fire", "extreme_cold", "extreme_heat")
INTERNATIONAL = {"border_tension", "border_incident", "patrol_incident", "diplomatic_crisis", "ultimatum",
    "partial_mobilization", "full_mobilization", "armed_clash", "state_of_war", "interstate_crisis"}
SOCIAL = {"petition", "protest", "strike", "riot", "tax_resistance", "local_uprising_precursor",
    "civil_unrest", "labour_unrest", "separatist_agitation", "autonomy_demand"}
FINANCIAL = {"fiscal_crisis", "fiscal_stress", "financial_crisis", "failed_payment", "payment_failure", "recession"}


def source_hashes():
    """Bind research data to all Python simulation and harness source files."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))}


def campaign_config():
    """Fictional annual hazard rates; they are scenario inputs, not history data."""
    rates = {
        "a": (.06, .10, .08, .01, .04, .01, .02, .12, .025),
        "b": (.10, .025, .03, .012, .07, .06, .025, .15, .035),
        "c": (.15, .10, .08, .018, .05, .04, .035, .20, .045),
        "d": (.045, .055, .12, .025, .035, .015, .015, .10, .020),
    }
    return {"player_countries": [], "settings": {"auto_enabled": True},
        "regions": {"r" + c: {"hazard_rates": dict(zip(HAZARDS, rates[c][:7])),
            "bad_harvest_rate": rates[c][7], "epidemic_introduction_rate": rates[c][8],
            "vulnerability": {"a": .35, "b": .5, "c": .75, "d": .4}[c],
            "food_reserve_months": {"a": 3.0, "b": 1.5, "c": .3, "d": 2.5}[c]}
            for c in COUNTRIES},
        "borders": {
            "ab": {"countries": ["a", "b"], "regions": ["ra", "rb"], "tension": 32},
            "bc": {"countries": ["b", "c"], "regions": ["rb", "rc"], "tension": 78},
            "cd": {"countries": ["c", "d"], "regions": ["rc", "rd"], "tension": 16},
            "da": {"countries": ["d", "a"], "regions": ["rd", "ra"], "tension": 4},
        }}


def campaign_world():
    """Four economies/regimes, trade links, rivalries and regional commanders."""
    world = npc_campaign_world()
    world.scenario_id = "fictional-crisis-campaign"
    world.relations["a-b"].update(dispute=.25, trust=.1)
    world.relations["b-c"].update(dispute=.8, trust=-.6, status="rival")
    world.relations["c-d"].update(dispute=.1, trust=.3)
    world.relations["d-a"].update(dispute=0, trust=.7, status="allied")
    state = world.npc_state
    for c in COUNTRIES:
        npc_state.add_person(state, c + ":local", c, "가상 " + c.upper() + " 국경 지휘관",
            role="regional_military_commander", region="r" + c, authority=["local_action"],
            abilities={"military": .7, "intelligence": .55, "administration": .45},
            traits={"risk_tolerance": .85 if c in ("b", "c") else .25,
                "prestige_sensitivity": .85 if c in ("b", "c") else .35,
                "fear_sensitivity": .75 if c == "c" else .4,
                "impulsiveness": .75 if c in ("b", "c") else .2,
                "institutional_loyalty": .8, "personal_loyalty": .4},
            biases={"overconfidence": .7 if c == "b" else .2,
                "enemy_underestimation": .5 if c == "b" else 0},
            preferences={"security": .95, "prestige": .8 if c in ("b", "c") else .2,
                         "fiscal": .1 if c in ("b", "c") else .5},
            goals=["담당 국경의 안전과 부하 보호"], importance=.8)
        npc_state.add_group(state, c + ":regional_command", c, "가상 지역 군사기관",
            kind="regional_authority", region="r" + c, members=[c + ":local"],
            leadership=[c + ":local"], preferences={"security": .9, "prestige": .5}, influence=.4)
        target = "c" if c == "b" else "b" if c == "c" else "b" if c == "a" else "c"
        npc_state.receive_report(state, c + ":local", "diplomacy",
            {"target": target, "enemy_strength": 30 if c == "b" else 120,
             "own_strength": 150 if c == "b" else 100,
             "threat": .8 if c in ("b", "c") else .15}, world.date,
            "가상 국경의 초기 불완전 보고", accuracy=.3 if c == "b" else .7,
            confidence=.85 if c in ("b", "c") else .6, reliability=.5 if c == "b" else .7)
    Auditor().check(world)
    return world


class TraceAccumulator:
    """Bounded examples and exact rolling digest of every emitted system event."""
    def __init__(self):
        self.count = 0
        self.digest = "0" * 64
        self.kinds = Counter()
        self.response_types = Counter()
        self.npc_choices = Counter()
        self.examples = {}
        self.crisis_entries = []
        self.sink = None

    def observe(self, entry):
        raw = encode(entry)
        self.digest = hashlib.sha256((self.digest + "\n" + raw).encode("utf-8")).hexdigest()
        self.count += 1
        kind = entry.get("kind", entry.get("type", "unknown"))
        self.kinds[kind] += 1
        if entry.get("kind") == "npc_decision":
            verify_decision(entry["decision"])
            self.npc_choices[entry["decision"].get("selected") or "none"] += 1
        elif isinstance(entry.get("decision"), dict) and "evaluations" in entry["decision"]:
            verify_decision(entry["decision"])
        if entry.get("system") == "crisis":
            # Full reasoning is in the compressed trace and exact hash chain.
            # Keep one bounded selected explanation per kind in memory and the
            # small factual transition/action entries needed by summary queries.
            if kind != "crisis_npc_decision":
                summary_entry = {k: copy.deepcopy(v) for k, v in entry.items() if k != "decision"}
                if "decision" in entry:
                    summary_entry["decision"] = {k: copy.deepcopy(entry["decision"].get(k))
                        for k in ("selected", "reason_trace")}
                self.crisis_entries.append(summary_entry)
        self.examples.setdefault(kind, copy.deepcopy(entry))
        if self.sink is not None:
            self.sink.write(raw + "\n")

    def to_dict(self):
        return {"count": self.count, "sha256": self.digest, "kinds": dict(self.kinds),
            "npc_choices": dict(self.npc_choices), "examples": copy.deepcopy(self.examples),
            "crisis_entries": copy.deepcopy(self.crisis_entries)}

    @classmethod
    def restore(cls, raw):
        obj = cls()
        obj.count, obj.digest = raw["count"], raw["sha256"]
        obj.kinds, obj.npc_choices = Counter(raw["kinds"]), Counter(raw["npc_choices"])
        obj.examples, obj.crisis_entries = copy.deepcopy(raw["examples"]), copy.deepcopy(raw["crisis_entries"])
        return obj


class CrisisReplay:
    """Exact production ticks with monthly auditing and no gameplay SQLite IO."""
    def __init__(self, world=None, seed=SEED, config=None):
        from . import crisis
        if type(seed) is not int:
            raise ValueError("Campaign seed must be an integer")
        self.world = copy.deepcopy(world if world is not None else campaign_world())
        self.seed, self.rng = seed, random.Random(seed)
        self.trace = TraceAccumulator()
        self.source_sha256 = source_hashes()
        crisis.initialize(self.world, campaign_config() if config is None else config, rng=self.rng)
        Auditor().check(self.world)

    def month(self):
        from . import crisis
        world = self.world
        start = canonical_date(world.date)
        index = start.year * 12 + start.month
        target = date(index // 12, index % 12 + 1, 1)
        while canonical_date(world.date) < target:
            previous = world.date
            world.date = (canonical_date(world.date) + timedelta(days=1)).isoformat()
            world.production.clear()
            event = {"previous_date": previous, "date": world.date, "days": 1}
            for tick in (projects.tick, economy.tick, npc.tick, crisis.tick):
                for entry in tick(world, copy.deepcopy(event), self.rng) or []:
                    self.trace.observe(entry)
            # These are the same retained MVP signals after registered systems.
            for _, pressure in sorted(world.pressures.items()):
                self.rng.random()
        Auditor().check(world)
        return world

    def checkpoint(self, path):
        data = {"crisis_campaign_schema": 1, "world": self.world.to_dict(), "seed": self.seed,
            "rng": self.rng.getstate(), "trace": self.trace.to_dict(), "source_sha256": self.source_sha256}
        data["sha256"] = hashlib.sha256(encode(data).encode("utf-8")).hexdigest()
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @classmethod
    def restore(cls, path):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        checksum = data.pop("sha256")
        if data.get("crisis_campaign_schema") != 1 or checksum != hashlib.sha256(encode(data).encode("utf-8")).hexdigest():
            raise ValueError("Invalid crisis campaign checkpoint")
        if data["source_sha256"] != source_hashes():
            raise ValueError("Crisis campaign source changed")
        obj = cls.__new__(cls)
        obj.world, obj.seed = World.from_dict(data["world"]), data["seed"]
        obj.rng = random.Random()
        obj.rng.setstate(tuples(data["rng"]))
        obj.trace, obj.source_sha256 = TraceAccumulator.restore(data["trace"]), data["source_sha256"]
        Auditor().check(obj.world)
        return obj


def measure(world, month):
    state = world.crisis_state
    row = {"month": month, "date": world.date, "countries": {},
        "event_count": len(state["events"]),
        "active_events": sum(e.get("end_date") is None for e in state["events"].values()),
        "pending_decisions": len(state["pending_decisions"])}
    for country in COUNTRIES:
        c, e = world.countries[country], world.economy["countries"][country]
        regions = economy.owned_regions(world, country)
        row["countries"][country] = {"population": c.population, "gdp": c.gdp,
            "debt": c.debt, "reserves": c.treasury, "arrears": e["credit"]["arrears"],
            "distress_state": e["credit"]["distress_state"], "creditworthiness": e["credit"]["creditworthiness"],
            "food_security": min(world.economy["regions"][r]["demography"]["food_security"] for r in regions),
            "military": e["labour"].get("military", 0), "unemployed": e["labour"].get("unemployed", 0),
            "capital_stock": math.fsum(s["capital_stock"] for r in regions for s in world.economy["regions"][r]["sectors"].values()),
            "capital_destruction": math.fsum(s["destruction_losses"] for r in regions for s in world.economy["regions"][r]["sectors"].values()),
            "epidemic_losses": sum(world.economy["regions"][r]["demography"]["epidemic_losses"] for r in regions),
            "disaster_losses": sum(world.economy["regions"][r]["demography"]["disaster_losses"] for r in regions),
            "war_losses": sum(world.economy["regions"][r]["demography"]["war_losses"] for r in regions),
            "exports": e["exports"], "imports": e["imports"], "stability": c.stability}
    return row


def summarize(world, trace):
    state = world.crisis_state
    events = state["events"]
    types = Counter(e["event_type"] for e in events.values())
    response_types, country_responses, war_entries = Counter(), Counter(), Counter()
    deescalations, escalations, mediated, unilateral, misinformed = 0, 0, 0, 0, 0
    explicitly_escalated = {entry["event_id"] for entry in trace.crisis_entries
        if entry.get("kind") == "crisis_escalated"}
    for event in events.values():
        if event["event_type"] == "state_of_war":
            war_entries.update(event["countries"])
        if event.get("parent_event"):
            parent = events[event["parent_event"]]
            if (event["event_id"] in explicitly_escalated or parent.get("resolution_state") == "escalated"
                    or event.get("escalation_level", 0) > parent.get("escalation_level", 0)):
                escalations += 1
        if event["event_type"] in INTERNATIONAL | SOCIAL | FINANCIAL | {"famine", "epidemic"} and event.get("resolution_state") in ("contained", "resolved"):
            deescalations += 1
        for response in event.get("responses", []):
            response_types[response["response"]] += 1
            country_responses[response["country"] + ":" + response["response"]] += 1
        if (any(r["response"] == "mediate" for r in event.get("responses", []))
                and event.get("resolution_state") in ("contained", "resolved")):
            mediated += 1
        conditions = event.get("triggering_conditions", {})
        facts = event.get("actual_facts", {})
        if (conditions.get("kind") == "local_unilateral_action" or facts.get("local_unilateral")
                or any(c.get("trigger_kind") == "local_unilateral_action" for c in event.get("causes", []))):
            unilateral += 1
        if conditions.get("misinformation") or facts.get("misinformation"):
            misinformed += 1
    local_actions = [e for e in trace.crisis_entries if e.get("kind") == "local_action" and e.get("execution", {}).get("status") == "accepted"]
    losses = {}
    for country in COUNTRIES:
        regions = economy.owned_regions(world, country)
        losses[country] = {"epidemic_losses": sum(world.economy["regions"][r]["demography"]["epidemic_losses"] for r in regions),
            "disaster_losses": sum(world.economy["regions"][r]["demography"]["disaster_losses"] for r in regions),
            "war_losses": sum(world.economy["regions"][r]["demography"]["war_losses"] for r in regions),
            "capital_destruction": math.fsum(s["destruction_losses"] for r in regions for s in world.economy["regions"][r]["sectors"].values()),
            "paid_response_cost": sum(response["actual_cost"] for e in events.values() for response in e.get("responses", []) if response["country"] == country)}
    return {"event_count": len(events), "event_types": dict(types),
        "active_events": sum(e.get("end_date") is None for e in events.values()),
        "crisis_count": sum(e["event_type"] in INTERNATIONAL | SOCIAL | FINANCIAL | {"famine", "epidemic"} for e in events.values()),
        "escalations": escalations, "deescalations": deescalations,
        "escalation_share": escalations / max(1, escalations + deescalations),
        "country_war_entries": {c: war_entries[c] for c in COUNTRIES},
        "response_types": dict(response_types), "country_responses": dict(country_responses),
        "local_unilateral_actions": len(local_actions), "local_triggered_events": unilateral,
        "misinformation_triggered_events": misinformed, "mediated_resolutions": mediated,
        "realized_losses": losses,
        "system_log_count": trace.count, "system_log_kinds": dict(trace.kinds),
        "npc_choices": dict(trace.npc_choices), "combat_resolutions": 0}


def deterministic_payload(data):
    return {key: value for key, value in data.items() if key != "runtime_seconds"}


def run_campaign(months=360, seed=SEED, checkpoint_path=None, checkpoint_month=None,
                 progress=None, trace_path=None):
    if type(months) is not int or months <= 0:
        raise ValueError("Campaign horizon must be positive whole months")
    if ((checkpoint_path is None) != (checkpoint_month is None) or
            checkpoint_month is not None and (type(checkpoint_month) is not int or not 0 < checkpoint_month <= months)):
        raise ValueError("Checkpoint path and a valid campaign month are both required")
    started = time.perf_counter()
    replay = CrisisReplay(seed=seed)
    initial_sha256 = hashlib.sha256(encode(replay.world.to_dict()).encode("utf-8")).hexdigest()
    records = [measure(replay.world, 0)]
    sink = gzip.open(trace_path, "wt", encoding="utf-8") if trace_path is not None else None
    replay.trace.sink = sink
    try:
        for month in range(1, months + 1):
            replay.month()
            records.append(measure(replay.world, month))
            if checkpoint_path is not None and month == checkpoint_month:
                replay.checkpoint(checkpoint_path)
                replay = CrisisReplay.restore(checkpoint_path)
                replay.trace.sink = sink
            if progress is not None and month % 12 == 0:
                progress(month)
    finally:
        if sink is not None:
            sink.close()
    if source_hashes() != replay.source_sha256:
        raise ValueError("Crisis campaign source changed while running; restart")
    return {"crisis_campaign_schema": 1, "months": months, "seed": seed,
        "initial_sha256": initial_sha256, "source_sha256": replay.source_sha256,
        "method": "daily production projects/economy/NPC/crisis ticks and MVP pressure draws; monthly Auditor",
        "runtime_seconds": time.perf_counter() - started,
        "summary": summarize(replay.world, replay.trace), "records": records,
        "trace": replay.trace.to_dict(), "final_world": replay.world.to_dict(),
        "final_rng": replay.rng.getstate()}


def export(data, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with gzip.open(directory / "campaign.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    fields = ["month", "date", "country", *data["records"][0]["countries"][COUNTRIES[0]]]
    with (directory / "campaign.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in data["records"]:
            for country, values in record["countries"].items():
                writer.writerow({"month": record["month"], "date": record["date"], "country": country, **values})
    (directory / "summary.json").write_text(json.dumps({"months": data["months"], "seed": data["seed"],
        "source_sha256": data["source_sha256"], "initial_sha256": data["initial_sha256"],
        "runtime_seconds": data["runtime_seconds"], "summary": data["summary"],
        "trace_sha256": data["trace"]["sha256"]}, ensure_ascii=False, indent=2), encoding="utf-8")


def run_stress_cases(months=3):
    """Synthetic initial conditions and explicit external shocks, not game rules.

    These eight experiments inspect pathways rather than assert that every
    country must collapse or that a named date must create an event.
    """
    from . import credit, crisis, crisis_effects, crisis_response, crisis_state, labour
    if type(months) is not int or months <= 0:
        raise ValueError("Stress horizon must be positive whole months")
    sources = source_hashes()
    cases = ("default_and_war", "harvest_blockade_low_reserves", "mobilization_and_labour",
        "identity_grievance", "hardline_border_commanders", "moderate_governments_local_incident",
        "growth_and_flood", "recession_and_small_petition")
    result = {}
    for index, name in enumerate(cases):
        world, config = campaign_world(), campaign_config()
        for profile in config["regions"].values():
            profile.update(hazard_rates=dict.fromkeys(HAZARDS, 0), bad_harvest_rate=0,
                           epidemic_introduction_rate=0)
        config["borders"] = {}
        if name == "default_and_war":
            country, economic = world.countries["b"], world.economy["countries"]["b"]
            country.treasury, country.debt = 0, 12000
            economic["credit"].update(domestic_debt=12000, foreign_debt=0, arrears=1000,
                distress_state="default", default_count=1, missed_service_days=365)
            economic["finance"].update(reserves=0, government_debt=12000)
            credit.refresh(world, "b")
        if name == "harvest_blockade_low_reserves":
            world.countries["c"].treasury = 0
            world.economy["countries"]["c"]["finance"]["reserves"] = 0
            config["regions"]["rc"]["food_reserve_months"] = 0
            credit.refresh(world, "c")
        if name in ("hardline_border_commanders", "moderate_governments_local_incident"):
            config["borders"]["bc"] = {"countries": ["b", "c"], "regions": ["rb", "rc"], "tension": 95}
            for country in ("b", "c"):
                person = world.npc_state["persons"][country + ":local"]
                person["traits"].update(risk_tolerance=.9, prestige_sensitivity=.9, impulsiveness=.8)
                if name == "moderate_governments_local_incident":
                    ruler = world.npc_state["persons"][country + ":ruler"]
                    ruler["traits"].update(risk_tolerance=.15, prestige_sensitivity=.1, pragmatism=.9)
                    ruler["preferences"] = {"security": .5, "fiscal": .9, "prosperity": .8, "prestige": .1}
                    world.npc_state["governments"][country]["decision_rule"] = "cabinet"
                    world.npc_state["groups"][country + ":military_faction"]["influence"] = .2
        replay = CrisisReplay(world, seed=SEED + index, config=config)
        focus = "b" if name in ("default_and_war", "mobilization_and_labour", "hardline_border_commanders", "moderate_governments_local_incident") else "c" if name in ("harvest_blockade_low_reserves", "identity_grievance", "recession_and_small_petition") else "a"
        setup, economic_context = [], {}
        if name == "mobilization_and_labour":
            labour.mobilize(replay.world, "b", "rb", 2000)
            crisis_state.set_pressure(replay.world, "labour_unrest", 88, region="rb",
                causes=[{"kind": "mobilization", "mobilized": 2000}])
            setup.append("2000 people withdrawn from the regional civilian labour pool")
        if name == "identity_grievance":
            crisis_state.set_pressure(replay.world, "ethnic_or_religious_tension", 88, region="rc",
                causes=[{"kind": "reported_discrimination", "group": "fictional-regional-minority"}])
            crisis_state.set_pressure(replay.world, "separatism", 82, region="rc",
                causes=[{"kind": "regional_autonomy_grievance"}])
            crisis.create_event(replay.world, "discrimination", "rc", ["c"], severity=.6,
                causes=[{"kind": "synthetic_stress_input", "group": "fictional-regional-minority"}],
                actual_facts={"policy": "unequal regional access", "group": "fictional-regional-minority"})
        if name == "harvest_blockade_low_reserves":
            event = crisis.create_event(replay.world, "bad_harvest", "rc", ["c"], severity=.8,
                causes=[{"kind": "synthetic_weather_shock"}])
            crisis_effects.enqueue(replay.world, event["event_id"], "economic_shock",
                {"kind": "blockade", "region": "rc", "severity": .9, "duration_days": 180})
            setup.append("external harvest shock plus actual trade disruption and zero cash")
        if name in ("default_and_war", "hardline_border_commanders", "moderate_governments_local_incident"):
            event = crisis.create_event(replay.world, "border_incident", "rb", ["b", "c"], severity=.55,
                causes=[{"kind": "synthetic_patrol_contact"}],
                actual_facts={"threat": .3, "alleged_casualties": 0})
            for country in ("b", "c"):
                actor = country + ":local"
                crisis_response.queue_report(replay.world, event["event_id"], actor, delay=0,
                    intentional_distortion={"threat": .95}, accuracy=.3, confidence=.9,
                    source="stress scenario erroneous field estimate")
                crisis_response.queue_command(replay.world, event["event_id"], actor,
                    {"intent": "avoid_conflict"}, delay=7, sender=country + ":ruler")
            setup.append("delayed central avoidance orders and mistaken local threat reports")
        if name == "growth_and_flood":
            replay.month()
            baseline = measure(replay.world, 1)["countries"]["a"]
            investment = crisis.create_event(replay.world, "custom:external_equity_inflow", "ra", ["a"], severity=.6,
                causes=[{"kind": "synthetic_external_investment_condition"}],
                actual_facts={"source": "external private equity", "not_government_revenue": True})
            crisis_effects.enqueue(replay.world, investment["event_id"], "economic_shock",
                {"kind": "foreign_investment_boom", "region": "ra", "severity": .6, "duration_days": 730})
            for _ in range(17):
                replay.month()
            expanded = measure(replay.world, 18)["countries"]["a"]
            economic_context = {"baseline_gdp": baseline["gdp"], "pre_flood_gdp": expanded["gdp"],
                "measured_growth": expanded["gdp"] / max(1, baseline["gdp"]) - 1,
                "baseline_capital": baseline["capital_stock"], "pre_flood_capital": expanded["capital_stock"],
                "warmup_months": 18,
                "external_equity_committed": sum(s.get("foreign_capital_committed", 0)
                    for s in replay.world.economy["shocks"].values() if s["region"] == "ra")}
            setup.append("actual expansion after finite external equity and normal construction delays; GDP never assigned")
            crisis.create_event(replay.world, "flood", "ra", ["a"], severity=.75,
                causes=[{"kind": "synthetic_flood_during_observed_development"}])
        if name == "recession_and_small_petition":
            for _ in range(12):
                replay.month()
            baseline = measure(replay.world, 12)["countries"]["c"]
            damage = crisis.create_event(replay.world, "custom:productive_capital_loss", "rc", ["c"], severity=.7,
                causes=[{"kind": "synthetic_recession_input"}])
            crisis_effects.enqueue(replay.world, damage["event_id"], "economic_shock",
                {"kind": "capital_destruction", "region": "rc", "severity": .7, "duration_days": 365})
            for _ in range(12):
                replay.month()
            contracted = measure(replay.world, 24)["countries"]["c"]
            economic_context = {"pre_destruction_gdp": baseline["gdp"], "pre_petition_gdp": contracted["gdp"],
                "measured_growth": contracted["gdp"] / max(1, baseline["gdp"]) - 1,
                "pre_destruction_capital": baseline["capital_stock"], "pre_petition_capital": contracted["capital_stock"],
                "warmup_months": 24}
            crisis.create_event(replay.world, "petition", "rc", ["c"], severity=.12,
                parent_event=damage["event_id"], causes=[{"kind": "small_political_dispute_after_real_capital_loss"}])
        opening = measure(replay.world, 0)
        for _ in range(months):
            replay.month()
        closing = measure(replay.world, months)
        result[name] = {"focus_country": focus, "setup": setup, "economic_context": economic_context, "months_after_setup": months,
            "opening": opening, "closing": closing, "summary": summarize(replay.world, replay.trace),
            "pressures": copy.deepcopy(replay.world.crisis_state["pressures"]),
            "event_paths": [{key: copy.deepcopy(event[key]) for key in
                ("event_id", "event_type", "start_date", "end_date", "countries", "region", "parent_event", "child_events", "causes", "resolution_state")}
                for event in replay.world.crisis_state["events"].values()]}
    if source_hashes() != sources:
        raise ValueError("Crisis stress source changed while running; restart")
    return result


def generate_report(data, destination, tests="최종 테스트 대기"):
    lines = ["# Crisis & Event Engine v1: 가상 세계 검증", "",
        f"실제 실행: {data['months']}개월, 가상 4개국·20명, seed {data['seed']}. 실행시간 {data['runtime_seconds']:.1f}초.", "",
        "실제 역사의 연도별 사건이나 GDP 목표를 입력하지 않았다. 기존 경제 profile과 NPC 정책 판단을 사용하고, 지역별 재난 위험·국경 분쟁·무역·동맹은 가상 초기조건으로 설정했다.", "",
        "## 사건 집계", "", "| 종류 | 사건 수 |", "|---|---:|"]
    for kind, count in sorted(data["summary"]["event_types"].items()):
        lines.append(f"| {kind} | {count} |")
    lines.extend(["", f"전체 사건 {data['summary']['event_count']}건, 종료되지 않은 사건 {data['summary']['active_events']}건. 전투 해결 횟수는 0이다.", "",
        f"위기 단계 사건 {data['summary']['crisis_count']}건, 부모 사건보다 높은 단계로 이동 {data['summary']['escalations']}건, 완화·해결 {data['summary']['deescalations']}건. 확대 비중 {data['summary']['escalation_share']:.1%}는 이 두 집계의 비율이며 개별 위기의 전쟁 확률이 아니다.", "",
        f"현지 제한 독단 {data['summary']['local_unilateral_actions']}건, 이를 계기로 생성된 후속 사건 {data['summary']['local_triggered_events']}건, 오정보가 계기로 기록된 사건 {data['summary']['misinformation_triggered_events']}건, 중재 대응 뒤 완화·해결된 사건 {data['summary']['mediated_resolutions']}건.", "",
        "| 국가 | 전쟁 상태 진입 횟수 |", "|---|---:|"])
    for country, count in data["summary"]["country_war_entries"].items():
        lines.append(f"| {NAMES[country]} | {count} |")
    lines.extend(["", "## NPC 대응", "", "| 대응 | 실제 선택 수 |", "|---|---:|"])
    for response, count in sorted(data["summary"]["response_types"].items()):
        lines.append(f"| {response} | {count} |")
    lines.extend(["",
        "## 경제와 인구", "", "| 국가 | 기간 | 인구 | GDP | 부채 | 준비자산 | 재정 상태 |", "|---|---:|---:|---:|---:|---:|---|"])
    for month in (0, 120, 240, 360):
        if month > data["months"]:
            continue
        record = data["records"][month]
        for c, row in record["countries"].items():
            lines.append(f"| {NAMES[c]} | {month // 12}년 | {row['population']:,} | {row['gdp']:,} | {row['debt']:,} | {row['reserves']:,} | {row['distress_state']} |")
    lines.extend(["", "## 실제 적용된 손실과 대응 비용", "",
        "| 국가 | 전염병 사망 | 재난·식량 위기 손실 | 자본 파괴 | 정부 대응 비용 |", "|---|---:|---:|---:|---:|"])
    for c, loss in data["summary"]["realized_losses"].items():
        lines.append(f"| {NAMES[c]} | {loss['epidemic_losses']:,} | {loss['disaster_losses']:,} | {loss['capital_destruction']:,.1f} | {loss['paid_response_cost']:,} |")
    lines.extend(["", "인구 손실은 경제·인구 결산에 실제 적용된 누적값이다. 대기 중인 피해를 사망자로 먼저 더하지 않았다. 자본 파괴는 해당 산업 장부의 실제 파괴 손실이며 감가상각을 포함하지 않는다. GDP는 사건이 없었을 때의 가상 GDP를 역산한 피해액이 아니라 실제 생산 결과다. 전투 사망자는 계산하지 않았다.", "",
        "## 실제 인과경로 예시", ""])
    events = data["final_world"]["crisis_state"]["events"]
    paths = []
    for kind in ("state_of_war", "armed_clash", "famine", "riot", "payment_failure", "financial_crisis", "epidemic"):
        event = next((e for e in events.values() if e["event_type"] == kind), None)
        if event is None:
            lines.append(f"- `{kind}`: 이 실행에서는 발생하지 않았다.")
            continue
        chain = [event]
        while chain[-1].get("parent_event"):
            chain.append(events[chain[-1]["parent_event"]])
        chain.reverse()
        text = " → ".join(f"{e['event_type']}({e['start_date']})" for e in chain)
        lines.append(f"- `{event['event_id']}`: {text}; 현재 {event['resolution_state']}.")
        paths.append(event)
    decision = data["trace"]["examples"].get("crisis_npc_decision")
    if decision:
        lines.extend(["", "다음은 실제 NPC 대응 판단의 선택된 근거다. 이유의 기여도 합은 판단 점수와 자동 대조한다.", "",
            f"인물 `{decision['actor']}`, 사건 `{decision['event_id']}`, 선택 `{decision['decision'].get('selected')}`.", "",
            "| 계산 근거 | 기여도 |", "|---|---:|"])
        for reason in decision["decision"].get("reason_trace", [])[:12]:
            lines.append(f"| {reason['factor']} | {reason['contribution']:.4f} |")
    stress_path = Path(destination).parent / "benchmarks" / "crisis" / "stress_cases.json"
    if stress_path.exists():
        stress = json.loads(stress_path.read_text(encoding="utf-8"))
        if stress.get("source_sha256") == data["source_sha256"]:
            lines.extend(["", "## 인위적 극단조건 별도 실험", "",
                "8개 실험은 동일한 생산 엔진을 사용하되 각 조건과 외부 충격을 명시적으로 입력했다. 실제 플레이의 특정 연도에 같은 충격을 발생시키는 코드가 아니다. 결과가 모두 붕괴해야 한다는 목표값을 설정하지 않았다.", "",
                "| 조건 | 중점 국가 | 전체 사건 수 | 종료 후 GDP | 종료 후 부채 | 종료 후 재정 |", "|---|---|---:|---:|---:|---:|"])
            for name, experiment in stress["cases"].items():
                row = experiment["closing"]["countries"][experiment["focus_country"]]
                lines.append(f"| {name} | {experiment['focus_country'].upper()} | {experiment['summary']['event_count']} | {row['gdp']:,} | {row['debt']:,} | {row['distress_state']} |")
            lines.append("\n사건 수는 해당 실험의 전체 세계 집계이며, GDP·부채·재정은 중점 국가 값이다. 경기 확장과 불황 실험에는 충격 전 실제 경제 계산을 위한 준비 기간을 별도로 두었다. 개별 압력과 부모·자식 사건 경로는 `stress_cases.json`에 보관한다.")
            growth = stress["cases"]["growth_and_flood"]["economic_context"]
            recession = stress["cases"]["recession_and_small_petition"]["economic_context"]
            lines.extend(["",
                f"확장 조건은 실제 외국 지분투자 {growth['external_equity_committed']:.2f}를 정상 건설 과정으로 처리했습니다. 홍수 전 GDP는 {growth['baseline_gdp']:,}→{growth['pre_flood_gdp']:,}({growth['measured_growth']:+.2%})입니다. 이는 급격한 호황보다 완만한 확장 사례입니다. 투자가 즉시 대규모 GDP 증가가 되지 않았다는 제한도 함께 기록합니다.", "",
                f"침체 조건은 실제 자본 파괴 후 GDP {recession['pre_destruction_gdp']:,}→{recession['pre_petition_gdp']:,}({recession['measured_growth']:+.2%})로 확인하고 작은 청원을 넣었습니다. 두 실험 모두 GDP 자체를 직접 설정하지 않았습니다."])
    lines.extend(["", "## 발견한 통합 위험과 처리", "",
        "- 기존 경제 shock의 일회성 피해와 사건의 별도 피해를 동시에 쓰면 자본·인구 손실을 두 번 셀 수 있습니다. 사건 파이프라인은 생산 차질과 일회성 피해의 담당을 분리하고, 적용 영수증과 재실행 검사를 사용합니다.",
        "- 보고 도착 전 사실을 기억하거나 플레이어의 선택을 정부가 먼저 확정하지 않도록 전달·권한·대기 결정을 구분했습니다.",
        "- 한 나라의 중재 선택으로 상대의 강경 선택까지 취소하는 경로를 막고, 재정 협상은 실제 신용 상태의 회복을 확인합니다.",
        "- 경제 비용 조회 때 모든 정치 기억을 매번 복사하던 비용을 줄였습니다. 자원을 가상 예약하는 재고만 분리하며, 서로 다른 재정·자원 조건의 수정 전 견적과 정확히 일치하는 회귀 검사로 경제 계산을 유지했습니다.",
        "- 확대·중재 집계에서 같은 단계 번호의 시위→폭동을 놓치거나 양국의 중재를 두 사건으로 세지 않도록 원인 연결과 고유 사건을 집계합니다.",
        "", "## 검증 범위와 한계", "", str(tests), "",
        "연구 harness는 게임의 프로젝트→경제→NPC→사건 실행 순서를 그대로 사용한다. SQLite 게임과 짧은 기간의 상태·RNG·시스템 사건 일치를 별도 테스트한다. 긴 실행은 월말마다 Auditor를 적용한다.", "",
        "실행시간은 검증용 정보이며 게임 상태나 재현 비교에 포함되지 않는다. 원시 JSON은 압축 보관하며 CSV는 사람이 읽을 수 있는 월별 경제 결과다. 모든 시스템 사건의 압축 로그와 순서별 SHA-256 체인을 보관한다.", "",
        "이번 버전은 전투 승패·점령·은행·가격·환율·정밀 역학·대중 정치심리를 계산하지 않는다. 사건 발생과 정부 대응의 인과관계, 실제 피해, 전달 정보, 기억과 압력을 검증한다.", "",
        "가상 지역의 재난 빈도와 취약성은 실제 지리·기후 자료로 검증한 역사 계수가 아닙니다. 네 나라·한 seed의 30년 결과는 방향성과 저장 재현성 검증이며 모든 세계의 장기 현실성을 증명하지 않습니다. 선언된 전쟁 상태 이후의 전투·종전·조약과 정치체제 붕괴는 후속 시스템이 담당해야 합니다.", ""])
    Path(destination).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=360)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", default="benchmarks/crisis")
    parser.add_argument("--report", default="CRISIS_SIMULATION_REPORT.md")
    parser.add_argument("--tests", default="최종 테스트 대기")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--stress-only", action="store_true")
    parser.add_argument("--stress-months", type=int, default=3)
    parser.add_argument("--checkpoint-month", type=int)
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    if args.stress_only:
        cases = run_stress_cases(args.stress_months)
        (directory / "stress_cases.json").write_text(json.dumps({"source_sha256": source_hashes(),
            "seed": SEED, "months_after_setup": args.stress_months, "cases": cases},
            ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(f"Completed {len(cases)} synthetic stress cases", flush=True)
        return
    if args.report_only:
        with gzip.open(directory / "campaign.json.gz", "rt", encoding="utf-8") as stream:
            data = json.load(stream)
    else:
        data = run_campaign(args.months, args.seed,
            checkpoint_path=directory / "research_checkpoint.json.gz" if args.checkpoint_month else None,
            checkpoint_month=args.checkpoint_month,
            trace_path=directory / "system_events.jsonl.gz",
            progress=lambda month: print(f"Crisis {month}/{args.months} months", flush=True))
        export(data, directory)
    generate_report(data, args.report, args.tests)
    print(f"Completed {data['months']} months / {data['summary']['event_count']} events", flush=True)


if __name__ == "__main__":
    main()
