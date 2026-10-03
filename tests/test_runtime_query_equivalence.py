"""Exact 0779ceb oracle: seed streams, ledger, save bytes and continuation."""
from pathlib import Path

import pytest

from worldline import Engine, military_benchmark as military
from worldline.engine import encode
from worldline.runtime_query_benchmark import exact_reference


@pytest.fixture(scope='module')
def reference():
    return exact_reference()


@pytest.mark.parametrize('scenario', military.SCENARIOS)
@pytest.mark.parametrize('seed', [941, 1731])
def test_baseline_daily_world_rng_ledger_save_bytes_and_cross_version_continue(tmp_path, reference, scenario, seed):
    before = reference.military_benchmark.setup_engine(tmp_path / 'before.sqlite', scenario,
                                                       seed=seed, validation_mode='runtime')
    after = military.setup_engine(tmp_path / 'after.sqlite', scenario, seed=seed,
                                  validation_mode='runtime')
    resumed = None
    try:
        actions = [[], []]
        for module, game, output in ((reference.military_benchmark, before, actions[0]),
                                      (military, after, actions[1])):
            module.campaign_orders(game, scenario, 1, output)
        assert actions[0] == actions[1]
        for _ in range(4):
            before.advance(1)
            after.advance(1)
            assert encode(before._stored_payload()) == encode(after._stored_payload())
            assert encode(before.ledger()) == encode(after.ledger())
        old_save = before.save(tmp_path / 'before.json')
        new_save = after.save(tmp_path / 'after.json')
        assert Path(old_save).read_bytes() == Path(new_save).read_bytes()
        # Load the actual baseline-produced save with optimized production code.
        resumed = Engine.load(old_save, tmp_path / 'resumed.sqlite', validation_mode='runtime')
        for _ in range(4):
            for game in (before, after, resumed):
                game.advance(1)
            expected = encode(before._stored_payload())
            ledger = encode(before.ledger())
            assert encode(after._stored_payload()) == encode(resumed._stored_payload()) == expected
            assert encode(after.ledger()) == encode(resumed.ledger()) == ledger
        # Archive comparison uses each package's own storage implementation.
        from worldline import state_storage
        assert encode(reference.state_storage.export_archive(before.db)) == encode(
            state_storage.export_archive(after.db)) == encode(state_storage.export_archive(resumed.db))
    finally:
        for game in (before, after, resumed):
            if game is not None:
                game.close()
