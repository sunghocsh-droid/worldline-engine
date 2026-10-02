"""The discarded-copy optimization keeps complete validation and detachment."""
import copy
import pytest
from test_crisis_integration import make
from worldline import persistence
from worldline.engine import encode


@pytest.mark.parametrize('military_enabled', [False, True])
def test_normalization_matches_preoptimization_algorithm(tmp_path, military_enabled):
    engine = make(tmp_path / 'game.sqlite')
    try:
        if military_enabled:
            engine.enable_military()
        engine.advance(2)
        payload = engine._stored_payload()
        before = copy.deepcopy(payload)
        reference = copy.deepcopy(payload)
        reference['world'] = persistence.validate_engine(payload).to_dict()
        if not reference.get('systems'):
            reference.pop('systems', None)
        actual = persistence.normalize_engine(payload)
        assert encode(actual) == encode(reference)
        assert payload == before
        actual['world']['countries']['a']['treasury'] = 0
        actual['systems'][0]['version'] = 'tampered'
        actual['observed']['tampered'] = {}
        assert payload == before
    finally:
        engine.close()


def test_normalization_retains_the_full_auditor(tmp_path, monkeypatch):
    engine = make(tmp_path / 'game.sqlite')
    try:
        engine.enable_military()
        payload = engine._stored_payload()
        calls = []
        original = persistence.Auditor.check
        def checked(self, world, *args, **kwargs):
            calls.append(world.military_state is not None)
            return original(self, world, *args, **kwargs)
        monkeypatch.setattr(persistence.Auditor, 'check', checked)
        persistence.normalize_engine(payload)
        assert calls == [True]
        malformed = copy.deepcopy(payload)
        malformed['world']['military_state']['formations']['army-a']['manpower'] += 10
        with pytest.raises(ValueError):
            persistence.normalize_engine(malformed)
        assert calls == [True, True]
    finally:
        engine.close()
