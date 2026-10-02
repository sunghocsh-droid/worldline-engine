"""Observe production performance without changing rules, scheduling or randomness.

Historical source hashes remain historical. Cross-source checkpoint imports are
explicit measurement operations with a verified original manifest; they never
retag old research artifacts as results produced by new code.
"""
import argparse
import copy
import cProfile
import csv
import ctypes
import gzip
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import pstats
import random
import sys
import time
import tracemalloc
from types import SimpleNamespace
from datetime import timedelta

from . import economy, npc, npc_state
from .audit import Auditor
from .crisis_benchmark import (CrisisReplay, TraceAccumulator, campaign_config,
                              campaign_world, encode, source_hashes, measure, summarize, export)
from .models import World, canonical_date
from .persistence import tuples

SEED = 941
MILESTONES = (12, 120, 180, 240, 360)
# Each category uses an inclusive boundary, rather than adding nested timings.
PROFILE_BOUNDARIES = {
    "world_state_copying": ("copy.py", "deepcopy"),
    "npc_tick": ("npc.py", "tick"),
    "npc_view_construction": ("npc_state.py", "actor_view"),
    "report_lookup": ("npc_state.py", "_known_events"),
    "memory_lookup": ("npc_state.py", "effective_memory"),
    "memory_reevaluation": ("npc_state.py", "_memory_weight"),
    "relation_ruler_evaluation": ("npc_state.py", "evaluate_ruler"),
    "government_decision": ("npc.py", "_run_council"),
    "crisis_evaluation": ("crisis.py", "tick"),
    "event_reporting": ("crisis_response.py", "deliver_due"),
    "auditor": ("audit.py", "check"),
    "economy": ("economy.py", "tick"),
    "serialization": ("models.py", "to_dict"),
}
PROFILE_ALTERNATIVES = {
    "npc_view_construction": (("npc_state.py", "_actor_read_view"), ("npc_state.py", "actor_view")),
    "memory_lookup": (("npc_state.py", "_effective_entries"), ("npc_state.py", "effective_memory")),
}
_BASELINE_MANIFESTS = {}


def canonical_sha256(value):
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False)
    for piece in encoder.iterencode(value):
        digest.update(piece.encode("utf-8"))
    return digest.hexdigest()


def package_sources(directory):
    """Actual file hashes in one isolated package, with no compatibility aliases."""
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(directory).glob("*.py"))}


def load_baseline_modules(directory=None, expected_sources=None):
    """Isolated immutable PRE package, without replacing current worldline modules.

    All relative imports stay under a distinct package name. This is test and
    profiling infrastructure, never an alternative production decision path.
    """
    if directory is None:
        portable = Path(__file__).resolve().parents[1] / "tests/fixtures/crisis_v1_source"
        directory = portable if portable.is_dir() else Path(__file__).resolve().parents[3] / "work/crisis_v1_baseline/worldline"
    directory = Path(directory).resolve()
    if not (directory / "__init__.py").is_file():
        raise ValueError("Original baseline package is unavailable")
    sources = package_sources(directory)
    manifest = directory / "SOURCE_MANIFEST.json"
    if manifest.is_file() and sources != json.loads(manifest.read_text(encoding="utf-8"))["source_sha256"]:
        raise ValueError("Baseline package does not match its immutable fixture manifest")
    if expected_sources is not None and sources != expected_sources:
        raise ValueError("Baseline package does not match the verified original manifest")
    key = str(directory)
    if key in _BASELINE_MANIFESTS and _BASELINE_MANIFESTS[key] != sources:
        raise ValueError("Immutable baseline source changed after loading")
    _BASELINE_MANIFESTS[key] = sources
    package_name = "_worldline_performance_baseline_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(package_name, directory / "__init__.py",
            submodule_search_locations=[str(directory)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(package_name, None)
            raise
    names = ("models", "audit", "npc", "npc_state", "npc_decision", "crisis", "crisis_state",
             "crisis_effects", "crisis_response", "crisis_risk", "crisis_benchmark", "npc_benchmark",
             "economy", "projects", "engine", "persistence")
    result = {name: importlib.import_module(package_name + "." + name) for name in names}
    return SimpleNamespace(**result, package_name=package_name, source_sha256=sources, directory=directory)


def process_memory():
    """Process resident/peak bytes; None is unavailable, never fabricated zero."""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {"rss_bytes": None, "peak_rss_bytes": None, "method": "unavailable"}
        return {"rss_bytes": counters.WorkingSetSize, "peak_rss_bytes": counters.PeakWorkingSetSize,
                "method": "Windows GetProcessMemoryInfo; peak since process start"}
    try:
        resident = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        return {"rss_bytes": resident, "peak_rss_bytes": None, "method": "Linux /proc/self/statm"}
    except (OSError, ValueError, IndexError, AttributeError):
        return {"rss_bytes": None, "peak_rss_bytes": None, "method": "unavailable"}


def history_counts(world):
    n, c = world.npc_state or {}, world.crisis_state or {}
    return {"date": world.date, "countries": len(world.countries), "regions": len(world.regions),
        "persons": len(n.get("persons", {})), "events": len(c.get("events", {})),
        "active_events": sum(event["ongoing"] for event in c.get("events", {}).values()),
        "messages": len(c.get("messages", {})), "pending_decisions": len(c.get("pending_decisions", {})),
        "reports": sum(len(reports) for reports in n.get("reports", {}).values()),
        "memories": sum(len(memories) for memories in n.get("memories", {}).values()),
        "records": len(n.get("records", {})), "npc_decisions": len(n.get("decisions", [])),
        "npc_actions": len(n.get("actions", [])), "effects": len(c.get("effects", {}))}


def logical_fingerprints(world, rng, trace=None):
    """Exact canonical values, including private facts and information boundaries."""
    raw = world.to_dict()
    n, c = raw.get("npc_state", {}), raw.get("crisis_state", {})
    values = {"world": raw, "rng": rng.getstate(), "economy": raw.get("economy"),
        "npc_decisions_reason_traces": n.get("decisions", []), "npc_reports": n.get("reports", {}),
        "npc_memories": n.get("memories", {}), "npc_relationships": n.get("relationships", {}),
        "npc_groups": n.get("groups", {}), "events": c.get("events", {}),
        "event_reports": c.get("messages", {}), "pending_decisions": c.get("pending_decisions", {}),
        "event_rng_streams": c.get("streams", {})}
    if trace is not None:
        values["event_sequence"] = trace.to_dict()
    return {key: canonical_sha256(value) for key, value in values.items()}


def compare_fingerprints(before, after):
    keys = set(before) | set(after)
    details = {key: key in before and key in after and before[key] == after[key] for key in sorted(keys)}
    return {"equal": all(details.values()), "components": details,
            "differences": [key for key, equal in details.items() if not equal]}


def advance_days(replay, days, audit=True):
    """Exact production tick order for golden comparisons, including old source.

    The replay's own isolated modules are used. No sensing, event scheduling,
    random draw or state copying is inserted into simulation decisions.
    """
    if type(days) is not int or days < 0:
        raise ValueError("Comparison days must be a nonnegative integer")
    package = replay.__class__.__module__.rsplit(".", 1)[0]
    systems = [importlib.import_module(package + "." + name)
               for name in ("projects", "economy", "npc", "crisis")]
    auditor = importlib.import_module(package + ".audit").Auditor()
    for _ in range(days):
        previous = replay.world.date
        replay.world.date = (canonical_date(previous) + timedelta(days=1)).isoformat()
        replay.world.production.clear()
        event = {"previous_date": previous, "date": replay.world.date, "days": 1}
        for system in systems:
            for entry in system.tick(replay.world, copy.deepcopy(event), replay.rng) or []:
                replay.trace.observe(entry)
        for _, _pressure in sorted(replay.world.pressures.items()):
            replay.rng.random()
    if audit:
        auditor.check(replay.world)
    return replay


def compare_campaigns(before, after):
    """Compare simulation values exactly; provenance and wall time stay distinct."""
    ignored = {"source_sha256", "runtime_seconds"}
    keys = (set(before) | set(after)) - ignored
    comparison = compare_fingerprints(
        {key: canonical_sha256(before[key]) for key in keys if key in before},
        {key: canonical_sha256(after[key]) for key in keys if key in after})
    comparison["excluded_metadata"] = sorted(ignored)
    comparison["before_sources"] = before.get("source_sha256")
    comparison["after_sources"] = after.get("source_sha256")
    return comparison


def restore_original_checkpoint(path, original_sources):
    """Import verified historical logical state for an explicit PRE/POST experiment.

    The original checksum and source manifest must match. Current source hashes
    are recorded separately on the running object. No initialization draw is
    made and the original file/provenance is never modified.
    """
    if not isinstance(original_sources, dict) or not original_sources:
        raise ValueError("A verified original source manifest is required")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    checksum = payload.pop("sha256", None)
    if payload.get("crisis_campaign_schema") != 1 or checksum != canonical_sha256(payload):
        raise ValueError("Invalid original research checkpoint checksum or schema")
    if payload.get("source_sha256") != original_sources:
        raise ValueError("Original checkpoint does not match verified source manifest")
    replay = CrisisReplay.__new__(CrisisReplay)
    replay.world, replay.seed = World.from_dict(payload["world"]), payload["seed"]
    replay.rng = random.Random()
    replay.rng.setstate(tuples(payload["rng"]))
    replay.trace = TraceAccumulator.restore(payload["trace"])
    replay.source_sha256 = source_hashes()
    replay.origin_source_sha256 = copy.deepcopy(original_sources)
    Auditor().check(replay.world)
    return replay


def profile_categories(stats):
    rows = {}
    for category, (module, function) in PROFILE_BOUNDARIES.items():
        alternatives = PROFILE_ALTERNATIVES.get(category, ((module, function),))
        functions = []
        selected = None
        for filename, name in alternatives:
            matches = [(key, values) for key, values in stats.stats.items()
                       if Path(key[0]).name == filename and key[2] == name]
            functions.extend({"boundary": filename + ":" + name, "calls": values[1],
                "self_seconds": values[2], "cumulative_seconds": values[3]} for _, values in matches)
            if selected is None and matches:
                selected = filename, name, matches
        module, function, matches = selected or (module, function, [])
        rows[category] = {"boundary": module + ":" + function, "called": bool(matches),
            "calls": sum(values[1] for _, values in matches),
            "self_seconds": sum(values[2] for _, values in matches),
            "cumulative_seconds": sum(values[3] for _, values in matches), "function_rows": functions,
            "method": "First called equivalent internal/public boundary; nested alternatives are not added"}
    return rows


def profile_month(replay, output=None, measure_allocations=False):
    """One real month plus separately timed serialization; profiling only."""
    sources = source_hashes()
    opening, resident_before = history_counts(replay.world), process_memory()
    profiler = cProfile.Profile()
    if measure_allocations:
        tracemalloc.start()
    started = time.perf_counter()
    profiler.enable()
    replay.month()
    profiler.disable()
    elapsed = time.perf_counter() - started
    # Serialization is a separate measured phase, without another simulation.
    started = time.perf_counter()
    profiler.enable()
    raw = replay.world.to_dict()
    serialized = encode(raw).encode("utf-8")
    profiler.disable()
    serialization_seconds = time.perf_counter() - started
    allocations = None
    if measure_allocations:
        current, peak = tracemalloc.get_traced_memory()
        allocations = {"current_bytes": current, "peak_bytes": peak}
        tracemalloc.stop()
    stats = pstats.Stats(profiler)
    result = {"performance_schema": 1, "method": "Production month and monthly Auditor; cProfile overhead; separate serialization phase; no daily SQLite I/O",
        "source_sha256": sources, "origin_source_sha256": getattr(replay, "origin_source_sha256", sources),
        "profiled_month_seconds": elapsed, "serialization_seconds": serialization_seconds,
        "opening": opening, "closing": history_counts(replay.world),
        "memory_before": resident_before, "memory_after": process_memory(), "tracemalloc": allocations,
        "logical_json_bytes": len(serialized),
        "logical_sections_bytes": {key: len(encode(raw.get(key)).encode("utf-8")) for key in ("npc_state", "crisis_state", "economy")},
        "categories": profile_categories(stats),
        "additional_functions": [{"boundary": Path(key[0]).name + ":" + key[2], "calls": value[1],
            "self_seconds": value[2], "cumulative_seconds": value[3]}
            for key, value in stats.stats.items() if key[2] in ("_evaluation_context", "_local", "_observer_events")],
        "fingerprints": logical_fingerprints(replay.world, replay.rng, replay.trace),
        "warning": "Inclusive cumulative categories overlap and cannot be summed; instrumented time is not normal benchmark speed"}
    if source_hashes() != sources:
        raise ValueError("Production sources changed during performance profile")
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        profiler.dump_stats(str(path.with_suffix(".prof")))
        with path.with_suffix(".txt").open("w", encoding="utf-8") as stream:
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(60)
    return result


def _remap(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): _remap(item, mapping) for key, item in value.items()}
    return copy.deepcopy(value)


def scaling_scenario(countries):
    """Repeated published fictional profiles, five actors each and a trade ring.

    Four countries is exactly the established benchmark scenario. Larger worlds
    are independent performance experiments, not history or GDP calibrations.
    """
    if type(countries) is not int or countries not in (4, 10, 20):
        raise ValueError("Scaling scenarios support 4, 10 or 20 countries")
    base = campaign_world()
    config = campaign_config()
    if countries == 4:
        return base, config
    world = World(base.date, scenario_id="fictional-performance-scaling")
    world.economy = copy.deepcopy(base.economy)
    world.economy["countries"], world.economy["regions"], world.economy["trades"] = {}, {}, {}
    state = npc_state.initialize_state()
    profiles = ("a", "b", "c", "d")
    ids = ["country_" + str(index).zfill(2) for index in range(countries)]
    routes = []
    config = {"player_countries": [], "settings": copy.deepcopy(config["settings"]), "regions": {}, "borders": {}}
    for index, country in enumerate(ids):
        template = profiles[index % 4]
        next_country = ids[(index + 1) % countries]
        region = "region_" + str(index).zfill(2)
        next_region = "region_" + str((index + 1) % countries).zfill(2)
        other = profiles[(index + 1) % 4]
        mapping = {template: country, other: next_country, "r" + template: region,
            "r" + other: next_region, "army-" + template: "army-" + country,
            "initial-memory-" + template: "initial-memory-" + country,
            "event:initial-memory-" + template: "event:initial-memory-" + country}
        for table in (base.npc_state["persons"], base.npc_state["groups"]):
            for key, item in table.items():
                if item["country"] == template:
                    mapping[key] = country + key[len(template):]
        c = world.countries[country] = copy.deepcopy(base.countries[template])
        c.name += " (표본 " + str(index + 1) + ")"
        r = world.regions[region] = copy.deepcopy(base.regions["r" + template])
        r.owner, r.x = country, index * 150
        r.name += " (표본 " + str(index + 1) + ")"
        army = world.armies["army-" + country] = copy.deepcopy(base.armies["army-" + template])
        army.owner, army.region = country, region
        world.economy["countries"][country] = _remap(base.economy["countries"][template], mapping)
        world.economy["regions"][region] = _remap(base.economy["regions"]["r" + template], mapping)
        for actor, person in base.npc_state["persons"].items():
            if person["country"] != template:
                continue
            p = _remap(person, mapping)
            npc_state.add_person(state, p["id"], country, p["name"],
                **{key: value for key, value in p.items() if key not in ("id", "country", "name", "kind")})
        for group in base.npc_state["groups"].values():
            if group["country"] != template:
                continue
            g = _remap(group, mapping)
            npc_state.add_group(state, g["id"], country, g["name"], g["kind"],
                **{key: value for key, value in g.items() if key not in ("id", "country", "name", "kind")})
        government = _remap(base.npc_state["governments"][template], mapping)
        npc_state.configure_government(state, **government)
        for record in base.npc_state["records"].values():
            if record["subject"] != template + ":ruler":
                continue
            record = _remap(record, mapping)
            npc_state.record_fact(state, record["id"], record["day"], record["subject"],
                record["category"], record["facts"], record["source"])
        for actor, person in base.npc_state["persons"].items():
            if person["country"] != template:
                continue
            mapped_actor = mapping[actor]
            for topic, report in base.npc_state["reports"][actor].items():
                report = _remap(report, mapping)
                npc_state.receive_report(state, mapped_actor, mapping.get(topic, topic), **report)
            for memory in base.npc_state["memories"][actor]:
                memory = _remap(memory, mapping)
                npc_state.remember(state, mapped_actor, memory["event_id"], **{key: memory[key] for key in
                    ("interpretation", "reliability", "salience", "personal_impact", "group_impact", "valence", "tags", "enduring")})
        # The keys a/b are endpoint field names, not identifiers to rename.
        relation = {key: _remap(value, mapping) for key, value in
                    base.relations[template + "-" + other].items()}
        world.relations[country + "-" + next_country] = relation
        routes.append(("trade-" + country, country, next_country,
                       "mining" if index % 2 else "light_industry", 60, "sea"))
        config["regions"][region] = copy.deepcopy(campaign_config()["regions"]["r" + template])
        border = copy.deepcopy(campaign_config()["borders"][template + other])
        border.update(countries=[country, next_country], regions=[region, next_region])
        config["borders"]["edge-" + str(index).zfill(2)] = border
    for route in routes:
        economy.add_trade(world, *route)
    npc.initialize(world, config=state)
    Auditor().check(world)
    return world, config


def run_scaling(countries=4, months=3, seed=SEED):
    if type(months) is not int or months < 1:
        raise ValueError("Scaling period must be positive whole months")
    sources = source_hashes()
    world, config = scaling_scenario(countries)
    replay = CrisisReplay(world=world, seed=seed, config=config)
    opening, memory_before = history_counts(replay.world), process_memory()
    elapsed_months = []
    for _ in range(months):
        started = time.perf_counter()
        replay.month()
        elapsed_months.append(time.perf_counter() - started)
    result = {"performance_schema": 1, "countries": countries, "months": months, "seed": seed,
        "source_sha256": sources, "opening": opening, "closing": history_counts(replay.world),
        "month_seconds": elapsed_months, "total_seconds": sum(elapsed_months),
        "memory_before": memory_before, "memory_after": process_memory(),
        "fingerprints": logical_fingerprints(replay.world, replay.rng, replay.trace),
        "method": "Unchanged projects/economy/NPC/crisis production ticks and monthly Auditor; no SQLite writes or cProfile"}
    if source_hashes() != sources:
        raise ValueError("Production sources changed during scaling run")
    return result


def run_original_scaling(countries=4, months=3, seed=SEED, directory=None):
    """Actual PRE ticks on the same synthetic logical initial state."""
    if type(months) is not int or months < 1:
        raise ValueError("Scaling period must be positive whole months")
    baseline = load_baseline_modules(directory)
    world, config = scaling_scenario(countries)
    initial = canonical_sha256(world.to_dict())
    replay = baseline.crisis_benchmark.CrisisReplay(
        world=baseline.models.World.from_dict(world.to_dict()), seed=seed, config=config)
    opening, memory_before = history_counts(replay.world), process_memory()
    timings = []
    for _ in range(months):
        started = time.perf_counter()
        replay.month()
        timings.append(time.perf_counter() - started)
    return {"performance_schema": 1, "countries": countries, "months": months, "seed": seed,
        "source_sha256": baseline.source_sha256, "initial_sha256": initial,
        "opening": opening, "closing": history_counts(replay.world), "month_seconds": timings,
        "total_seconds": sum(timings), "memory_before": memory_before, "memory_after": process_memory(),
        "fingerprints": logical_fingerprints(replay.world, replay.rng, replay.trace),
        "method": "Actual isolated immutable PRE production ticks and monthly Auditor; no SQLite writes or cProfile"}


def _milestone(replay, month, elapsed, month_seconds):
    started = time.perf_counter()
    raw = replay.world.to_dict()
    byte_count = sum(len(piece.encode("utf-8")) for piece in
        json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False).iterencode(raw))
    return {"month": month, "years": month / 12, "date": replay.world.date,
        "simulation_seconds_so_far": elapsed, "last_month_seconds": month_seconds,
        "counts": history_counts(replay.world), "memory": process_memory(),
        "logical_json_bytes": byte_count,
        "measurement_serialization_seconds": time.perf_counter() - started,
        "world_sha256": canonical_sha256(raw), "rng_sha256": canonical_sha256(replay.rng.getstate()),
        "trace_sha256": replay.trace.digest, "trace_count": replay.trace.count}


def run_measured_campaign(directory, months=360, seed=SEED, checkpoint_month=180, progress=None):
    """Real uninterrupted POST reference, with measurement outside decisions.

    The research checkpoint is written without restoring the reference branch.
    A second actual branch can resume it with resume_measured_campaign. Timings
    for milestone serialization/checkpoint IO are separate from tick timings.
    """
    if (type(months) is not int or months <= 0 or type(checkpoint_month) is not int
            or not 0 < checkpoint_month <= months):
        raise ValueError("Valid whole campaign and checkpoint months required")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    sources = source_hashes()
    replay = CrisisReplay(seed=seed)
    initial = canonical_sha256(replay.world.to_dict())
    records = [measure(replay.world, 0)]
    timings, milestones = [], []
    wall_started = time.perf_counter()
    checkpoint_seconds = None
    with gzip.open(output / "optimized_system_events.jsonl.gz", "wt", encoding="utf-8") as sink:
        replay.trace.sink = sink
        for month in range(1, months + 1):
            started = time.perf_counter()
            replay.month()
            elapsed = time.perf_counter() - started
            timings.append(elapsed)
            records.append(measure(replay.world, month))
            if month in MILESTONES or month == months:
                milestones.append(_milestone(replay, month, sum(timings), elapsed))
            if month == checkpoint_month:
                started = time.perf_counter()
                replay.checkpoint(output / "optimized_checkpoint.json.gz")
                checkpoint_seconds = time.perf_counter() - started
            if source_hashes() != sources:
                raise ValueError("Production sources changed during measured campaign")
            if progress and month % 12 == 0:
                progress({"month": month, "date": replay.world.date,
                    "wall_seconds": time.perf_counter() - wall_started,
                    "simulation_seconds": sum(timings), "last_month_seconds": elapsed,
                    "event_count": len(replay.world.crisis_state["events"]),
                    "memory": process_memory()})
    replay.trace.sink = None
    data = {"crisis_campaign_schema": 1, "months": months, "seed": seed,
        "initial_sha256": initial, "source_sha256": sources,
        "method": "daily production projects/economy/NPC/crisis ticks and MVP pressure draws; monthly Auditor",
        "runtime_seconds": time.perf_counter() - wall_started,
        "summary": summarize(replay.world, replay.trace), "records": records,
        "trace": replay.trace.to_dict(), "final_world": replay.world.to_dict(), "final_rng": replay.rng.getstate()}
    performance = {"performance_schema": 1, "source_sha256": sources,
        "python": sys.version, "platform": platform.platform(), "seed": seed,
        "method": "Uninstrumented exact reference with compressed trace and monthly Auditor; no daily SQLite IO",
        "months": months, "checkpoint_month": checkpoint_month,
        "month_seconds": timings, "simulation_seconds": sum(timings),
        "wall_seconds": data["runtime_seconds"], "checkpoint_write_seconds": checkpoint_seconds,
        "milestones": milestones, "final_memory": process_memory(),
        "checkpoint_sha256": hashlib.sha256((output / "optimized_checkpoint.json.gz").read_bytes()).hexdigest()}
    with gzip.open(output / "optimized_campaign.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (output / "campaign_performance.json").write_text(json.dumps(performance, ensure_ascii=False, indent=2), encoding="utf-8")
    write_performance_csv(performance, output / "campaign_performance.csv")
    return data, performance


def resume_measured_campaign(directory, reference=None, progress=None):
    """A genuine midpoint research save/load branch, never a copied final result."""
    output = Path(directory)
    if reference is None:
        with gzip.open(output / "optimized_campaign.json.gz", "rt", encoding="utf-8") as stream:
            reference = json.load(stream)
    measurements = json.loads((output / "campaign_performance.json").read_text(encoding="utf-8"))
    sources = source_hashes()
    if reference["source_sha256"] != sources or measurements["source_sha256"] != sources:
        raise ValueError("Measured reference source changed before resume")
    checkpoint_month, months = measurements["checkpoint_month"], reference["months"]
    started = time.perf_counter()
    replay = CrisisReplay.restore(output / "optimized_checkpoint.json.gz")
    restore_seconds = time.perf_counter() - started
    records = copy.deepcopy(reference["records"][:checkpoint_month + 1])
    if records[-1]["date"] != replay.world.date:
        raise ValueError("Midpoint date differs from actual reference")
    wall_started = time.perf_counter()
    timings = []
    with gzip.open(output / "optimized_resume_events.jsonl.gz", "wt", encoding="utf-8") as sink:
        replay.trace.sink = sink
        for month in range(checkpoint_month + 1, months + 1):
            started = time.perf_counter()
            replay.month()
            elapsed = time.perf_counter() - started
            timings.append(elapsed)
            records.append(measure(replay.world, month))
            if source_hashes() != sources:
                raise ValueError("Production sources changed during actual resume")
            if progress and month % 12 == 0:
                progress({"month": month, "date": replay.world.date,
                    "wall_seconds": time.perf_counter() - wall_started,
                    "simulation_seconds": sum(timings), "last_month_seconds": elapsed,
                    "event_count": len(replay.world.crisis_state["events"]), "memory": process_memory()})
    replay.trace.sink = None
    data = {"crisis_campaign_schema": 1, "months": months, "seed": reference["seed"],
        "initial_sha256": reference["initial_sha256"], "source_sha256": sources, "method": reference["method"],
        "runtime_seconds": time.perf_counter() - wall_started,
        "summary": summarize(replay.world, replay.trace), "records": records,
        "trace": replay.trace.to_dict(), "final_world": replay.world.to_dict(), "final_rng": replay.rng.getstate()}
    comparison = compare_campaigns(reference, data)
    if not comparison["equal"]:
        raise ValueError("Actual midpoint replay differed: " + ", ".join(comparison["differences"]))
    with gzip.open(output / "optimized_replayed.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    result = {"performance_schema": 1, "source_sha256": sources, "checkpoint_month": checkpoint_month,
        "actually_replayed_months": months - checkpoint_month, "restore_seconds": restore_seconds,
        "wall_seconds": data["runtime_seconds"], "simulation_seconds": sum(timings), "month_seconds": timings,
        "comparison": comparison,
        "method": "Shared genuine first 15 years; current-source research save/load; actual second 15 years replayed"}
    (output / "replay_performance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return data, result


def write_performance_csv(result, path):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["month", "years", "date", "simulation_seconds_so_far",
            "last_month_seconds", "countries", "persons", "events", "reports", "memories",
            "logical_json_bytes", "rss_bytes", "peak_rss_bytes"])
        writer.writeheader()
        for row in result["milestones"]:
            writer.writerow({key: row[key] for key in ("month", "years", "date", "simulation_seconds_so_far", "last_month_seconds", "logical_json_bytes")} |
                {key: row["counts"][key] for key in ("countries", "persons", "events", "reports", "memories")} |
                {key: row["memory"][key] for key in ("rss_bytes", "peak_rss_bytes")})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", type=int, choices=(4, 10, 20), default=4)
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--output", default="benchmarks/performance/scaling.json")
    parser.add_argument("--campaign", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--original", action="store_true")
    args = parser.parse_args()
    def progress(row):
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if args.campaign:
        _, result = run_measured_campaign(args.output, months=args.months, checkpoint_month=min(180, args.months), progress=progress)
        print("Genuine uninterrupted campaign completed", flush=True)
        return
    if args.resume:
        _, result = resume_measured_campaign(args.output, progress=progress)
        print("Genuine midpoint continuation matched", flush=True)
        return
    result = (run_original_scaling if args.original else run_scaling)(args.countries, args.months)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{args.countries} countries: {args.months} real months, {result['total_seconds']:.3f}s")


if __name__ == "__main__":
    main()
