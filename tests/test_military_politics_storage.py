"""Real transaction, archive indexes and compatibility for political approvals."""
import json

import pytest

from worldline import Engine, state_storage
from test_military_politics import world


def test_explicit_approval_binds_real_paid_people_and_indexed_immutable_history(tmp_path, world):
    e = Engine.create(tmp_path / 'political-training.sqlite', world, seed=197)
    try:
        before = e.true_state
        people = before.military_state['formations']['a']['manpower']
        cash = before.countries['a'].treasury
        e.train_replacements('paid-cohort', 'a', 20)
        current = e.true_state
        item = current.military_state['replacements']['paid-cohort']
        receipt_id = item['political_decision_id']
        receipt = current.military_state['political_decisions'][receipt_id]
        assert receipt['body']['mode'] == 'explicit_player_approval'
        assert receipt['body']['selected'] == 'replenish'
        assert receipt['consumed_by'] == 'paid-cohort'
        assert current.military_state['formations']['a']['manpower'] == people
        assert current.military_state['formations'][item['cohort']]['manpower'] == 20
        assert current.countries['a'].treasury == cash - 20
        assert current.projects[item['project']]['spent'] == 20
        row = e.db.execute('SELECT owner,country,region,day FROM state_heads WHERE kind=? AND item=?',
            ('military_political_decisions', receipt_id)).fetchone()
        assert row == ('a', 'a', 'ra', current.date)
        versions = state_storage.history(e.db, kind='military_political_decisions', owner='a', item=receipt_id)
        assert any(v['value'] == receipt for v in versions)
        versions[-1]['value']['body']['selected'] = 'defer'
        assert e.true_state.military_state['political_decisions'][receipt_id] == receipt
        with pytest.raises(ValueError, match='already consumed'):
            e.train_replacements('another-cohort', 'a', 20, approval_receipt=receipt_id)
        assert e.true_state.to_dict() == current.to_dict()
    finally:
        e.close()


def test_failed_physical_request_rolls_back_new_approval_money_people_ledger_and_rng(tmp_path, world):
    e = Engine.create(tmp_path / 'rollback.sqlite', world, seed=198)
    try:
        before, rng, ledger = e.true_state.to_dict(), e.rng.getstate(), e.ledger()
        with pytest.raises(ValueError):
            e.train_replacements('no-existing-people', 'a', 10**9, allow_emergency=True)
        assert e.true_state.to_dict() == before
        assert e.rng.getstate() == rng
        assert e.ledger() == ledger
        assert e.db.execute('SELECT COUNT(*) FROM state_heads WHERE kind=?',
                            ('military_political_decisions',)).fetchone()[0] == 0
    finally:
        e.close()


def test_actual_previous_military_schema_six_loads_without_invented_will_or_rng(tmp_path, world):
    world.military_state.pop('politics_version')
    world.military_state.pop('political_decisions')
    for values in world.military_state['politics'].values():
        values['will_to_continue'] = .5
    e = Engine.create(tmp_path / 'previous.sqlite', world, seed=199)
    try:
        save = e.save(tmp_path / 'previous-military.json')
        assert json.loads(save.read_text(encoding='utf-8'))['payload']['schema_version'] == 6
        loaded = Engine.load(save, tmp_path / 'restored.sqlite')
        try:
            before, rng = loaded.true_state.to_dict(), loaded.rng.getstate()
            quote = loaded.military_constraints('a')
            assert quote['political_will']['status'] == 'not_assessed'
            assert loaded.true_state.to_dict() == before
            assert loaded.rng.getstate() == rng
            loaded.train_replacements('real-new-choice', 'a', 20)
            current = loaded.true_state
            assert current.military_state['politics_version'] == 1
            assert current.military_state['political_decisions']['replacement-approval:real-new-choice']['body']['approved']
            assert loaded.rng.getstate() == rng
        finally:
            loaded.close()
    finally:
        e.close()
