"""Training and institutional culture determine command's practical effect."""
import copy
import pytest
from test_military_operations import world, planned, advance
from worldline import military_combat as combat, military_operations as ops, military


def test_trained_mission_command_mitigates_delayed_coordination(world):
    f = world.military_state['formations']['a-force']
    f['doctrine'].update(mission_oriented_command=.8, adoption=.8, subordinate_initiative=.8,
        staff_capability=.8, centralized_control=.2, punishment_risk_culture=.1)
    slow = combat.command_doctrine(f, 14)
    centralized = copy.deepcopy(f)
    centralized['doctrine'].update(centralized_control=.95, subordinate_initiative=.05, punishment_risk_culture=.9)
    control = combat.command_doctrine(centralized, 14)
    assert control['factor'] < slow['factor'] <= 1
    assert slow['effective_initiative'] > control['effective_initiative']
    assert combat.command_doctrine(f, 0)['factor'] == 1


def test_declaring_a_doctrine_without_training_cannot_instantly_use_it(world):
    f = world.military_state['formations']['a-force']
    f['training'] = 0
    f['doctrine'].update(mission_oriented_command=1, adoption=1, subordinate_initiative=1,
        staff_capability=1, centralized_control=0, punishment_risk_culture=0)
    result = combat.command_doctrine(f, 30)
    assert result['effective_initiative'] == 0
    assert result['factor'] < 1
    assert result['doctrine_axes']['mission_oriented_command'] == 1


def test_mission_command_does_not_teleport_unreceived_orders(world):
    f = world.military_state['formations']['a-force']
    f['doctrine'].update(mission_oriented_command=1, adoption=1, subordinate_initiative=1,
        staff_capability=1, centralized_control=0, punishment_risk_culture=0)
    planned(world)
    ops.send_command(world, 'march', 'a', delay=14)
    advance(world, 13)
    assert f['location'] == 'ra'
    assert world.military_state['operations']['march']['execution_started_at'] is None


def test_military_quote_cannot_count_a_unit_twice(world):
    with pytest.raises(ValueError, match='unique'):
        military.constraints(world, 'a', ['a-force', 'a-force'])


@pytest.mark.parametrize('amount', [-1, float('nan'), float('inf'), True])
def test_military_quote_rejects_money_that_does_not_exist(world, amount):
    with pytest.raises(ValueError, match='finite and nonnegative'):
        military.constraints(world, 'a', ['a-force'], amount)
