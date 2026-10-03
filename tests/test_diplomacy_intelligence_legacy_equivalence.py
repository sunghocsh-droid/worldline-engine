"""Feature-off results are exact against independent stable f492 source.

The immutable oracle is exported directly from Git, never by relabelling cache
hashes. Each isolated interpreter performs five days over a monthly boundary,
including actual save/load/continue; no long campaign or benchmark is run.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

import pytest

STABLE_COMMIT = "f492d755ff30abe79943c3f73d134263adf92f8c"
CASES = ("mvp_save2", "economy_save2", "stabilized_save3", "npc_save4",
         "crisis_save5", "military_save6", "legacy_save1")

_PROBE = r'''"""Short exact regression evidence; never enable D&I or run long campaigns.

Run in separate Python subprocesses with package_root explicitly prepended.
All writes are limited to the given scratch evidence directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument("package_root", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = args.package_root.resolve()
sys.path.insert(0, str(root))
from worldline import Engine, World, Country, Region, Army, Report
from worldline import npc_state, state_storage
from worldline.engine import encode

assert Path(sys.modules["worldline"].__file__).resolve().parent == root / "worldline"
args.output.mkdir(parents=True, exist_ok=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def initial_world():
    return World("1897-01-29", scenario_id="stage1-feature-off-evidence",
        countries={"a": Country("A", population=10000, treasury=1000),
                   "b": Country("B", population=10000, treasury=1000)},
        regions={"ra": Region("A region", "a", 10000),
                 "rb": Region("B region", "b", 10000, 100, 0)},
        armies={"army-a": Army("a", "ra", 100), "army-b": Army("b", "rb", 100)},
        relations={"opaque-import-pair": {"a": "a", "b": "b", "status": "neutral", "trust": .1,
                                            "trade_openness": .5}})


def configuration():
    state = npc_state.initialize_state()
    for country in ("a", "b"):
        leader = country + ":ruler"
        npc_state.add_person(state, leader, country, "Evidence ruler " + country,
            role="ruler", authority=["economic_policy", "fiscal_action", "mobilize", "diplomatic_engagement"],
            traits={"risk_tolerance": .4}, abilities={"diplomacy": .6})
        npc_state.configure_government(state, country, leader, [], autonomous=True, decision_rule="executive")
    return state


def capture(engine):
    world = engine.true_state.to_dict()
    assert "diplomacy_state" not in world and "intelligence_state" not in world
    result = {"world": world, "rng": engine.rng.getstate(), "ledger": engine.ledger(),
              "archive": state_storage.export_archive(engine.db),
              "manifest": engine._stored_payload().get("systems", []),
              "observed": engine.observed_state("evidence-player"),
              "orders": engine.orders, "journeys": engine.journeys}
    if world.get("npc_state"):
        result["npc_views"] = {person: engine.npc_view(person) for person in sorted(world["npc_state"]["persons"])}
    return result


summaries = {}
for case in ("mvp_save2", "economy_save2", "stabilized_save3", "npc_save4", "crisis_save5", "military_save6"):
    directory = args.output / case
    directory.mkdir()
    engine = Engine.create(directory / "initial.sqlite", initial_world(), seed=1731)
    try:
        if case != "mvp_save2":
            engine.enable_economy()
        if case not in ("mvp_save2", "economy_save2"):
            engine.stabilize_economy()
        if case in ("npc_save4", "crisis_save5", "military_save6"):
            engine.enable_npc(configuration())
            engine.npc_report("a:ruler", "diplomacy", {"target": "b", "own_strength": 100,
                "enemy_strength": 100, "threat": .2}, source="explicit evidence observation",
                confidence=.7, reliability=.8, accuracy=.4)
            engine.npc_outcome("a:ruler", "economic_performance", {"performance": .1},
                observers=["a:ruler"], salience=.6)
        if case in ("crisis_save5", "military_save6"):
            engine.enable_crisis({"settings": {"auto_enabled": False}, "player_countries": ["a"]})
        if case == "military_save6":
            engine.enable_military()
        engine.record_observation("evidence-player", "public-account", Report("1897-01-29",
            "permitted domestic report", .8, {"treasury_report": 1000}))
        opening = capture(engine)
        try:
            engine.record_observation("evidence-player", "forbidden-future", Report("1999-01-01",
                "future report", .8, {"treasury_report": -1000}))
        except ValueError as error:
            rejected_api_result = {"error_type": type(error).__name__, "message": str(error)}
        else:
            raise AssertionError("Future observation must be rejected")
        assert encode(capture(engine)) == encode(opening)
        detached = engine.observed_state("evidence-player")
        detached["public-account"]["data"]["treasury_report"] = -999
        if opening["world"].get("npc_state"):
            engine.npc_view("a:ruler").clear()
        assert encode(capture(engine)) == encode(opening)
        engine.advance(3, "day")
        midpoint = capture(engine)
        saved = engine.save(directory / "midpoint.json")
        resumed = Engine.load(saved, directory / "resumed.sqlite")
        try:
            assert encode(capture(resumed)) == encode(midpoint)
            engine.advance(2, "day")
            resumed.advance(2, "day")
            closing = capture(engine)
            assert encode(capture(resumed)) == encode(closing)
            original_final = engine.save(directory / "final.json")
            resumed_final = resumed.save(directory / "resumed-final.json")
            assert original_final.read_bytes() == resumed_final.read_bytes()
            projection = {"opening": opening, "midpoint": midpoint, "closing": closing,
                          "rejected_api_result": rejected_api_result,
                          "midpoint_save_sha256": sha(saved.read_bytes()),
                          "final_save_sha256": sha(original_final.read_bytes())}
            (directory / "projection.json").write_text(encode(projection), encoding="utf-8")
            envelope = json.loads(saved.read_text(encoding="utf-8"))
            summaries[case] = {"projection_sha256": sha(encode(projection).encode()),
                "save_schema": envelope["payload"]["schema_version"],
                "world_schema": midpoint["world"]["schema_version"],
                "archive_present": midpoint["archive"] is not None,
                "ledger_entries": len(closing["ledger"]),
                "save_load_continue_exact": True,
                "final_save_bytes_exact": True}
        finally:
            resumed.close()
    finally:
        engine.close()

# A real legacy schema-1 snapshot follows baseline normalization; compare outputs.
legacy_dir = args.output / "legacy_save1"
legacy_dir.mkdir()
initial = Engine.create(legacy_dir / "source.sqlite", initial_world(), seed=91)
try:
    source = initial.save(legacy_dir / "source.json")
finally:
    initial.close()
legacy = json.loads(source.read_text(encoding="utf-8"))
legacy["payload"]["schema_version"] = 1
legacy["payload"]["engine"]["world"]["schema_version"] = 1
legacy["sha256"] = sha(encode(legacy["payload"]).encode())
legacy_path = legacy_dir / "input-save1.json"
legacy_path.write_text(encode(legacy), encoding="utf-8")
loaded = Engine.load(legacy_path, legacy_dir / "loaded.sqlite")
try:
    opening = capture(loaded)
    loaded.advance(2, "day")
    saved = loaded.save(legacy_dir / "normalized.json")
    projection = {"opening": opening, "closing": capture(loaded), "save_sha256": sha(saved.read_bytes())}
    (legacy_dir / "projection.json").write_text(encode(projection), encoding="utf-8")
    summaries["legacy_save1"] = {"projection_sha256": sha(encode(projection).encode()),
        "normalized_save_schema": json.loads(saved.read_text(encoding="utf-8"))["payload"]["schema_version"],
        "diplomacy_auto_enabled": False}
finally:
    loaded.close()

manifest = {p.name: sha(p.read_bytes()) for p in sorted((root / "worldline").glob("*.py"))}
(args.output / "evidence.json").write_text(json.dumps({"source_manifest": manifest, "cases": summaries,
    "maximum_progress": "five days across one monthly boundary per case; schema1 two days",
    "diplomacy_enabled": False}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summaries, ensure_ascii=False, indent=2))
'''

_CROSSLOAD = r'''import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument("package_root", type=Path)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = args.package_root.resolve()
sys.path.insert(0, str(root))
from worldline import Engine
assert Path(sys.modules["worldline"].__file__).resolve().parent == root / "worldline"
args.output.mkdir()
results = {}
for case in ("mvp_save2", "economy_save2", "stabilized_save3", "npc_save4", "crisis_save5", "military_save6", "legacy_save1"):
    source = args.source / case
    filename = "input-save1.json" if case == "legacy_save1" else "midpoint.json"
    expected = source / ("normalized.json" if case == "legacy_save1" else "final.json")
    engine = Engine.load(source / filename, args.output / (case + ".sqlite"))
    try:
        world = engine.true_state.to_dict()
        assert "diplomacy_state" not in world and "intelligence_state" not in world
        engine.advance(2, "day")
        saved = engine.save(args.output / (case + ".json"))
        assert saved.read_bytes() == expected.read_bytes(), case
        results[case] = {"exact_continued_save_bytes": True,
            "sha256": hashlib.sha256(saved.read_bytes()).hexdigest()}
    finally:
        engine.close()
(args.output / "evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
'''

def _source_manifest(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((directory / "worldline").glob("*.py"))}


def _run(script, *arguments):
    completed = subprocess.run([sys.executable, str(script), *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


@pytest.fixture(scope="module")
def stable_equivalence_evidence():
    repository = Path(__file__).resolve().parents[1]
    evidence = repository / "work" / "diplomacy_stage1" / ("equivalence-" + uuid.uuid4().hex)
    evidence.mkdir(parents=True)
    oracle = evidence / "baseline_source"
    oracle.mkdir()
    archive = evidence / "stable_source.zip"
    exported = subprocess.run(["git", "-c", "safe.directory=" + repository.as_posix(),
        "archive", "--format=zip", "--output=" + str(archive), STABLE_COMMIT, "worldline"],
        cwd=repository, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert exported.returncode == 0, exported.stdout + exported.stderr
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(oracle)
    source_before = _source_manifest(oracle)
    script = evidence / "short_probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    baseline, candidate = evidence / "baseline", evidence / "candidate"
    (evidence / "baseline_stdout.txt").write_text(_run(script, oracle, baseline), encoding="utf-8")
    (evidence / "candidate_stdout.txt").write_text(_run(script, repository, candidate), encoding="utf-8")
    assert _source_manifest(oracle) == source_before
    cross = evidence / "cross_load.py"
    cross.write_text(_CROSSLOAD, encoding="utf-8")
    _run(cross, repository, baseline, evidence / "baseline_save_current_load")
    _run(cross, oracle, candidate, evidence / "current_save_baseline_load")
    assert _source_manifest(oracle) == source_before
    (evidence / "provenance.json").write_text(json.dumps({"stable_commit": STABLE_COMMIT,
        "baseline_source_manifest": source_before, "baseline_source_unchanged": True,
        "baseline_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "candidate_source_manifest": _source_manifest(repository),
        "scope": "feature off; five days; save1-6; isolated subprocesses; no long campaign"}, indent=2),
        encoding="utf-8")
    return evidence


@pytest.mark.parametrize("case", CASES)
def test_feature_off_exact_world_rng_ledger_archive_manifest_views_save_bytes(stable_equivalence_evidence, case):
    evidence = stable_equivalence_evidence
    baseline = evidence / "baseline" / case / "projection.json"
    candidate = evidence / "candidate" / case / "projection.json"
    assert candidate.read_bytes() == baseline.read_bytes()


@pytest.mark.parametrize("direction", ["baseline_save_current_load", "current_save_baseline_load"])
def test_actual_legacy_saves_cross_load_and_continue_exact(stable_equivalence_evidence, direction):
    results = json.loads((stable_equivalence_evidence / direction / "evidence.json").read_text(encoding="utf-8"))
    assert set(results) == set(CASES)
    assert all(item["exact_continued_save_bytes"] for item in results.values())
