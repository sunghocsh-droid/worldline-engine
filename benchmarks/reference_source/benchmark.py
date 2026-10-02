"""Isolated research replay using the production economic/project functions.

No player SQLite is touched. Daily copies/ledger IO are omitted, monthly state is
audited. Equivalence to Engine is tested separately; this is not a gameplay save.
"""
import argparse
import copy
import csv
import hashlib
import json
import math
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from . import economy, projects
from .audit import Auditor
from .models import World, Country, Region, Army, canonical_date
from .persistence import tuples

PROFILES = ("preindustrial", "early_industrial", "industrial")
PATHS = ("baseline", "infrastructure", "education_industry", "balanced", "military_heavy", "debt_boom")
BOTTLENECKS = ("unskilled_capital", "skilled_no_capital", "no_energy", "no_transport",
               "access_no_capability", "large_uneducated")
SHOCK_CASES = {"war": ("war_disruption", .9, 365), "capital": ("capital_destruction", .8, 90),
    "harvest": ("bad_harvest", .9, 365), "epidemic": ("epidemic", .9, 180),
    "blockade": ("blockade", 1, 365), "foreign": ("foreign_investment_boom", 1, 1095),
    "resource": ("resource_discovery", 1, 365)}
SEED = 1897


def reference_world(profile, calibration=None, bottleneck=None):
    """Fictional standardized starting conditions, never fitted historical GDP."""
    rank = PROFILES.index(profile)
    edu = (.12, .3, .65)[rank]
    tech = (.01, .06, .20)[rank]
    infra = (5, 20, 50)[rank]
    population = 50000 if bottleneck == "large_uneducated" else 10000
    world = World("1897-01-01", scenario_id=f"benchmark-{profile}",
        countries={c: Country(f"Synthetic {c}", population=population, treasury=1000) for c in ("a", "b")},
        regions={r: Region(r, c, population) for r, c in (("ra", "a"), ("rb", "b"))},
        armies={c: Army(c, f"r{c}", round(population * .01)) for c in ("a", "b")})
    overrides = {r: {"education": {"literacy": edu, "primary": edu * .8, "secondary": edu * .4,
        "technical": tech, "university": tech * .4}, "demography": {"urbanization": (.12, .25, .5)[rank]},
        "infrastructure": {"roads": infra, "ports": infra, "electricity": infra, "sanitation": infra,
            "communications": infra, "railways": 0 if rank == 0 else infra}} for r in ("ra", "rb")}
    if bottleneck == "unskilled_capital":
        overrides["ra"].update(capital_stock={s: population * 2 for s in economy.SECTORS},
            education=dict.fromkeys(economy.EDUCATION, 0))
    elif bottleneck == "skilled_no_capital":
        overrides["ra"].update(capital_stock={s: 1 for s in economy.SECTORS},
            education=dict.fromkeys(economy.EDUCATION, .8))
    elif bottleneck == "no_energy":
        overrides["ra"].update(capital_stock={s: (0 if s == "energy" else population) for s in economy.SECTORS}, stocks={"energy": 0})
    elif bottleneck == "no_transport":
        overrides["ra"].update(capital_stock={s: (1 if s in ("transport", "shipping") else population) for s in economy.SECTORS}, infrastructure=dict.fromkeys(projects.INFRA, 0))
    elif bottleneck == "large_uneducated":
        overrides["ra"]["education"] = dict.fromkeys(economy.EDUCATION, 0)
    economy.initialize(world, profile, overrides, calibration)
    if bottleneck == "access_no_capability":
        for s in world.economy["regions"]["ra"]["sectors"].values():
            s["technology"].update(access=True, installed=True, adoption=.9, diffusion=.8, domestic_capability=.01)
    economy.add_trade(world, "a-export", "a", "b", "light_industry", 100, "sea")
    economy.add_trade(world, "a-import", "b", "a", "mining", 100, "sea")
    return world


class Replay:
    def __init__(self, world, seed=SEED):
        self.world = copy.deepcopy(world)
        self.rng = random.Random(seed)
        self.seed = seed

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
            for _, pressure in sorted(w.pressures.items()):
                self.rng.random()  # Same daily pressure draw consumption as Engine.
        Auditor().check(w)
        return w

    def checkpoint(self, path):
        data = {"benchmark_schema": 1, "world": self.world.to_dict(), "rng": self.rng.getstate(), "seed": self.seed}
        data["sha256"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def restore(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        checksum = data.pop("sha256")
        if data.get("benchmark_schema") != 1 or hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() != checksum:
            raise ValueError("Invalid benchmark checkpoint")
        obj = cls(World.from_dict(data["world"]), data["seed"])
        obj.rng.setstate(tuples(data["rng"]))
        Auditor().check(obj.world)
        return obj


def policy_for(world, path, month):
    e = world.economy["countries"]["a"]
    revenue = e["finance"].get("total_revenue", 0)
    budget = max(0, revenue - e["finance"].get("debt_service", 0))
    # Adaptive rules use last realized revenue, not future GDP or terminal targets.
    base = {"administration": int(budget * .15), "military": int(budget * .15),
        "education": 0, "infrastructure": 0, "subsidy": 0, "welfare": 0}
    if path == "infrastructure":
        base.update(infrastructure=int(budget * .60), infrastructure_target=("railways", "ports", "roads", "electricity")[month % 4])
    elif path == "education_industry":
        base.update(education=int(budget * .28), subsidy=int(budget * .32), subsidy_sector=("machinery", "heavy_industry")[month % 2])
    elif path == "balanced":
        base.update(education=int(budget * .20), infrastructure=int(budget * .25), subsidy=int(budget * .20),
            infrastructure_target=("railways", "electricity", "ports", "sanitation", "roads")[month % 5],
            subsidy_sector=("machinery", "heavy_industry", "energy", "mining")[month % 4])
    elif path == "military_heavy":
        base.update(military=int(revenue * 1.5), subsidy=int(revenue * .5), subsidy_sector=("heavy_industry", "machinery")[month % 2])
    elif path == "debt_boom":
        base.update(infrastructure=int(revenue * 1.5), education=int(revenue * .75), subsidy=int(revenue * 1.5),
            infrastructure_target=("railways", "electricity", "ports", "sanitation")[month % 4],
            subsidy_sector=("machinery", "heavy_industry", "energy", "mining")[month % 4])
    return base


def constraints(world, country="a"):
    region = world.economy["regions"][f"r{country}"]
    result = {}
    for sector, state in region["sectors"].items():
        f = state["factors"]
        if not f: continue
        labels = []
        labels.append("capital" if f["capital_capacity"] < f["labour_capacity"] else "labour")
        if f["skilled_labour"] < .95: labels.append("skills")
        if f["input_supply"] < .95: labels.append("energy/raw_materials")
        if f["market_access"] < .7: labels.append("market/transport/debt")
        t = state["technology"]
        if t["access"] and t["domestic_capability"] < .2: labels.append("domestic_capability")
        result[sector] = labels
    return result


def measure(world, month):
    c, e = world.countries["a"], world.economy["countries"]["a"]
    r = world.economy["regions"]["ra"]
    return {"month": month, "date": world.date, "population": c.population, "gdp": c.gdp,
        "gdp_per_capita": e["gdp_per_capita"], "government_revenue": e["finance"].get("total_revenue", 0),
        "government_expenditure": e["finance"].get("total_expenditure", 0), "debt": c.debt, "reserves": c.treasury,
        "literacy": r["education"]["literacy"], "skilled_labour": r["labour"].get("skilled", 0),
        "capital_stock": sum(s["capital_stock"] for s in r["sectors"].values()),
        "infrastructure": dict(world.regions["ra"].infrastructure), "urbanization": r["demography"]["urbanization"],
        "sector_output": {s: v["output"] * 365 / e["report"]["elapsed_days"] for s, v in r["sectors"].items()},
        "technology": {s: copy.deepcopy(v["technology"]) for s, v in r["sectors"].items()},
        "labour": copy.deepcopy(r["labour"]), "trade": copy.deepcopy(e["report"]["trade"]),
        "bottlenecks": constraints(world), "explain": copy.deepcopy(e["report"]),
        "projects": {status: sum(p["status"] == status and p["country"] == "a" for p in world.projects.values()) for status in projects.STATUSES}}


def verify_explanation(world):
    e = world.economy["countries"]["a"]
    r = world.economy["regions"]["ra"]
    report = e["report"]
    assert report["gdp"] == world.countries["a"].gdp
    assert report["gdp"] == round(math.fsum(report["sector_value_added"].values()))
    assert report["finance"] == e["finance"]
    assert report["population"]["after"] == world.countries["a"].population
    for sector, s in r["sectors"].items():
        f = report["production_factors"]["ra"][sector]
        assert f == s["factors"]
        reconstructed = f["binding_capacity"] * f["elapsed_days"] / 365
        for key in ("skilled_labour", "technology", "infrastructure", "capacity_utilization", "disruption", "harvest", "resource_endowment", "unit_multiplier", "input_supply"):
            reconstructed *= f[key]
        assert math.isclose(reconstructed, s["output"], rel_tol=1e-9, abs_tol=1e-9)


def run_case(spec):
    profile, path = spec["profile"], spec.get("path", "baseline")
    replay = Replay(reference_world(profile, spec.get("calibration"), spec.get("bottleneck")))
    replay.month()  # Common first settlement to establish observed revenue.
    w = replay.world
    initial = measure(w, 0)
    records = [initial]
    shock_month = spec.get("shock_month", 24)
    horizon = spec.get("months", 360)
    checkpoints = set(spec.get("checkpoints", (12, 120, 240, 360)))
    if "shock" in spec: checkpoints |= {shock_month, shock_month + 1, shock_month + 12, shock_month + 60, shock_month + 120}
    for month in range(horizon):
        economy.change_policy(w, "a", policy_for(w, path, month))
        if "shock" in spec and month == shock_month:
            kind, severity, duration = SHOCK_CASES[spec["shock"]]
            economy.add_shock(w, "benchmark-shock", kind, "ra", severity, duration)
        replay.month()
        verify_explanation(w)
        if month + 1 in checkpoints: records.append(measure(w, month + 1))
    return {"spec": spec, "initial_sha256": hashlib.sha256(json.dumps(reference_world(profile, spec.get("calibration"), spec.get("bottleneck")).to_dict(), sort_keys=True).encode()).hexdigest(), "seed": SEED, "records": records}


def suite_specs(suite):
    specs = [{"profile": p, "path": path, "months": 360} for p in PROFILES for path in PATHS]
    if suite == "policies": return specs
    specs += [{"profile": p, "path": path, "shock": shock, "months": 144} for p in PROFILES
              for shock in SHOCK_CASES for path in ("baseline", "balanced")]
    specs += [{"profile": "industrial", "bottleneck": b, "months": 12, "checkpoints": [1, 12]} for b in BOTTLENECKS]
    for profile in PROFILES:
        for key, value in economy.PROFILES[profile].items():
            for multiplier in (.9, 1.1):
                specs.append({"profile": profile, "path": "balanced", "months": 360,
                    "calibration": {key: value * multiplier}, "sensitivity": key, "multiplier": multiplier})
    return specs


def export(cases, directory, label):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data = {"benchmark_version": 1, "model_system_version": economy.VERSION,
        "seed": SEED, "method": "same production functions, daily projects, monthly audit; no gameplay ledger IO",
        "cases": sorted(cases, key=lambda c: json.dumps(c["spec"], sort_keys=True))}
    (directory / f"{label}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ("profile", "path", "shock", "bottleneck", "sensitivity", "multiplier", "month", "population", "gdp", "gdp_per_capita", "government_revenue", "government_expenditure", "debt", "reserves", "literacy", "skilled_labour", "capital_stock", "urbanization")
    with (directory / f"{label}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in data["cases"]:
            for record in case["records"]:
                writer.writerow({key: record.get(key, case["spec"].get(key, "")) for key in fields})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmarks")
    parser.add_argument("--label", default="calibrated")
    parser.add_argument("--suite", choices=("policies", "full"), default="full")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1: parser.error("workers must be positive")
    specs, cases = suite_specs(args.suite), []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, spec): spec for spec in specs}
        for future in as_completed(futures):
            cases.append(future.result())
            print(f"{len(cases)}/{len(specs)} {futures[future]}", flush=True)
            export(cases, args.output, args.label)
    print(f"Completed {len(cases)} cases", flush=True)


if __name__ == "__main__": main()
