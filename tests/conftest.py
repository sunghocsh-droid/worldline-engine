import pytest
from worldline import World, Country, Region, Army, Faction, NPC, Engine


@pytest.fixture
def world():
    # Synthetic test data, deliberately not Korean historical estimates.
    return World(date="1897-01-31", countries={"a": Country("Test A", population=10000,
                 treasury=1000, industries={"grain": 10}), "b": Country("Test B", population=5000)},
                 regions={"r1": Region("One", "a", 5000), "r2": Region("Two", "a", 5000, 60, 0)},
                 armies={"army": Army("a", "r1", 100, 20)},
                 factions={"f": Faction("a", "Test faction", NPC(goals=["power"], faction_power=30))},
                 tech_requirements={"basic": [], "advanced": ["basic"]}, canon={"founding": "synthetic"})


@pytest.fixture
def engine(tmp_path, world):
    e = Engine.create(tmp_path / "world.sqlite", world, 123)
    yield e
    e.close()
