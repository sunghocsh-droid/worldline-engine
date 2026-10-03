"""Auditor-accepted imported history keeps exact legacy failures and rollback."""
import pytest

from worldline import Engine
from worldline.runtime_query_benchmark import current_modules, exact_reference


@pytest.fixture(scope='module')
def baseline():
    return exact_reference()


def imported_terminal_effect_world(modules):
    """Import a custom identity while retaining every permanent causal fact.

    The original full Auditor and snapshot validators permit this identity,
    although the original scheduler rejects its nonnumeric suffix on advance.
    Keep that observable contract rather than silently repairing the import.
    """
    world = modules.crisis_benchmark.CrisisReplay().world
    event = modules.crisis.create_event(world, 'civil_unrest', 'ra', ['a'], severity=.1)
    effect = modules.crisis_effects.enqueue(world, event['event_id'], 'pressure_delta',
        {'category': 'civil_unrest', 'delta': 0, 'country': 'a'})
    world.crisis_state['outbox'].extend(modules.crisis_effects.apply_due(world))
    original, imported = effect['effect_id'], 'imported-effect'
    del world.crisis_state['effects'][original]
    effect['effect_id'] = imported
    world.crisis_state['effects'][imported] = effect
    event['immediate_consequences'] = [imported if key == original else key
                                       for key in event['immediate_consequences']]
    for record in world.crisis_state['outbox']:
        if record.get('effect_id') == original:
            record['effect_id'] = imported
            if 'effect' in record:
                record['effect']['effect_id'] = imported
    assert effect['applied'] is True
    assert modules.engine.Auditor().check(world) == []
    return world


def committed(game, encoder):
    return encoder({'world': game.true_state.to_dict(), 'rng': game.rng.getstate(),
                    'ledger': game.ledger()})


def rejected_advance_preserves_every_committed_result(game, encoder):
    before = committed(game, encoder)
    actual_rng_before = game._rng.getstate()
    with pytest.raises(ValueError) as failure:
        game.advance(1)
    assert type(failure.value) is ValueError
    assert committed(game, encoder) == before
    assert game._rng.getstate() == actual_rng_before
    assert game._journal.frames == []
    assert not game.db.in_transaction
    return str(failure.value)


@pytest.mark.parametrize('mode', ['runtime', 'verification'])
def test_imported_applied_effect_keeps_baseline_engine_error_and_full_rollback(tmp_path, baseline, mode):
    modules = current_modules()
    games = []
    try:
        for name, package in [('baseline', baseline), ('optimized', modules)]:
            world = imported_terminal_effect_world(package)
            game = package.engine.Engine.create(tmp_path / (name + '.sqlite'), world,
                                                seed=1731, validation_mode=mode)
            assert package.engine.Auditor().check(game.true_state) == []
            games.append(game)
        encoder = modules.engine.encode
        assert committed(games[0], encoder) == committed(games[1], encoder)
        messages = [rejected_advance_preserves_every_committed_result(game, encoder) for game in games]
        assert messages == ["invalid literal for int() with base 10: 'effect'"] * 2
    finally:
        for game in games:
            game.close()


@pytest.mark.parametrize('mode', ['runtime', 'verification'])
def test_baseline_save_current_load_keeps_import_error_and_world_rng_ledger_isolation(tmp_path, baseline, mode):
    original = baseline.engine.Engine.create(tmp_path / 'original.sqlite',
        imported_terminal_effect_world(baseline), seed=1731, validation_mode=mode)
    restored = None
    try:
        snapshot = original.save(tmp_path / 'baseline.json')
        restored = Engine.load(snapshot, tmp_path / 'restored.sqlite', validation_mode=mode)
        assert baseline.engine.Auditor().check(original.true_state) == []
        from worldline.audit import Auditor
        assert Auditor().check(restored.true_state) == []
        encoder = baseline.engine.encode
        assert committed(restored, encoder) == committed(original, encoder)
        old_error = rejected_advance_preserves_every_committed_result(original, encoder)
        new_error = rejected_advance_preserves_every_committed_result(restored, encoder)
        assert old_error == new_error == "invalid literal for int() with base 10: 'effect'"
        assert committed(restored, encoder) == committed(original, encoder)
        # Saving again after the failed load/continue branch proves no candidate
        # state, RNG draws, ledger rows or archive bodies leaked through rollback.
        saved_again = restored.save(tmp_path / 'restored_after_failure.json')
        assert saved_again.read_bytes() == snapshot.read_bytes()
    finally:
        for game in (original, restored):
            if game is not None:
                game.close()
