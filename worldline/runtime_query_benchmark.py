"""Exact-source runtime query measurements; timings never enter simulation state.

Synthetic query workloads are distinct from the actual SQLite military campaign.
The PRE package is always the requested 0779ceb Git source, never the older
crisis-v1 fixture used by other performance experiments.
"""
import argparse
import hashlib
import io
import importlib
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import tarfile
import time
from types import SimpleNamespace

from .performance_benchmark import canonical_sha256, load_baseline_modules, package_sources

BASELINE_COMMIT = "66dcf5371b3aa321522ae858eae5f9b234838bd2"
REPOSITORY = Path(__file__).resolve().parents[1]
MODULES = ("models", "engine", "economy", "state_storage", "state_runtime",
           "crisis", "crisis_state", "crisis_effects", "crisis_response",
           "crisis_benchmark", "military_benchmark")


def _inside_repository(path):
    path = Path(path).resolve()
    if not path.is_relative_to(REPOSITORY):
        raise ValueError("Runtime query evidence must stay inside the canonical repository")
    return path


def exact_reference(directory=None):
    """Verify/materialize the exact Git baseline in an isolated local package."""
    directory = _inside_repository(directory or REPOSITORY / "work/runtime_query/baseline_source/worldline")
    archive = subprocess.check_output(["git", "archive", BASELINE_COMMIT, "worldline/"], cwd=REPOSITORY)
    names = set()
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for entry in stream.getmembers():
            if not entry.isfile() or not entry.name.endswith(".py") or entry.name.count("/") != 1:
                continue
            name = Path(entry.name).name
            names.add(name)
            data = stream.extractfile(entry).read()
            path = directory / name
            if not path.exists():
                directory.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            # Windows checkout CRLF and Git blob LF compile identically. Keep
            # the existing frozen bytes/manifests unchanged while verifying Git.
            if path.read_bytes().replace(b"\r\n", b"\n") != data.replace(b"\r\n", b"\n"):
                raise ValueError("Runtime query baseline differs from exact 0779ceb Git sources")
    sources = package_sources(directory)
    if set(sources) != names:
        raise ValueError("Runtime query baseline has unverified extra sources")
    baseline = load_baseline_modules(directory, expected_sources=sources)
    return SimpleNamespace(**{name: importlib.import_module(baseline.package_name + "." + name)
                              for name in MODULES}, source_sha256=sources, commit=BASELINE_COMMIT)


def current_modules():
    return SimpleNamespace(**{name: importlib.import_module("worldline." + name) for name in MODULES},
                           source_sha256=package_sources(REPOSITORY / "worldline"), commit=None)


def measure_call(callback, *, repetitions=5, batch=1, db=None):
    """Time only calls; fingerprinting and SQL tracing are outside timed samples."""
    if type(repetitions) is not int or repetitions < 1 or type(batch) is not int or batch < 1:
        raise ValueError("Positive whole repetitions and batch required")
    expected = canonical_sha256(callback())
    seconds, counts = [], []
    for _ in range(repetitions):
        started = time.perf_counter()
        for _ in range(batch):
            result = callback()
        seconds.append(time.perf_counter() - started)
        if canonical_sha256(result) != expected:
            raise ValueError("Repeated query changed its logical result")
    if db is not None:
        statements = []
        db.set_trace_callback(statements.append)
        try:
            for _ in range(batch):
                callback()
        finally:
            db.set_trace_callback(None)
        counts = {"total": len(statements), "select": sum(
            statement.lstrip().upper().startswith("SELECT") for statement in statements)}
    return {"seconds": seconds, "median_seconds": statistics.median(seconds),
            "per_call_seconds": statistics.median(seconds) / batch, "batch": batch,
            "sql_statements": counts, "result_sha256": expected}


def archive_queries(modules, *, blobs=500, repetitions=5):
    db = sqlite3.connect(":memory:")
    try:
        storage = modules.state_storage
        storage.ensure_schema(db)
        for item in range(blobs):
            storage._store(db, {"date": "1900-01-01", "value": item, "data": [item, True, -0.0]},
                           "report", "actor", item, {None: set()})
        return {"history": measure_call(lambda: storage.history(db, kind="report", owner="actor"),
                                        repetitions=repetitions, db=db),
                "export": measure_call(lambda: storage.export_archive(db), repetitions=repetitions, db=db),
                "blobs": blobs}
    finally:
        db.close()


def metadata_queries(modules, directory, *, payload_items=20000, repetitions=5, batch=10):
    world = modules.crisis_benchmark.campaign_world()
    world.canon["query_benchmark_payload"] = list(range(payload_items))
    engine = modules.engine.Engine.create(Path(directory) / "metadata.sqlite", world, seed=941)
    def query():
        return {"orders": engine.orders, "observed": engine.observed,
                "journeys": engine.journeys, "rng": engine.rng.getstate()}
    try:
        before = canonical_sha256(engine._payload())
        result = measure_call(query, repetitions=repetitions, batch=batch, db=engine.db)
        encoded = []
        original = modules.engine.encode
        def observe_encode(value):
            text = original(value)
            encoded.append(len(text.encode("utf-8")))
            return text
        modules.engine.encode = observe_encode
        try:
            query()
        finally:
            modules.engine.encode = original
        result.update(payload_items=payload_items, encoded_bytes_per_metadata_group=sum(encoded),
                      encode_calls_per_metadata_group=len(encoded), world_unchanged=before == canonical_sha256(engine._payload()))
        if not result["world_unchanged"]:
            raise ValueError("Metadata queries changed simulation state")
        return result
    finally:
        engine.close()


def region_queries(modules, *, regions=2000, countries=20, repetitions=5, batch=20):
    world = modules.models.World("1900-01-01")
    for item in range(countries):
        world.countries[str(item)] = modules.models.Country("query country")
    for item in range(regions):
        key = "r" + str(item).zfill(6)
        world.regions[key] = modules.models.Region("query region", str(item % countries))
    world.economy = {"regions": {key: {} for key in world.regions}}
    journal = modules.state_runtime.Journal({"world": world})
    world = journal.root["world"]
    result = measure_call(lambda: {country: modules.economy.owned_regions(world, country)
                                  for country in world.countries}, repetitions=repetitions, batch=batch)
    result.update(regions=regions, countries=countries)
    return result


def cold_crisis_queries(modules, *, retained=1000, repetitions=5, batch=20):
    world = modules.crisis_benchmark.CrisisReplay().world
    event = modules.crisis_state.add_event(world, "civil_unrest", "ra", ["a"], severity=.1)
    event_id = event["event_id"]
    for item in range(retained):
        modules.crisis_effects.enqueue(world, event_id, "pressure_delta",
            {"category": "civil_unrest", "delta": 0, "country": "a"}, idempotency_key="benchmark:" + str(item))
        modules.crisis_response.queue_report(world, event_id, "a:ruler", delay=0, direct=True)
    modules.crisis_effects.apply_due(world)
    modules.crisis_response.deliver_due(world)
    journal = modules.state_runtime.Journal({"world": world})
    world = journal.root["world"]
    before = canonical_sha256(world.to_dict())
    result = {"effects": measure_call(lambda: modules.crisis_effects.apply_due(world),
                                      repetitions=repetitions, batch=batch),
              "messages": measure_call(lambda: modules.crisis_response.deliver_due(world),
                                       repetitions=repetitions, batch=batch), "retained": retained}
    result["world_unchanged"] = before == canonical_sha256(world.to_dict())
    if not result["world_unchanged"]:
        raise ValueError("Cold crisis queries changed simulation state")
    return result


def run_queries(*, baseline=False, repetitions=5, blobs=500, payload_items=20000,
                regions=2000, retained=1000):
    modules = exact_reference() if baseline else current_modules()
    scratch = REPOSITORY / "work/runtime_query"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="queries_", dir=scratch) as directory:
        workloads = {"archive": archive_queries(modules, blobs=blobs, repetitions=repetitions),
                     "metadata": metadata_queries(modules, directory, payload_items=payload_items, repetitions=repetitions),
                     "owned_regions": region_queries(modules, regions=regions, repetitions=repetitions),
                     "cold_crisis": cold_crisis_queries(modules, retained=retained, repetitions=repetitions)}
    return {"runtime_query_benchmark_schema": 1, "baseline_commit": modules.commit,
            "source_sha256": modules.source_sha256, "python": sys.version, "platform": platform.platform(),
            "repetitions": repetitions, "method": "Warm deterministic synthetic queries; five sample medians by default; "
            "SQL counts and logical fingerprints measured outside timed samples; all fixtures inside canonical repository",
            "workloads": workloads}


def compare_queries(before, after):
    dimensions = {name: all(before["workloads"][name].get(field) == after["workloads"][name].get(field)
                            for field in fields)
                  for name, fields in (("archive", ("blobs",)), ("metadata", ("payload_items",)),
                                       ("owned_regions", ("regions", "countries")), ("cold_crisis", ("retained",)))}
    changes = {}
    for name, paths in (("archive", ("history", "export")), ("metadata", (None,)),
                        ("owned_regions", (None,)), ("cold_crisis", ("effects", "messages"))):
        for path in paths:
            left = before["workloads"][name] if path is None else before["workloads"][name][path]
            right = after["workloads"][name] if path is None else after["workloads"][name][path]
            label = name if path is None else name + "." + path
            changes[label] = {"equal": left["result_sha256"] == right["result_sha256"],
                              "before_seconds": left["per_call_seconds"], "after_seconds": right["per_call_seconds"],
                              "speedup": left["per_call_seconds"] / max(right["per_call_seconds"], 1e-12),
                              "before_sql": left["sql_statements"], "after_sql": right["sql_statements"]}
    return {"equal": all(dimensions.values()) and all(value["equal"] for value in changes.values()),
            "workload_dimensions_equal": dimensions, "queries": changes}


def campaign_fingerprints(result):
    """Whole logical outputs and decision traces, with timing metadata excluded."""
    return {**{key: canonical_sha256(result["final_logical"][key]) for key in ("world", "rng", "ledger")},
            **{key: canonical_sha256(result[key]) for key in ("rows", "actions")}}


def run_campaign_comparison(directory, *, months=2, repetitions=3, seed=941, progress=None):
    """Sequential exact PRE/POST military campaigns plus real midpoint resumes.

    Each sample has its own actual SQLite database. PRE and POST use the same
    seed, scenario, runtime audits, dated campaign orders and save checkpoint.
    Extra resumed samples are correctness evidence outside the timing median.
    """
    if type(months) is not int or months < 2 or type(repetitions) is not int or repetitions < 1:
        raise ValueError("At least two whole months and positive whole repetitions required")
    if type(seed) is not int:
        raise ValueError("Campaign seed must be an integer")
    directory = _inside_repository(directory)
    baseline, optimized = exact_reference(), current_modules()
    scenarios = {}
    for scenario in optimized.military_benchmark.SCENARIOS:
        reference_fingerprints, reference_save = None, None
        samples, checks = {}, []
        for label, module in (("baseline", baseline.military_benchmark), ("optimized", optimized.military_benchmark)):
            raw_times, simulation_times, fingerprints, save_fingerprints = [], [], [], []
            for repeat in range(1, repetitions + 1):
                path = directory / scenario / label / ("run_" + str(repeat))
                result = module.run_campaign(path, scenario, months=months, seed=seed,
                    checkpoint_month=1, restore=False, validation_mode="runtime")
                logical = campaign_fingerprints(result)
                saved = (path / "midpoint.json").read_bytes()
                if reference_fingerprints is None:
                    reference_fingerprints, reference_save = logical, saved
                checks.append({"sample": label + ":" + str(repeat),
                               "logical_equal": logical == reference_fingerprints,
                               "save_bytes_equal": saved == reference_save})
                raw_times.append(result["total_seconds"])
                simulation_times.append(sum(item["seconds"] for item in result["month_timings"]))
                fingerprints.append(logical)
                save_fingerprints.append(hashlib.sha256(saved).hexdigest())
                if progress:
                    progress({"scenario": scenario, "sample": label + ":" + str(repeat),
                              "seconds": result["total_seconds"], **checks[-1]})
            samples[label] = {"seconds": raw_times, "median_seconds": statistics.median(raw_times),
                              "simulation_seconds": simulation_times,
                              "median_simulation_seconds": statistics.median(simulation_times),
                              "fingerprints": fingerprints, "midpoint_save_sha256": save_fingerprints}
        path = directory / scenario / "optimized_resume"
        resumed = optimized.military_benchmark.run_campaign(path, scenario, months=months, seed=seed,
            checkpoint_month=1, restore=True, validation_mode="runtime")
        resume_checks = {"logical_equal": campaign_fingerprints(resumed) == reference_fingerprints,
                         "save_bytes_equal": (path / "midpoint.json").read_bytes() == reference_save,
                         "actually_restored": (path / "continued.sqlite").is_file()}
        samples["resume"] = {"seconds": resumed["total_seconds"],
                             "fingerprints": campaign_fingerprints(resumed), **resume_checks}
        before, after = samples["baseline"], samples["optimized"]
        scenarios[scenario] = {"runs": samples, "checks": checks,
                               "equal": all(check["logical_equal"] and check["save_bytes_equal"] for check in checks)
                               and all(resume_checks.values()),
                               "speedup": before["median_seconds"] / max(after["median_seconds"], 1e-12),
                               "simulation_speedup": before["median_simulation_seconds"] / max(
                                   after["median_simulation_seconds"], 1e-12)}
        if progress:
            progress({"scenario": scenario, "sample": "optimized_resume", **resume_checks})
    return {"runtime_query_campaign_schema": 1, "baseline_commit": BASELINE_COMMIT,
            "baseline_source_sha256": baseline.source_sha256, "optimized_source_sha256": optimized.source_sha256,
            "months": months, "repetitions": repetitions, "seed": seed, "validation_mode": "runtime",
            "python": sys.version, "platform": platform.platform(),
            "method": "Sequential actual SQLite military Engine; same scenarios, orders, seed and runtime audits; "
            "midpoint save in every timed run; additional genuine save/load/continue outside repeated median",
            "equal": all(result["equal"] for result in scenarios.values()), "scenarios": scenarios}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--campaigns", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--directory", default="work/runtime_query/campaign_runs")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--months", type=int, default=2)
    parser.add_argument("--seed", type=int, default=941)
    args = parser.parse_args(argv)
    if args.campaigns:
        result = run_campaign_comparison(REPOSITORY / args.directory, months=args.months,
            repetitions=3 if args.repetitions is None else args.repetitions, seed=args.seed,
            progress=lambda row: print(json.dumps(row), flush=True))
        default_output = "benchmarks/runtime_query/campaign_comparison.json"
    else:
        result = run_queries(baseline=args.baseline, repetitions=5 if args.repetitions is None else args.repetitions)
        default_output = "benchmarks/runtime_query/query_baseline.json" if args.baseline else "benchmarks/runtime_query/query_optimized.json"
    path = _inside_repository(REPOSITORY / (args.output or default_output))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(path), "baseline_commit": result["baseline_commit"]}))


if __name__ == "__main__":
    main()
