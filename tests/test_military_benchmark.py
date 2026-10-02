"""SQLite campaign evidence, deterministic replay and finite military accounts."""
import gzip
import json
from pathlib import Path

import pytest

from worldline import military_benchmark as bench
from worldline.audit import Auditor


@pytest.mark.parametrize('scenario', bench.SCENARIOS)
def test_fictional_scenarios_use_existing_profiles_and_twenty_distinct_people(scenario):
    world = bench.scenario_world(scenario)
    assert len(world.countries) == 4
    assert len(world.npc_state['persons']) == 20
    assert all(e['profile'] == bench.SCENARIOS[scenario] for e in world.economy['countries'].values())
    assert world.military_state is None
    assert world.crisis_state is None
    Auditor().check(world)


def test_military_scenario_initial_supplies_are_explicit_finite_inputs():
    world = bench.scenario_world('expeditionary_sea')
    config = bench.military_config(world, 'expeditionary_sea')
    assert set(config['initial_supply']) == set(world.armies)
    assert all(v['food'] > 0 for v in config['initial_supply'].values())
    assert config['initial_equipment']['b-transport']['ships']['quantity'] == 4
    assert config['initial_equipment']['army-b']['personal_weapons']['uses_ammunition']
    assert not config['initial_equipment']['army-d']['personal_weapons']['uses_ammunition']


@pytest.mark.parametrize('scenario', bench.SCENARIOS)
def test_required_campaign_conditions_are_actual_finite_state_not_labels(tmp_path, scenario):
    engine = bench.setup_engine(tmp_path / (scenario + '.sqlite'), scenario)
    try:
        world, s = engine.true_state, engine.true_state.military_state
        assert s['formations']['army-c']['manpower'] == bench.INITIAL_MANPOWER[scenario]
        for country in world.countries:
            assert world.economy['countries'][country]['labour']['military'] == sum(
                f['manpower'] for f in s['formations'].values() if f['actor'] == country)
            assert all(value == 0 for value in s['actors'][country]['stocks'].values())
        parameters = next(item['payload'] for item in engine.ledger() if item['kind'] == 'military_scenario_parameters')
        assert parameters['order_delay'] == bench.ORDER_DELAY[scenario]
        if scenario == 'preindustrial_border':
            assert s['formations']['army-b']['equipment_state']['personal_weapons']['uses_ammunition']
            assert not s['formations']['b-cavalry']['equipment_state']['personal_weapons']['uses_ammunition']
            assert s['formations']['b-cavalry']['equipment_state']['horses']['quantity'] == 200
            assert s['formations']['b-cavalry']['supply_state']['stocks']['fodder'] > 0
            assert s['links']['sea:rb-rd']['mode'] == 'sea'
        elif scenario == 'industrial_front':
            assert s['links']['rb-rc']['mode'] == 'rail'
            assert s['formations']['army-b']['equipment_state']['artillery']['quantity'] == 10
            assert s['formations']['army-b']['supply_state']['requirements']['ammunition'] == .5
        else:
            assert len(s['convoys']) == 2
            assert s['formations']['b-invasion-fleet']['equipment_state']['ships']['quantity'] == 8
            assert s['links']['rc-rd']['throughput'] == 20
        if scenario != 'expeditionary_sea':
            fort = s['fortresses']['rc']
            assert fort['garrison'] == ['army-c']
            assert fort['food'] > 0
            assert fort['food'] == parameters['fortress_reserves']['rc']['food']
            assert s['formations']['army-c']['supply_state']['stocks']['food'] == parameters['formation_stocks']['army-c']['food']
        Auditor().check(world)
    finally:
        engine.close()


def test_sqlite_harness_disabled_mode_keeps_existing_system_order(tmp_path):
    engine = bench.setup_engine(tmp_path / 'disabled.sqlite', 'preindustrial_border', military_enabled=False)
    try:
        assert engine.true_state.military_state is None
        engine.advance(2)
        row = bench.measure(engine, 0)
        assert row['military'] is None
        assert row['ledger_entries'] > 0
    finally:
        engine.close()


def test_sqlite_setup_carries_real_crews_ports_links_and_delayed_orders(tmp_path):
    engine = bench.setup_engine(tmp_path / 'enabled.sqlite', 'expeditionary_sea')
    try:
        actions = []
        bench.campaign_orders(engine, 'expeditionary_sea', 1, actions)
        s = engine.true_state.military_state
        assert s['formations']['b-transport']['manpower'] == 30
        assert s['formations']['b-transport']['location'] == 'rb'
        assert s['operations']['campaign-operation:1']['execution_started_at'] is None
        assert s['loss_receipts'] == {}
        assert all(v['mode'] == 'sea' for v in s['links'].values())
        assert sum(v['capacity'] for v in s['convoys'].values()) > 0
        assert s['operations']['blockade:1']['target'] == 'rc'
        assert s['operations']['blockade:1']['formations'] == ['c-fleet']
        assert len({f for v in s['convoys'].values() for f in v['formations']}) == 2
    finally:
        engine.close()


@pytest.mark.parametrize('scenario', ('preindustrial_border', 'industrial_front'))
def test_fortress_campaigns_issue_real_siege_orders_with_scenario_communication_delay(tmp_path, scenario):
    engine = bench.setup_engine(tmp_path / 'siege.sqlite', scenario)
    try:
        actions = []
        bench.campaign_orders(engine, scenario, 1, actions)
        s = engine.true_state.military_state
        operation = s['operations']['campaign-operation:1']
        assert operation['kind'] == 'siege'
        assert operation['params']['investment_days'] == 14
        assert operation['target'] == 'rc'
        assert s['fortresses']['rc']['strength'] > 0
        message = next(v for v in s['commands'].values() if v['operation_id'] == operation['id'])
        assert message['communication_delay'] == bench.ORDER_DELAY[scenario]
        assert message['received_at'] is None
        assert operation['execution_started_at'] is None
    finally:
        engine.close()


def test_world_rng_and_ledger_equality_are_all_required():
    reference = {'final_logical': {'world': {'value': 1}, 'rng': [1], 'ledger': [{'value': 1}]},
                 'final_logical_sha256': 'reference'}
    replay = {'final_logical': {'world': {'value': 1}, 'rng': [1], 'ledger': [{'value': 2}]},
              'final_logical_sha256': 'replay'}
    result = bench.compare_campaigns(reference, replay)
    assert not result['equal']
    assert result['differences'] == ['ledger']


def test_invalid_campaign_duration_is_rejected_before_database_creation(tmp_path):
    with pytest.raises(ValueError):
        bench.run_campaign(tmp_path, 'industrial_front', 0)
    assert not (tmp_path / 'game.sqlite').exists()


def test_benchmark_does_not_disguise_hard_audit_failure_as_policy_constraint():
    from worldline.audit import AuditError
    actions = []
    def hard_failure():
        raise AuditError(['military: actual conservation failure'])
    with pytest.raises(AuditError, match='actual conservation failure'):
        bench._try(actions, None, 'procurement', hard_failure)
    assert actions == []


def test_two_convoys_quote_current_shared_depot_inventory_after_first_withdrawal(tmp_path):
    """Focused controller checkpoint; actual voyage integration has its own tests."""
    import copy
    from worldline import military_logistics as logistics
    engine = bench.setup_engine(tmp_path / 'convoys.sqlite', 'expeditionary_sea')
    try:
        checkpoint = engine.true_state
    finally:
        engine.close()
    s = checkpoint.military_state
    # Configure a detached initial checkpoint using two units of existing stores.
    s['depots']['home:b']['stocks']['food'] -= 2.
    s['depots']['forward:b']['stocks']['food'] = 2.
    for key in ('army-b', 'b-invasion-fleet'):
        s['formations'][key]['location'] = 'rc'
        s['formations'][key]['supply_state']['stocks']['food'] = 0.
    for key in ('b-transport', 'b-transport-navy'):
        s['formations'][key]['location'] = 'rd'

    class PublicAdapter:
        @property
        def true_state(self):
            return copy.deepcopy(checkpoint)

        def dispatch_supply(self, *args, **kwargs):
            return logistics.dispatch(checkpoint, *args, **kwargs)

        def plan_operation(self, *args, **kwargs):
            return {'id': args[0]}

        def command_operation(self, *args, **kwargs):
            return None

    actions = []
    bench._convoy_orders(PublicAdapter(), 'expeditionary_sea', 3, actions)
    assert len(s['shipments']) == 1
    assert sum(v['cargo'].get('food', 0) for v in s['shipments'].values()) == 2.
    assert s['depots']['forward:b']['stocks']['food'] == 0.
    assert not any(a['status'] == 'constrained' and 'actual depot supplies' in a['reason'] for a in actions)


def test_actual_sqlite_save_load_reproduces_world_rng_ledger_and_pending_cohorts(tmp_path):
    reference = bench.run_campaign(tmp_path / 'reference', 'preindustrial_border', 2)
    restored = bench.run_campaign(tmp_path / 'restored', 'preindustrial_border', 2,
                                  checkpoint_month=1, restore=True)
    assert bench.compare_campaigns(reference, restored)['equal']
    assert restored['database_bytes'] > 0
    assert (tmp_path / 'restored' / 'midpoint.json').exists()


@pytest.fixture(scope='module')
def genuine_campaigns():
    """Reuse only genuinely current-source campaigns; fresh checkout reruns them."""
    directory = Path(__file__).resolve().parents[1] / 'benchmarks' / 'military'
    results = {}
    for scenario in bench.SCENARIOS:
        path = directory / scenario / 'result.json.gz'
        if path.exists():
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                result = json.load(stream)
            if result.get('source_sha256') == bench.source_hashes() and result.get('months') == 36:
                results[scenario] = result
                continue
        # Existing stale artifacts stay intact. A genuine fresh run gets a new path.
        path = directory / (scenario + '-pytest-' + bench.digest(bench.source_hashes())[:12])
        cached = path / 'result.json.gz'
        if cached.exists():
            with gzip.open(cached, 'rt', encoding='utf-8') as stream:
                result = json.load(stream)
            if result.get('source_sha256') == bench.source_hashes() and result.get('months') == 36:
                results[scenario] = result
                continue
        if path.exists():
            raise AssertionError('Incomplete current-source campaign remains; preserve it before a fresh rerun')
        results[scenario] = bench.run_campaign(path, scenario, 36)
    return results


@pytest.mark.parametrize('scenario', bench.SCENARIOS)
def test_actual_three_year_campaigns_keep_consistent_cohorts_finance_and_history(genuine_campaigns, scenario):
    result = genuine_campaigns[scenario]
    assert result['months'] == 36
    assert result['rows'][-1]['date'] == '1900-01-01'
    assert result['source_sha256'] == bench.source_hashes()
    assert result['rows'][-1]['military']['operations'] > 1
    assert result['rows'][-1]['military']['procurement_cost'] > 0
    assert result['rows'][-1]['military']['loss_receipts'] > 0
    assert result['rows'][-1]['ledger_entries'] > result['rows'][0]['ledger_entries']
    assert bench.digest(result['final_logical']) == result['final_logical_sha256']
    from worldline.models import World
    Auditor().check(World.from_dict(result['final_logical']['world']))
