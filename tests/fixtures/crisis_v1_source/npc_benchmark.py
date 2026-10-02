"""Fictional NPC research campaign using the game's authoritative decision tick.

No historical trajectory, combat resolution, free resources or foreign truth
sensor is added. Research events are preserved outside the bounded state views.
"""
import argparse
import copy
import csv
import hashlib
import json
import math
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import economy, projects
from .audit import Auditor
from .models import World, Country, Region, Army, canonical_date
from .persistence import tuples

SEED = 731
COUNTRIES = ("a", "b", "c", "d")
PROFILES = {"a": "early_industrial", "b": "industrial", "c": "preindustrial", "d": "early_industrial"}
REGIMES = {"a": "constitutional_monarchy", "b": "military_monarchy", "c": "oligarchy", "d": "coalition"}
NAMES = {"a": "가상 개발국 A", "b": "가상 위신국 B", "c": "가상 신중국 C", "d": "가상 연합국 D"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def source_hashes():
    names = ("npc.py", "npc_state.py", "npc_decision.py", "order_execution.py", "economy.py",
             "credit.py", "guardrails.py", "labour.py", "projects.py", "calibration.py", "models.py", "audit.py", "engine.py", "persistence.py", "npc_benchmark.py")
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}


def campaign_world():
    """Scenario initialization only: four governments, sixteen fictional people."""
    from . import npc, npc_state
    world = World("1897-01-01", scenario_id="fictional-npc-campaign",
        countries={c: Country(NAMES[c], population=10000, treasury=1000,
                              stability={"a": 75, "b": 60, "c": 70, "d": 65}[c]) for c in COUNTRIES},
        regions={"r" + c: Region("가상 지역 " + c.upper(), c, 10000, index * 150, 0)
                 for index, c in enumerate(COUNTRIES)},
        armies={"army-" + c: Army(c, "r" + c, 100) for c in COUNTRIES})
    overrides = {}
    for c in COUNTRIES:
        rank = ("preindustrial", "early_industrial", "industrial").index(PROFILES[c])
        education, technical, infrastructure = (.12, .30, .65)[rank], (.01, .06, .20)[rank], (5, 20, 50)[rank]
        overrides["r" + c] = {"education": {"literacy": education, "primary": education * .8,
            "secondary": education * .4, "technical": technical, "university": technical * .4},
            "demography": {"urbanization": (.12, .25, .50)[rank]},
            "infrastructure": {k: (0 if k == "railways" and rank == 0 else infrastructure) for k in projects.INFRA}}
    economy.initialize(world, "early_industrial", overrides)
    # Existing published profile defaults are scenario data, never tuned GDPs.
    for c in COUNTRIES:
        profile = PROFILES[c]
        e = world.economy["countries"][c]
        e.update(profile=profile, calibration=dict(economy.PROFILES[profile]),
                 calibration_ranges=copy.deepcopy(economy.PROFILE_RANGES[profile]))
        demography = world.economy["regions"]["r" + c]["demography"]
        demography.update(birth_rate=economy.PROFILES[profile]["birth_rate"], death_rate=economy.PROFILES[profile]["death_rate"])
    for index, c in enumerate(COUNTRIES):
        other = COUNTRIES[(index + 1) % 4]
        world.relations[c + "-" + other] = {"a": c, "b": other, "status": "neutral",
            "trade_openness": .8 if c in ("a", "b") else 1.0,
            "trust": -.5 if c == "b" else .2, "dispute": .7 if c == "b" else .1}
        economy.add_trade(world, "trade-" + c, c, other, "mining" if index % 2 else "light_industry", 60, "sea")
    economy.enable_stabilization(world)
    state = npc_state.initialize_state()
    ruler_preferences = {
        "a": {"prosperity": .9, "education": .9, "fiscal": .7, "security": .3, "prestige": .2},
        "b": {"security": .95, "prestige": .95, "ideology": .8, "fiscal": .1, "education": .2},
        "c": {"fiscal": .95, "prosperity": .4, "education": .3, "security": .4, "prestige": .1},
        "d": {"prosperity": .6, "security": .5, "education": .6, "fiscal": .6, "group_interest": .7},
    }
    for c in COUNTRIES:
        ids = {role: c + ":" + role for role in ("ruler", "finance", "military", "education")}
        for role, actor in ids.items():
            preferences = ruler_preferences[c] if role == "ruler" else (
                {"fiscal": .95, "prosperity": .6, "security": .15} if role == "finance" else
                {"security": .95, "prestige": .8, "group_interest": .8} if role == "military" else
                {"education": .95, "prosperity": .9, "fiscal": .4})
            risk = (.85 if c == "b" else .15 if c == "c" else .35) if role == "ruler" else (.7 if role == "military" else .2)
            biases = {"overconfidence": .8, "optimism": .7, "enemy_underestimation": .65,
                      "historical_analogy_bias": .6, "group_conformity": .5} if c == "b" and role in ("ruler", "military") else {}
            npc_state.add_person(state, actor, c, f"가상 {c.upper()} {role}", role=role,
                abilities={"intelligence": .8 if role == "finance" else .5, "finance": .9 if role == "finance" else .4,
                           "military": .9 if role == "military" else .3},
                traits={"risk_tolerance": risk, "ambition": .9 if c == "b" else .5,
                        "ideological_commitment": .8 if c == "b" else .3,
                        "prestige_sensitivity": .9 if role == "military" or c == "b" else .2,
                        "patience": .2 if c == "b" else .7},
                biases=biases, preferences=preferences,
                goals=["교육과 생산능력 확대"] if role == "education" else ["국가와 소속 집단의 영향력 유지"],
                authority=["economic_policy", "war_preparation", "diplomatic_engagement"] if role == "ruler" else [],
                importance=.95 if role == "ruler" else .8 if role == "military" else .6)
        npc_state.add_group(state, c + ":development", c, "개발·재정 파벌", members=[ids["finance"], ids["education"]],
            leadership=[ids["finance"]], influence=.75 if c in ("a", "c") else .35,
            preferences={"prosperity": .8, "education": .8, "fiscal": .8}, goals=["설비·인적자본과 상환능력 보존"])
        npc_state.add_group(state, c + ":military_faction", c, "군사·위신 파벌", members=[ids["ruler"], ids["military"]],
            leadership=[ids["military"]], influence=.85 if c == "b" else .4,
            preferences={"security": .9, "prestige": .9, "group_interest": .8}, goals=["국경 안전과 군부 영향력 확대"])
        npc_state.configure_government(state, c, ids["ruler"], cabinet=[ids[r] for r in ("finance", "military", "education")], regime=REGIMES[c],
            autonomous=True, decision_rule="executive" if c == "b" else "cabinet")
        target = COUNTRIES[(COUNTRIES.index(c) + 1) % 4]
        for actor in ids.values():
            # These are received reports, not a sensor reading another country's truth.
            npc_state.receive_report(state, actor, "diplomacy",
                {"target": target, "enemy_strength": 25 if c == "b" else 100,
                 "own_strength": 200 if c == "b" else 100, "threat": .8 if c == "b" else .2,
                 "expected_ally_support": .8 if c == "b" else .3}, world.date,
                "가상 외교관의 초기 평가(불완전)", accuracy=.25 if c == "b" else .65,
                confidence=.9 if c == "b" else .6, reliability=.45 if c == "b" else .7)
    for c, category, interpretation, valence, tags in (
        ("a", "economic_performance", "이전 교육 투자에 대한 성공 경험", .6, ["development", "success"]),
        ("b", "treaty_broken", "회담 결렬은 개인과 국가의 위신에 대한 모욕", -.8, ["war_preparation", "humiliation", "revenge"]),
        ("c", "economic_performance", "과거 지급 지연에서 얻은 재정 위험의 기억", -.7, ["fiscal_consolidation", "fiscal", "loss"]),
        ("d", "treaty_kept", "연합 협의와 상호 약속을 지킨 경험", .5, ["diplomatic_engagement", "cooperation", "trust"])):
        event_id = "initial-memory-" + c
        npc_state.record_fact(state, event_id, "1896-01-01", c + ":ruler", category,
            {"event": interpretation, "participants": [c], "fictional_scenario_backstory": True}, "가상 시나리오 초기 기록")
        for actor in (c + ":ruler", c + ":finance", c + ":military", c + ":education"):
            npc_state.deliver_record(state, actor, event_id, as_of="1896-01-01", confidence=.85, reliability=.85)
            remembered_preferences = ({"security": .8, "prestige": .7} if c == "b" else
                {"fiscal": .8} if c == "c" else {"education": .7} if c == "a" else {"prosperity": .7, "security": .7})
            npc_state.remember(state, actor, event_id, interpretation={"lesson": interpretation,
                **remembered_preferences}, reliability=.85,
                salience=.9 if c == "b" else .6, personal_impact=valence * .6,
                group_impact=valence * .6, valence=valence, tags=tags, enduring=c == "b")
    npc.initialize(world, config=state)
    Auditor().check(world)
    return world


class CampaignReplay:
    """Same production ticks and RNG draws; no gameplay SQLite or ledger IO."""
    def __init__(self, world, seed=SEED):
        from . import npc
        if type(seed) is not int:
            raise ValueError("Campaign seed must be an integer")
        self.world, self.seed = copy.deepcopy(world), seed
        self.rng = random.Random(seed)
        self.events = []
        self.source_sha256 = source_hashes()
        self._npc_tick = npc.tick

    def month(self):
        w = self.world
        start = canonical_date(w.date)
        index = start.year * 12 + start.month
        target = date(index // 12, index % 12 + 1, 1)
        while canonical_date(w.date) < target:
            previous = w.date
            w.date = (canonical_date(w.date) + timedelta(days=1)).isoformat()
            w.production.clear()
            event = {"previous_date": previous, "date": w.date, "days": 1}
            projects.tick(w, event, self.rng)
            economy.tick(w, event, self.rng)
            self.events.extend(copy.deepcopy(self._npc_tick(w, event, self.rng)))
            for _, pressure in sorted(w.pressures.items()):
                self.rng.random()
        Auditor().check(w)
        return w

    def checkpoint(self, path):
        data = {"campaign_schema": 1, "world": self.world.to_dict(), "seed": self.seed,
                "rng": self.rng.getstate(), "events": self.events, "source_sha256": self.source_sha256}
        data["sha256"] = hashlib.sha256(encode(data).encode()).hexdigest()
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    @classmethod
    def restore(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        checksum = data.pop("sha256")
        if data.get("campaign_schema") != 1 or hashlib.sha256(encode(data).encode()).hexdigest() != checksum:
            raise ValueError("Invalid NPC campaign checkpoint")
        if data["source_sha256"] != source_hashes():
            raise ValueError("NPC campaign source changed; use the matching research version")
        replay = cls(World.from_dict(data["world"]), data["seed"])
        replay.rng.setstate(tuples(data["rng"]))
        replay.events = data["events"]
        Auditor().check(replay.world)
        return replay


def verify_decision(decision):
    """Explanation scores must be sums of the actual contributions shown."""
    for evaluation in decision.get("evaluations", []):
        contributions = [r["contribution"] for r in evaluation.get("reason_trace", [])]
        if contributions and not math.isclose(math.fsum(contributions), evaluation["score"], rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("NPC explanation does not reconstruct its decision score")
    selected = decision.get("selected")
    if selected is not None:
        candidates = [e for e in decision["evaluations"] if e["option"] == selected]
        if len(candidates) != 1:
            raise ValueError("NPC selected an option absent from its evaluated choices")
        if decision.get("reason_trace") != candidates[0].get("reason_trace"):
            raise ValueError("NPC selected explanation differs from actual scoring trace")


def measure(world, month):
    result = {"month": month, "date": world.date, "countries": {}, "npc_counts": {}}
    state = world.npc_state
    result["npc_counts"] = {key: len(state.get(key, {})) for key in ("persons", "groups", "records", "decisions", "actions")}
    result["npc_counts"].update(reports=sum(len(v) for v in state["reports"].values()),
                                 memories=sum(len(v) for v in state["memories"].values()))
    for c in COUNTRIES:
        country, e = world.countries[c], world.economy["countries"][c]
        r = world.economy["regions"]["r" + c]
        result["countries"][c] = {"population": country.population, "gdp": country.gdp,
            "gdp_per_capita": e["gdp_per_capita"], "debt": country.debt, "reserves": country.treasury,
            "creditworthiness": e["credit"]["creditworthiness"], "arrears": e["credit"]["arrears"],
            "distress_state": e["credit"]["distress_state"], "finance": copy.deepcopy(e["finance"]),
            "labour": copy.deepcopy(e["labour"]), "literacy": r["education"]["literacy"],
            "capital_stock": math.fsum(s["capital_stock"] for s in r["sectors"].values()),
            "infrastructure": dict(world.regions["r" + c].infrastructure), "bottlenecks": economy.bottlenecks(world, c),
            "policy": copy.deepcopy(e["policy"]), "projects": Counter(p["status"] for p in world.projects.values() if p["country"] == c),
            "ruler_evaluations": {a: {**{key: value for key, value in rel.get(c + ":ruler", {}).items() if key != "causes"},
                "cause_count": len(rel.get(c + ":ruler", {}).get("causes", []))}
                for a, rel in state["relationships"].items() if state["persons"][a]["country"] == c}}
    return result


def summarize(events):
    choices, actors, countries, action_types, rejection_reasons, factors = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    decisions, actions, examples, action_examples, country_actions = [], [], {}, {}, Counter()
    for event in events:
        if event.get("kind") == "npc_decision":
            decision = event["decision"]
            verify_decision(decision)
            decisions.append(decision)
            actors[decision["actor"]] += 1
            countries[decision["country"]] += 1
            choice = decision.get("selected") or "none"
            choices[choice] += 1
            for reason in decision.get("reason_trace", []):
                factors[reason["factor"]] += 1
            if choice not in examples and decision.get("selected") is not None:
                examples[choice] = copy.deepcopy(decision)
        elif event.get("kind") == "npc_action":
            action = event["action"]
            actions.append(action)
            action_types[action.get("kind", action.get("option_kind", "unknown"))] += 1
            execution = action.get("execution", {})
            country_actions[action["country"] + ":" + action["option"] + ":" + execution.get("status", "unknown")] += 1
            action_examples.setdefault(action["option"], copy.deepcopy(action))
            if execution.get("status") in ("rejected", "failed"):
                rejection_reasons[str(execution.get("reason", execution.get("error", "unspecified")))] += 1
    return {"decision_count": len(decisions), "action_count": len(actions), "choices": dict(choices),
        "actor_decisions": dict(actors), "country_decisions": dict(countries), "action_types": dict(action_types),
        "rejection_reasons": dict(rejection_reasons), "scoring_factors": dict(factors), "examples": examples,
        "action_examples": action_examples, "country_actions": dict(country_actions),
        "combat_resolutions": 0, "note": "War preparation/military expenditure are funded intents; no battle or war outcome simulator."}


def run_campaign(months=240, seed=SEED, checkpoint_path=None, checkpoint_month=None, progress=None):
    if type(months) is not int or months <= 0:
        raise ValueError("Campaign horizon must be positive whole months")
    replay = CampaignReplay(campaign_world(), seed)
    initial_hash = hashlib.sha256(encode(replay.world.to_dict()).encode()).hexdigest()
    records = [measure(replay.world, 0)]
    for month in range(months):
        replay.month()
        records.append(measure(replay.world, month + 1))
        if checkpoint_path is not None and month + 1 == checkpoint_month:
            replay.checkpoint(checkpoint_path)
            replay = CampaignReplay.restore(checkpoint_path)
        if progress is not None and (month + 1) % 12 == 0:
            progress(month + 1)
    if source_hashes() != replay.source_sha256:
        raise ValueError("NPC campaign source changed while running; restart")
    return {"campaign_schema": 1, "months": months, "seed": seed, "initial_sha256": initial_hash,
        "source_sha256": replay.source_sha256, "method": "daily projects/economy/NPC/pressure draws; monthly audit; no SQLite IO or combat stub",
        "summary": summarize(replay.events), "records": records, "events": replay.events,
        "final_world": replay.world.to_dict(), "final_rng": replay.rng.getstate()}


def export(data, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Full option traces make indentation very costly in a long campaign.
    # Preserve every value; the Markdown/CSV are the human-facing views.
    with (directory / "campaign.json").open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    fields = ("month", "date", "country", "population", "gdp", "gdp_per_capita", "debt", "reserves", "creditworthiness", "arrears", "distress_state", "literacy", "capital_stock")
    with (directory / "campaign.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in data["records"]:
            for country, row in record["countries"].items():
                values = {**row, "country": country, "month": record["month"], "date": record["date"]}
                writer.writerow({key: values[key] for key in fields})


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def generate_report(data, destination, tests="최종 테스트 대기", before_path="benchmarks/npc/before_fix/campaign.json"):
    records, summary = data["records"], data["summary"]
    rows = []
    for c in COUNTRIES:
        for m in (1, 120, 240):
            matches = [r for r in records if r["month"] == m]
            if not matches: continue
            r = matches[0]["countries"][c]
            rows.append([NAMES[c], "1개월" if m == 1 else f"{m // 12}년", f'{r["population"]:,}', f'{r["gdp"]:,}', f'{r["debt"]:,}',
                f'{r["reserves"]:,}', f'{r["creditworthiness"]:.1f}', f'{r["arrears"]:,}', r["distress_state"], f'{r["literacy"]*100:.1f}%'])
    actor_rows = [[actor, count] for actor, count in sorted(summary["actor_decisions"].items())]
    text = ["# NPC_SIMULATION_REPORT\n", "가상 4개국 NPC 자율 의사결정 캠페인\n",
        f"실제로 진행한 기간: **{data['months']/12:g}년**. seed: **{data['seed']}**. pytest 최종 결과: **{tests}**.\n",
        "## 검증 범위\n",
        "A/B/C/D는 역사 국가가 아닌 가상국이며, 각 인구 10,000·국고 1,000·상비군 100에서 시작한다. 기존 preindustrial/early_industrial/industrial profile의 공개 기본값을 사용했다. 경제계수·GDP 목표·전쟁 결과를 국가별로 지정하지 않았다. 교육·인프라·정부 제도·파벌 영향력·인물의 선호와 편향·받은 보고·과거 경험이 초기조건으로 다르다.\n",
        "A는 개발과 교육을 중시하는 내각, B는 군사 위신과 위험 감수를 중시하는 군주, C는 상환능력을 중시하는 과두정부, D는 외교와 협의를 중시하는 연합 내각이다. 각 정부에는 군주/재정/군사/교육 담당자와 개발·군사 파벌이 있다. 이름 자체는 계산에 보너스를 주지 않는다.\n",
        "특히 B는 상대 병력을 실제보다 낮게 평가한 불완전한 외교관 보고를 받는다. 객관적 기록과 그 기록을 인물이 어떻게 기억하는지를 분리했다. 전쟁준비와 군사·외교 압박을 선택할 수 있지만, 이번 캠페인은 전투 판정이나 전쟁 승패를 구현하지 않는다. **전투 해결 횟수는 0**이며 전쟁준비를 실제 전쟁 발생으로 세지 않는다.\n",
        "## 구조 검증에서 고친 문제\n",
        "단기 실행으로 발견한 결함마다 회귀 검사를 추가했다. 월별 보고일과 내각 회의일이 어긋나 회의가 누락되는 문제, 국가 사이 행동 ID 중복, 군주의 중복 투표, 비어 있는 군주·평가일의 실행 오류, 실제 지출을 줄이는 미래 예산 결정까지 당장의 자금 부족으로 막는 문제를 검사한다. 정부의 정책 승인과 월별 실제 지급을 분리하고, 실제 자원·자금 제약은 지급·건설·동원 단계에 계속 적용한다.\n",
        "경제 검사용 복사에는 경제 계산에 필요 없는 NPC 과거 기록을 제외해 장기 실행 비용을 줄였다. 경제계수나 GDP 목표를 조정하는 방식은 사용하지 않았다.\n",
        "## 경제와 재정 결과\n", table(["국가", "경과기간", "인구", "GDP", "부채", "국고", "신용/100", "미지급", "재정상태", "문해율"], rows), "\n",
        "## 실제 결정의 다양성\n",
        f"인물 판단 {summary['decision_count']:,}건, 정부 실행 시도 {summary['action_count']:,}건을 기록했다. 실행권한이 없는 인물의 제안은 국가 명령과 구분한다.\n",
        table(["선택 종류", "인물 선택 횟수"], sorted(summary["choices"].items())), "\n",
        table(["국가:명령:실행 결과", "횟수"], sorted(summary["country_actions"].items())), "\n",
        table(["인물", "판단 횟수"], actor_rows), "\n",
        "위 집계는 엔진이 반환한 실제 판단과 실행 사건에서 계산했다. 위험한 선택을 없애기 위해 경제 위험을 행동금지로 바꾸지 않았다. 실제 부족한 사람·자재·조달수단 때문에 실행이 불가능하면 이유를 기록한다. 정책의 의도·제안·정부 승인·실제 실행을 혼동하지 않는다.\n",
        "## 개인의 제안과 정부의 최종 승인\n"]
    coalition_rows = []
    for action in summary["action_examples"].values():
        c, votes = action["country"], action.get("coalition", {})
        coalition_rows.append([c, votes.get(c + ":ruler", ""), votes.get(c + ":finance", ""),
            votes.get(c + ":military", ""), votes.get(c + ":education", ""), action["option"], action["execution"]["status"]])
    text += [table(["국가", "군주 제안", "재정 제안", "군사 제안", "교육 제안", "정부 승인", "실제 실행"], coalition_rows), "\n",
        "각 종류의 첫 실행 사례를 사용했다. 같은 국가에 속해도 인물·파벌의 이해관계가 달라 서로 다른 안을 낸다. 내각은 구성원의 영향력과 지지를 계산하고, 집행권한을 가진 인물이 실제 명령을 내린다. 군주가 직접 결정하는 정부에서는 반대 의견이 기록돼도 군주가 위험한 선택을 승인할 수 있다.\n",
        "## 군주를 평가하는 서로 다른 기준\n"]
    ruler_rows = []
    for c in COUNTRIES:
        for actor, relation in sorted(records[-1]["countries"][c]["ruler_evaluations"].items()):
            if relation:
                ruler_rows.append([actor, f'{relation.get("affection",0):.3f}', f'{relation.get("respect",0):.3f}',
                    f'{relation.get("trust",0):.3f}', f'{relation.get("fear",0):.3f}',
                    f'{relation.get("perceived_legitimacy",0):.3f}', f'{relation.get("grievance",0):.3f}'])
    text += [table(["평가자", "호감", "존경", "신뢰", "두려움", "정당성 인식", "원한"], ruler_rows), "\n",
        "같은 군주라도 평가자의 경험과 이해관계에 따라 다른 값이 나온다. 호감·존경·신뢰·복종은 서로 다른 기준으로 유지한다. 최종 World에는 그 값을 만든 알려진 사건과 원인이 보존된다.\n",
        "후계자의 경우 선대의 실제 업적 기록은 후계자 기록으로 복사되지 않는다. 관찰자가 알고 있는 선대의 성과와 개인적 유산은 기대 수준·비교 기준·왕조 정당성 인식에 영향을 줄 수 있다. 이 캠페인에서는 왕위 승계 사건을 삽입하지 않았으며, 인기 있던 선대·악평 받던 선대·기대 대비 실제 후계자 성과는 별도 자동 테스트로 검증한다.\n",
        "## 선택을 설명하는 내부 근거\n"]
    for kind, example in summary["examples"].items():
        traces = [[r["factor"], f'{r["contribution"]:+.4f}', str(r.get("detail", ""))[:180].replace("|", "/")] for r in example["reason_trace"]]
        text += [f"### {kind}: {example['actor']} / {example.get('date','')}\n",
            table(["요인", "점수 기여", "근거"], traces), "\n"]
    text += ["각 평가점수는 표시한 reason_trace 기여 합과 일치하는지 자동 검사한다. 선택된 설명 역시 같은 평가의 설명을 복사한 값이다. 보고서의 예시는 원시 사건을 축약한 것이며, 상세 입력·perceived 비용/위험·제약·정부 승인·실제 명령 결과는 campaign.json에 있다.\n",
        "## 지도부 예상과 실제 경제 제약\n"]
    cost_rows = []
    for choice, action in summary["action_examples"].items():
        actual, perceived = action.get("actual", {}), action.get("perceived", {})
        truth = actual.get("true_state", {})
        if truth:
            cost_rows.append([action["country"], choice, f'{perceived.get("cost",0):.1f}',
                f'{truth.get("cost",0):.1f}', f'{perceived.get("risk",0):.3f}', f'{truth.get("risk",0):.3f}',
                action["execution"]["status"]])
    text += [table(["국가", "명령", "예상 비용", "실제 상태 기준 견적", "예상 위험", "실제 상태 위험", "실행"], cost_rows), "\n",
        "위 표는 첫 실행 시도의 서로 다른 두 견적을 보여준다. 실제 상태 기준 견적은 현재 실제 세계·재정·조달수단으로 계산한 요청 비용이며, 예산 정책은 향후 월별 지급 과정에서 실제 지급액과 미지급액이 다시 정해진다. 객관적 위험은 해당 명령의 조달 견적이고, 예상 위험은 지도부가 받은 국내 재정 보고·능력·편향을 적용한 값이다. 위험 점수는 상대적 모델 위험을 표현한다.\n",
        "## 수정 전후 단기 비교\n"]
    before_path = Path(before_path) if before_path else None
    if before_path and before_path.exists() and data["months"] >= 3:
        before = json.loads(before_path.read_text(encoding="utf-8"))
        if before["months"] == 3 and before["seed"] == data["seed"] and before["initial_sha256"] == data["initial_sha256"]:
            cutoff = next(r["date"] for r in records if r["month"] == 3)
            after_events = [e for e in data["events"] if e.get("action", e.get("decision", {})).get("date", cutoff) <= cutoff]
            after = summarize(after_events)
            comparison = [[label, item["decision_count"], item["action_count"]]
                for label, item in (("수정 전 3개월", before["summary"]), ("수정 후 같은 3개월", after))]
            text += [table(["실험", "인물 판단", "정부 실행 시도"], comparison), "\n",
                "이 비교는 동일한 가상국·seed의 첫 3개월을 사용한다. 종료일에도 날짜 진행과 월별 결산에 따른 판단이 실행된다. 수정 전 20년 결과를 완성했다고 주장하지 않는다. 장기 실행 도중 단기 결함을 확인해 그 실행을 중단했고, 수정 전 원시 자료는 before_fix 폴더에 따로 보존했다.\n"]
        else:
            text += ["수정 전 자료의 기간·seed·초기 세계 해시가 이번 실행과 달라 직접 전후 비교하지 않았다.\n"]
    else:
        text += ["수정 전후 비교는 같은 초기조건의 최소 3개월 실행 결과가 있을 때 표시한다.\n"]
    text += ["## 재현성 및 정보 분리\n",
        "모든 판단의 난수는 게임 RNG를 사용한다. 프로젝트→경제→NPC→압력 순서로 실제 tick 함수를 호출하고 매월 세계 validator를 실행한다. 연구 실행은 SQLite IO만 생략하며, 연구 체크포인트는 World·RNG·외부 수집 사건·코드 해시를 보존한다. JSON 체크섬이나 코드 버전이 다르면 조용히 계속하지 않는다. 실제 게임 저장본 호환성과 실행 순서는 별도 Engine 테스트로 검증한다.\n",
        "NPC 판단에는 명시적으로 받은 보고와 행위자의 기억만 전달한다. 다른 국가의 실제 세계 상태를 무료로 읽는 센서를 추가하지 않았다. trusted 실행 계층이 확인하는 실제 자원·비용과 인물이 예상하는 비용·위험을 분리한다. 경제 엔진이 정치적으로 위험하다는 이유로 선택을 스스로 취소하지 않는다.\n",
        "## 한계\n",
        "이 실험은 네 가상정부의 하나의 초기 배치와 seed에 대한 검증이다. 모든 시대·국가·제도·전쟁 선택의 역사적 현실성을 입증하지 않는다. 전투, 군사 AI, 외교 승패, 혁명·쿠데타·자동 위기, 가격·환율, 실제 1897 국가자료, MCP와 UI는 추가하지 않았다. 실제 위협·전쟁 결과가 NPC의 기억과 판단에 전달되는 후속 통합은 별도의 검증이 필요하다.\n",
        "이번 국내 보고는 정부 구성원에게 제공되는 단순화된 행정 집계다. 더 세밀한 신분·지역별 정보 접근, 첩보·통신망·보고 지연의 생성, 더 넓은 정책 대안과 그 효과 예측은 후속 확장 대상이다. 판단 점수는 인물이 예상하는 방향성을 사용하고, 실제 결과는 경제 엔진의 별도 계산으로 검증한다.\n",
        "원시 JSON에는 매월 경제 지표와 모든 중요한 NPC 사건, 최종 세계·RNG가 들어 있다. 현재 세계의 판단/실행 표시 목록은 유한한 최근 기록만 보관하고, 연구 로그와 게임 Event Ledger가 영구 사건 기록을 담당한다. 새로운 정부·인물·집단·기억을 추가해 같은 harness를 재사용할 수 있다.\n",
        "장기 기록량은 여전히 늘어난다. 판단 기록에 그 시점의 보고 메타데이터가 포함되므로 캠페인이 더 길어지면 보고 참조를 별도 저장하고 압축하는 검토가 필요하다. 중요한 사건이나 평생 기억을 임의로 삭제해 속도를 맞추지는 않았다.\n"]
    Path(destination).write_text("\n".join(text), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=240)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", default="benchmarks/npc")
    parser.add_argument("--report", default="NPC_SIMULATION_REPORT.md")
    parser.add_argument("--tests", default="최종 테스트 대기")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--checkpoint-month", type=int)
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        data = json.loads((directory / "campaign.json").read_text(encoding="utf-8"))
    else:
        data = run_campaign(args.months, args.seed,
            checkpoint_path=directory / "research_checkpoint.json" if args.checkpoint_month else None,
            checkpoint_month=args.checkpoint_month,
            progress=lambda month: print(f"{month}/{args.months} months", flush=True))
        export(data, directory)
    generate_report(data, args.report, args.tests)
    print(f"Completed {data['months']} months / {data['summary']['decision_count']} decisions", flush=True)


if __name__ == "__main__":
    main()
