"""Military accounting is tested through real audited SQLite transactions."""
import copy
import json
import random

import pytest

from test_crisis_integration import make, rewrite
from worldline import Engine
from worldline.audit import AuditError
from worldline import military_bridge as bridge, military_state as ms, military_combat as combat


@pytest.fixture
def engine(tmp_path):
    e = make(tmp_path / 'military.sqlite')
    e.enable_military({'initial_equipment': {k: {'personal_weapons': {'quantity': 100, 'condition': 1.}} for k in ('army-a', 'army-b')},
        'initial_supply': {k: {'food': 100, 'ammunition': 500, 'medical': 100} for k in ('army-a', 'army-b')}})
    yield e
    e.close()


def loss(identifier='loss-one', **causes):
    return {'id': identifier, 'actor': 'a', 'region': 'ra', 'operation_id': None,
        'casualties': {'army-a': causes}, 'equipment_losses': {}, 'supply_consumption': {}, 'unit_changes': {}}


@pytest.mark.parametrize('stage', range(5))
def test_old_saves_remain_exactly_opt_in(tmp_path, stage):
    e = make(tmp_path / 'old.sqlite', stage)
    try:
        rng = e.rng.getstate()
        saved = e.save(tmp_path / 'old.json')
        assert 'military_state' not in e.true_state.to_dict()
        loaded = Engine.load(saved, tmp_path / 'loaded.sqlite')
        try:
            assert loaded.true_state.to_dict() == e.true_state.to_dict()
            assert loaded.rng.getstate() == rng
            for game in (e, loaded): game.advance(5)
            assert loaded.true_state.to_dict() == e.true_state.to_dict()
        finally: loaded.close()
    finally: e.close()


def test_activation_one_draw_and_ordered_manifest(tmp_path):
    e = make(tmp_path / 'game.sqlite')
    try:
        rng = random.Random(); rng.setstate(e.rng.getstate())
        seed = rng.getrandbits(128)
        e.enable_military()
        assert e.rng.getstate() == rng.getstate()
        assert e.true_state.military_state['root_seed'] == seed
        assert [x['name'] for x in e._system_specs] == ['worldline.projects', 'worldline.economy', 'worldline.npc', 'worldline.crisis', 'worldline.military']
    finally: e.close()


def test_fatal_loss_is_not_demobilized_and_is_applied_once(engine):
    before = engine.true_state.economy['regions']['ra']['labour_state']
    result = loss(battle_casualties=10)
    engine._military_call('military_bridge', 'apply_result', result)
    after = engine.true_state
    assert after.armies['army-a'].personnel == 90
    assert after.military_state['formations']['army-a']['manpower'] == 90
    assert after.economy['regions']['ra']['pending_losses']['war_losses'] == 10
    l = after.economy['regions']['ra']['labour_state']
    assert l['standing_army'] == before['standing_army'] - 10
    assert l['labour_force'] == before['labour_force'] - 10
    assert not l['pipeline']
    state = after.to_dict()
    engine._military_call('military_bridge', 'apply_result', result)
    assert engine.true_state.to_dict() == state
    assert len(engine.true_state.military_state['loss_receipts']) == 1
    engine.advance(31)
    assert engine.true_state.economy['regions']['ra']['demography']['last_account']['war_losses'] == 10


def test_capture_is_unavailable_and_deserters_return_through_pipeline(engine):
    engine._military_call('military_bridge', 'apply_result', loss(capture_missing=10, desertion=10))
    w = engine.true_state
    assert w.military_state['formations']['army-a']['manpower'] == 80
    assert w.economy['regions']['ra']['pending_losses']['war_losses'] == 0
    assert any(t['reason'] == 'demobilization' for t in w.economy['regions']['ra']['labour_state']['pipeline'])


def test_new_mobilization_reduces_civilian_jobs_and_no_free_equipment(engine):
    w = engine.true_state
    before = sum(w.economy['regions']['ra']['labour_state']['jobs'].values())
    engine.raise_formation('levy', 'a', 100, 'ra')
    after = engine.true_state
    assert sum(after.economy['regions']['ra']['labour_state']['jobs'].values()) < before
    assert after.military_state['formations']['levy']['equipment_state'] == {}
    state = after.to_dict()
    with pytest.raises(ValueError): engine.raise_formation('free', 'a', 20, 'ra', equipment_state={'personal_weapons': {'quantity': 20}})
    assert engine.true_state.to_dict() == state


def test_nonstate_actor_has_finite_men_without_fake_national_economy(engine):
    engine.register_military_actor('company', country=None, home_region='ra', kind='chartered_company', manpower_pool=120, trained_reserve=50, treasury=80, stocks={'food': 50})
    engine.raise_formation('company-force', 'company', 100, 'ra')
    assert 'company' not in engine.true_state.countries
    assert engine.true_state.military_state['actors']['company']['manpower_pool'] == 20
    with pytest.raises(ValueError): engine.raise_formation('impossible', 'company', 21, 'ra')
    engine.add_supply_depot('company-depot', 'company', 'ra', stocks={'food': 10})
    engine.load_formation_supplies('company-depot', 'company-force', {'food': 5}, receipt_id='finite')
    assert engine.true_state.military_state['actors']['company']['stocks']['food'] == 40


def test_foreign_factory_cannot_be_raided_by_procurement_api(engine):
    state = engine.true_state.to_dict()
    with pytest.raises(ValueError): engine._military_call('military_bridge', 'procure', 'a', 'rb', 'food', 1)
    assert engine.true_state.to_dict() == state


def test_food_counted_once_across_daily_and_monthly_accounting(engine):
    w = engine.true_state
    bridge.record_food_consumption(w, 'army-a', .01)
    bridge.record_food_consumption(w, 'army-a', .01)
    credit = min(.02, 100 * .08 / 365)
    assert bridge.civilian_food_need(w, 'ra', 100.) == pytest.approx(100. - credit)
    assert bridge.civilian_food_need(w, 'ra', 100.) == 100.


def test_save_load_military_streams_reports_memories_and_next_tick(engine, tmp_path):
    engine._military_call('military_bridge', 'apply_result', loss(disease=1))
    engine.advance(8)
    snapshot = engine.save(tmp_path / 'military.json')
    assert json.loads(snapshot.read_text(encoding='utf-8'))['payload']['schema_version'] == 6
    loaded = Engine.load(snapshot, tmp_path / 'loaded.sqlite')
    try:
        assert loaded.true_state.to_dict() == engine.true_state.to_dict()
        assert loaded.rng.getstate() == engine.rng.getstate()
        for game in (engine, loaded): game.advance(40)
        assert loaded.true_state.to_dict() == engine.true_state.to_dict()
        assert loaded.rng.getstate() == engine.rng.getstate()
        assert loaded.ledger() == engine.ledger()
    finally: loaded.close()


@pytest.mark.parametrize('mutation', ['missing', 'order', 'version', 'loss'])
def test_inconsistent_military_save_is_rejected(engine, tmp_path, mutation):
    engine._military_call('military_bridge', 'apply_result', loss(disease=1))
    path = engine.save(tmp_path / 'valid.json')
    def change(p):
        if mutation == 'missing': p['engine']['world'].pop('military_state')
        elif mutation == 'order': p['engine']['systems'][3:5] = reversed(p['engine']['systems'][3:5])
        elif mutation == 'version': p['engine']['world']['military_state']['version'] = 999
        else: p['engine']['world']['military_state']['loss_receipts']['loss-one']['deaths']['ra'] += 1
    rewrite(path, tmp_path / 'bad.json', change)
    with pytest.raises((ValueError, AuditError)): Engine.load(tmp_path / 'bad.json', tmp_path / 'bad.sqlite')


def test_crisis_war_links_without_automatic_offensive(engine):
    event = engine.inject_event('state_of_war', 'ra', ['a', 'b'])
    engine.advance(1)
    s = engine.true_state.military_state
    assert event['event_id'] in s['war_links']
    assert not s['operations']
    assert not engine.true_state.crisis_state['events'][event['event_id']]['responses']  # No military political decision.


def test_tactical_success_does_not_guarantee_objective(engine):
    w = engine.true_state
    op = {'id': 'test', 'actor': 'a', 'kind': 'attack', 'formations': ['army-a'], 'target': 'ra', 'results': [], 'params': {'defenders': [], 'objective_reach': .1}}
    result = combat.resolve(w, op)
    assert result['tactical_result'] == 'success'
    assert not result['strategic_objective_achieved']
    assert result['control_change'] is None


def test_equipment_production_consumes_real_output_and_is_delayed(engine):
    w = engine.true_state
    goods = w.economy['regions']['ra']['stocks']['goods']
    # Explicit synthetic initial warehouse assets; no simulated production is invented.
    with engine._transaction():
        for name in ('machinery', 'heavy_industry'):
            engine._world.economy['regions']['ra']['stocks']['goods'][name] = 10.
    goods = engine.true_state.economy['regions']['ra']['stocks']['goods']
    n = min(5, int(min(goods['machinery'], goods['heavy_industry']) * 2))
    assert n > 0
    engine.produce_military_equipment('batch', 'a', 'ra', 'personal_weapons', n, duration_days=30)
    assert not engine.true_state.military_state['actors']['a']['equipment_reserve']
    assert engine.true_state.economy['regions']['ra']['stocks']['goods']['machinery'] == pytest.approx(goods['machinery'] - n * .5)
    engine.advance(30)
    assert engine.true_state.military_state['actors']['a']['equipment_reserve']['personal_weapons'] == n
    engine.issue_military_equipment('a', 'army-a', 'personal_weapons', n)
    assert engine.true_state.military_state['formations']['army-a']['equipment_state']['personal_weapons']['quantity'] == n + 100


def test_same_seed_same_initial_conditions_and_orders(tmp_path):
    games = [make(tmp_path / (name + '.sqlite')) for name in ('one', 'two')]
    try:
        for e in games:
            e.enable_military()
            e.raise_formation('levy', 'a', 100, 'ra')
            e.advance(33)
        assert games[0].true_state.to_dict() == games[1].true_state.to_dict()
        assert games[0].rng.getstate() == games[1].rng.getstate()
        assert games[0].ledger() == games[1].ledger()
    finally:
        for e in games: e.close()


def test_replacements_need_training_real_equipment_and_actual_arrival(engine):
    with engine._transaction():
        for name in ('machinery', 'heavy_industry'):
            engine._world.economy['regions']['ra']['stocks']['goods'][name] = 50.
        engine._world.economy['regions']['ra']['stocks']['goods']['agriculture'] = 10.
    engine.produce_military_equipment('weapons', 'a', 'ra', 'personal_weapons', 20, duration_days=30)
    item = engine.train_replacements('cohort-one', 'army-a', 20)
    cohort = item['cohort']
    assert engine.true_state.military_state['formations']['army-a']['manpower'] == 100
    with pytest.raises(ValueError): engine.incorporate_replacements('cohort-one')
    engine.add_supply_depot('training-depot', 'a', 'ra')
    engine.procure_supply('training-depot', 'food', 5, receipt_id='training-food')
    engine.load_formation_supplies('training-depot', cohort, {'food': 5}, receipt_id='cohort-food')
    engine.advance(120)
    assert engine.true_state.military_state['replacements']['cohort-one']['status'] == 'ready'
    with pytest.raises(ValueError): engine.incorporate_replacements('cohort-one')
    engine.issue_military_equipment('a', cohort, 'personal_weapons', 20)
    charged = engine.true_state.economy['regions']['ra']['labour_state']['standing_army'] + engine.true_state.economy['regions']['ra']['labour_state']['mobilized']
    engine.incorporate_replacements('cohort-one')
    w = engine.true_state
    assert w.military_state['formations'][cohort]['manpower'] == 0
    assert w.armies['army-a'].personnel > 100
    assert w.economy['regions']['ra']['labour_state']['standing_army'] + w.economy['regions']['ra']['labour_state']['mobilized'] == pytest.approx(charged)


def test_armored_production_needs_real_capabilities_not_calendar(engine):
    state = engine.true_state.to_dict()
    with pytest.raises(ValueError, match='physical capabilities'):
        engine.produce_military_equipment('impossible-armor', 'a', 'ra', 'armored_vehicles', 1)
    assert engine.true_state.to_dict() == state


def test_small_combat_losses_do_not_round_every_encounter_to_zero(engine):
    w = engine.true_state
    w.military_state['formations']['army-b']['location'] = 'ra'
    op = {'id': 'small-contact', 'actor': 'a', 'kind': 'raid', 'formations': ['army-a'], 'target': 'ra', 'results': [], 'params': {'defenders': ['army-b']}}
    # Repeated observations use the stored stream, not a new random generator.
    losses = [combat.resolve(w, op)['casualties']['army-a']['battle_casualties'] for _ in range(50)]
    assert sum(losses) > 0


def test_firearms_and_artillery_cannot_fire_without_ammunition(engine):
    w = engine.true_state
    f = w.military_state['formations']['army-a']
    f['equipment_state']['personal_weapons']['uses_ammunition'] = True
    armed = combat.capability(w, 'army-a')
    f['supply_state']['stocks']['ammunition'] = 0
    unfed = combat.capability(w, 'army-a')
    assert unfed['ammunition_ratio'] == 0
    assert unfed['effective_capacity'] < armed['effective_capacity']


def test_legacy_army_and_actual_labour_cannot_diverge(engine):
    before = engine.true_state.to_dict()
    with pytest.raises(AuditError):
        with engine._transaction():
            engine._world.military_state['formations']['army-a']['manpower'] += 1
            engine._world.military_state['formations']['army-a']['combat_manpower'] += 1
    assert engine.true_state.to_dict() == before


def test_no_free_runtime_fortress_or_remote_combat(engine):
    before = engine.true_state.to_dict()
    with pytest.raises(ValueError): engine.register_fortress('ra', 'a', strength=100, food=500)
    assert engine.true_state.to_dict() == before
    w = engine.true_state
    op = {'id': 'remote', 'actor': 'a', 'kind': 'attack', 'formations': ['army-a'], 'target': 'ra', 'results': [], 'params': {'defenders': ['army-b', 'army-a']}}
    result = combat.resolve(w, op)
    assert 'army-b' not in result['casualties']
    assert result['casualties']['army-a']['battle_casualties'] == 0


def test_constraint_api_cannot_read_private_enemy_strength(engine):
    with pytest.raises(ValueError): engine.military_constraints('a', ['army-b'])
    assert engine.military_constraints('a', ['army-a'])['actual']['manpower'] == 100


def test_naval_contact_does_not_kill_unrelated_port_infantry(engine):
    w = engine.true_state
    s = w.military_state
    s['formations']['army-a']['equipment_state']['ships'] = {'quantity': 5, 'condition': 1}
    s['formations']['army-b']['location'] = 'ra'
    op = {'id': 'naval', 'actor': 'a', 'kind': 'naval_attack', 'formations': ['army-a'], 'target': 'ra', 'results': [], 'params': {}}
    result = combat.resolve(w, op)
    assert 'army-b' not in result['casualties']


def test_blades_cannot_be_issued_as_guns(engine):
    with engine._transaction():
        engine._world.military_state['actors']['a']['equipment_reserve']['personal_weapons'] = 20
        engine._world.military_state['formations']['army-a']['equipment_state']['personal_weapons']['uses_ammunition'] = True
    with pytest.raises(ValueError, match='incompatible'):
        engine.issue_military_equipment('a', 'army-a', 'personal_weapons', 20)
