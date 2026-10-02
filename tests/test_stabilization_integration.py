import copy
import json
from pathlib import Path
import pytest
from worldline import Engine, Order
from worldline import economy, guardrails
from worldline.audit import Auditor, AuditError
from worldline.benchmark import Replay, reference_world, policy_for


@pytest.fixture
def stabilized(tmp_path):
    engine = Engine.create(tmp_path / 'stable.sqlite', reference_world('early_industrial'), 73)
    engine.stabilize_economy()
    yield engine
    engine.close()


def test_activation_preserves_assets_rng_and_old_rule_version(stabilized):
    w = reference_world('early_industrial')
    enabled = stabilized.true_state
    assert enabled.countries == w.countries
    assert enabled.projects == w.projects
    assert enabled.economy['version'] == enabled.economy['model_revision'] == 3
    assert enabled.economy['stabilization_version'] == 1
    with pytest.raises(ValueError, match='already'): stabilized.stabilize_economy()
    assert stabilized.ledger()[-1]['kind'] == 'economic_stabilization_enabled'


def test_legacy_save_load_is_unchanged_and_explicitly_migratable(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'examples' / 'economy_v1_month_one.json'
    e = Engine.load(path, tmp_path / 'legacy.sqlite')
    before = e.true_state
    assert before.economy['version'] == 1
    assert 'stabilization_version' not in before.economy
    with pytest.raises(ValueError, match='revision'): e.stabilize_economy()
    e.upgrade_economy()
    e.stabilize_economy()
    assert e.true_state.countries == before.countries
    assert e.true_state.projects == before.projects
    e.advance(1, 'month')
    Auditor().check(e.true_state)
    e.close()


def test_future_stabilization_version_cannot_be_silently_read(stabilized):
    world = stabilized.true_state
    world.economy['stabilization_version'] = 2
    with pytest.raises(AuditError, match='stabilization version'): Auditor().check(world)


def test_policy_queries_are_read_only_and_actor_beliefs_separate(stabilized):
    before = copy.deepcopy(stabilized._payload())
    quote = stabilized.policy_constraints('a', 100, perceived={'cost': 1, 'risk': .01, 'source': 'optimistic briefing'})
    assert quote['true_state'] != quote['perceived_state']
    assert stabilized._payload() == before
    assert 'normal_financial_feasibility' in quote
    assert 'emergency_financial_feasibility' in quote
    assert 'expected_consequences' in quote


def test_physical_resource_shortage_rolls_back_financing(stabilized):
    stabilized.plan_project('impossible', 'a', 'ra', 'factory', 'machinery', 10**8)
    before = copy.deepcopy(stabilized._payload())
    with pytest.raises(ValueError, match='resource'): stabilized.fund_project('impossible', allow_emergency=True)
    assert stabilized._payload() == before
    quote = stabilized.policy_constraints('a', project_id='impossible')
    assert not quote['physical_feasibility']['feasible']


def test_project_order_and_direct_funding_use_same_rules_once(stabilized):
    stabilized.plan_project('one', 'a', 'ra', 'factory', 'machinery', 10)
    initial = stabilized.true_state.countries['a'].treasury
    result = stabilized.submit(Order('fund-one', 'a', 'fund_project', {'project_id': 'one'}))
    assert result['status'] == 'accepted'
    assert stabilized.true_state.countries['a'].treasury == initial - 10
    assert stabilized.true_state.projects['one']['status'] == 'funded'
    assert stabilized.submit(Order('fund-twice', 'a', 'fund_project', {'project_id': 'one'}))['status'] == 'rejected'


def test_rejected_huge_financing_never_changes_accounts(stabilized):
    before = stabilized.true_state
    result = stabilized.fiscal_action('a', 10**12, allow_emergency=True)
    assert result['status'] == 'rejected'
    assert stabilized.true_state == before


def test_high_risk_political_choice_is_not_cancelled(stabilized):
    # Above normal limits, but not above all finite emergency sources.
    normal = stabilized.policy_constraints('a', 10**7, allow_emergency=False)
    normal_total = normal['normal_financial_feasibility']['available']
    amount = int(normal_total) + 1
    quote = stabilized.policy_constraints('a', amount, allow_emergency=True)
    assert not quote['normal_financial_feasibility']['feasible']
    assert quote['emergency_financial_feasibility']['feasible']
    result = stabilized.fiscal_action('a', amount, purpose='prestige war', allow_emergency=True)
    assert result['status'] == 'accepted'
    assert any(row['kind'] == 'fiscal_action_executed' for row in stabilized.ledger())


def test_mobilization_and_demobilization_save_pipeline(stabilized, tmp_path):
    before = stabilized.true_state.economy['regions']['ra']['labour']['employed']
    assert stabilized.mobilize('a', 'ra', 1000)['status'] == 'accepted'
    assert stabilized.true_state.economy['regions']['ra']['labour']['employed'] < before
    assert stabilized.mobilize('a', 'ra', 0)['status'] == 'accepted'
    snapshot = stabilized.save(tmp_path / 'mobilized.json')
    loaded = Engine.load(snapshot, tmp_path / 'loaded.sqlite')
    assert loaded._payload() == stabilized._payload()
    assert loaded.ledger() == stabilized.ledger()
    for e in (stabilized, loaded): e.advance(3, 'month')
    assert loaded._payload() == stabilized._payload()
    assert loaded.ledger() == stabilized.ledger()
    loaded.close()


def test_stabilized_replay_matches_sqlite_order_system_and_rng(tmp_path):
    world = reference_world('preindustrial')
    economy.enable_stabilization(world)
    replay = Replay(world, 711)
    engine = Engine.create(tmp_path / 'parity.sqlite', world, 711)
    for month in range(6):
        changes = policy_for(replay.world, 'balanced', month)
        economy.change_policy(replay.world, 'a', changes)
        engine.set_policy('a', changes)
        replay.month()
        engine.advance(1, 'month')
        assert replay.world.to_dict() == engine.true_state.to_dict()
        assert replay.rng.getstate() == engine.rng.getstate()
    engine.close()


def test_save_resume_preserves_new_credit_and_jobs_exactly(stabilized, tmp_path):
    stabilized.set_policy('a', {'military': 150, 'subsidy': 100, 'education': 30, 'emergency_finance': True})
    stabilized.advance(3, 'month')
    stabilized.mobilize('a', 'ra', 300)
    loaded = Engine.load(stabilized.save(tmp_path / 'resume.json'), tmp_path / 'resume.sqlite')
    for month in range(12):
        for e in (stabilized, loaded): e.advance(1, 'month')
    assert loaded._payload() == stabilized._payload()
    assert loaded.ledger() == stabilized.ledger()
    loaded.close()


def test_migration_requires_opt_in_before_npc_constraints(tmp_path):
    world = reference_world('industrial')
    with pytest.raises(ValueError, match='stabilization'): guardrails.can_borrow(world, 'a', 1)


def test_regional_quotes_never_use_foreign_skilled_pool(stabilized):
    quote = guardrails.available_skilled_labour(stabilized.true_state, 'a', 'ra', 'machinery')
    assert set(quote['regions']) == {'ra'}
    with pytest.raises(ValueError, match='owned'): guardrails.available_labour(stabilized.true_state, 'a', 'rb')


def test_war_cost_is_separate_from_physical_manpower(stabilized):
    quote = guardrails.can_fund_war(stabilized.true_state, 'a', 10**10, region='ra', manpower=1)
    assert quote['physical_feasibility']['feasible']
    assert not quote['execution_feasibility']
    missing = guardrails.can_fund_war(stabilized.true_state, 'a', 1, region='ra', manpower=10**10)
    assert not missing['physical_feasibility']['feasible']


def test_project_effects_remain_delayed_after_stabilization(stabilized):
    stabilized.plan_project('rail', 'a', 'ra', 'infrastructure', 'railways', 1)
    before = stabilized.true_state.regions['ra'].infrastructure['railways']
    stabilized.fund_project('rail')
    assert stabilized.true_state.regions['ra'].infrastructure['railways'] == before
    stabilized.advance(1, 'month')
    assert stabilized.true_state.projects['rail']['status'] != 'completed'


def test_unknown_emergency_instrument_rejected_as_order(stabilized):
    old = stabilized.true_state
    result = stabilized.set_policy('a', {'emergency_instruments': ['imaginary money']})
    assert result['status'] == 'rejected'
    assert old == stabilized.true_state


def test_stabilized_save_uses_new_schema_and_legacy_saves_still_use_two(stabilized, tmp_path):
    saved = stabilized.save(tmp_path / 'schema3.json')
    raw = json.loads(saved.read_text(encoding='utf-8'))
    assert raw['payload']['schema_version'] == 3
    from worldline.persistence import migrate_snapshot
    with pytest.raises(ValueError, match='does not match'):
        migrate_snapshot({**raw['payload'], 'schema_version': 2})


def test_repayment_changes_principal_and_reserves_once(stabilized):
    # Existing cash spent, then borrowing paid as an actual action.
    first = stabilized.true_state.countries['a'].treasury
    assert stabilized.fiscal_action('a', first + 100)['status'] == 'accepted'
    stabilized.advance(12, 'month')
    before = stabilized.true_state.countries['a']
    amount = min(before.treasury, before.debt, 10)
    assert amount > 0
    assert stabilized.repay_debt('a', amount)['status'] == 'accepted'
    after = stabilized.true_state.countries['a']
    assert after.debt == before.debt - amount
    assert after.treasury == before.treasury - amount
    Auditor().check(stabilized.true_state)


def test_late_month_demobilization_is_not_accelerated(stabilized):
    stabilized.advance(58, 'day')  # 1897-02-28
    assert stabilized.mobilize('a', 'ra', 1000)['status'] == 'accepted'
    assert stabilized.mobilize('a', 'ra', 0)['status'] == 'accepted'
    stabilized.advance(1, 'day')
    pipelines = stabilized.true_state.economy['regions']['ra']['labour_state']['pipeline']
    returners = [p for p in pipelines if p['reason'] == 'demobilization']
    assert returners and all(p['elapsed_days'] == 1 for p in returners)


def test_war_resources_cannot_also_finance_the_same_action_by_requisition(stabilized):
    world = stabilized.true_state
    c = world.countries['a']
    c.treasury, c.debt = 0, 10**6
    state = world.economy['countries']['a']['credit']
    state['domestic_debt'], state['foreign_debt'] = c.debt, 0
    stocks = world.economy['regions']['ra']['stocks']
    assert stocks['energy'] > 0 and stocks['raw_materials'] > 0
    before = copy.deepcopy(world.to_dict())
    needed = {'energy': stocks['energy'], 'raw_materials': stocks['raw_materials']}
    quote = guardrails.can_fund_war(world, 'a', 20, region='ra', resources=needed, instruments=['requisition'])
    assert quote['physical_feasibility']['feasible']
    assert not quote['emergency_financial_feasibility']['feasible']
    assert not quote['execution_feasibility']
    assert world.to_dict() == before


def test_fully_prepaid_project_is_not_frozen_by_unrelated_budget_shortfall(stabilized):
    from worldline import projects
    stabilized.plan_project('paid', 'a', 'ra', 'factory', 'machinery', 10)
    stabilized.fund_project('paid')
    world = stabilized.true_state
    world.economy['countries']['a']['credit']['government_payment_ratio'] = 0
    world.economy['countries']['a']['credit']['hooks']['government_project_speed'] = 0
    world.economy['countries']['a']['credit']['arrears'] = 100
    projects.tick(world, {}, None)
    assert world.projects['paid']['elapsed_days'] == 1


def test_destroyed_assets_cannot_be_sold_for_new_funding(stabilized):
    from worldline import credit
    world = stabilized.true_state
    c = world.countries['a']
    c.treasury, c.debt = 0, 10**6
    state = world.economy['countries']['a']['credit']
    state['domestic_debt'], state['foreign_debt'] = c.debt, 0
    for sector in world.economy['regions']['ra']['sectors'].values():
        sector['capital_stock'] = 0
    before = copy.deepcopy(world.to_dict())
    quote = credit.assess(world, 'a', 20, instruments=['asset_sales'])
    assert quote['emergency_financial_feasibility']['instruments_available']['asset_sales'] == 0
    assert not quote['emergency_financial_feasibility']['feasible']
    with pytest.raises(ValueError, match='financing'): credit.finance_amount(world, 'a', 20, allow_emergency=True, instruments=['asset_sales'])
    assert world.to_dict() == before
