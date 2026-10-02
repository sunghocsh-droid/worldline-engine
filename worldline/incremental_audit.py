"""Incremental JSON validity with exhaustive existing semantic invariants.

Every original reference, receipt and resource check still examines its full
semantic scope each day. Only already validated, unchanged JSON subtrees are
reused; inserted/replaced/dirty subtrees retain strict JSON checks.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar

_selection = ContextVar('worldline_audit_selection', default=None)
_json_context = ContextVar('worldline_json_audit_context', default=None)
_missing = object()


def items(mapping):
    # Reverse references may live in unchanged receipts. Keep their checks whole.
    return mapping.items()


def values(mapping):
    return mapping.values()


def json_check(value):
    selected = _selection.get()
    context = _json_context.get()
    if selected is None or context is None:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    frame, roots, tracker = context
    prior = roots.get(id(value), _missing)
    if prior is _missing or tracker is None:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    fragments = []
    def inspect(obj, old):
        # An inserted/replaced subtree has not passed the previous audit. This
        # includes plain values that the barrier has just wrapped. Borrow it
        # whole so JSON still checks arbitrary shapes, cycles and numeric keys.
        if obj is not old or getattr(obj, '_tracker', None) is not tracker:
            fragments.append(obj)
            return
        entry = frame.states.get(id(obj))
        if entry is None:
            # The same existing tracked subtree is untouched in this frame;
            # mutation propagation guarantees nested changes also touch it.
            return
        if isinstance(obj, dict) and isinstance(entry[1], dict):
            before, keys = entry[1], frame.keys.get(id(obj))
            if keys is None:
                fragments.append(obj)
                return
            if len(obj) != len(before) or any(key not in before for key in keys):
                # Full key shape keeps sort_keys mixed-key errors observable
                # even when the unchanged values need no further validation.
                fragments.append(dict.fromkeys(obj))
            for key in keys:
                if key in obj:
                    inspect(obj[key], before.get(key, _missing))
        else:
            # List insertion/deletion/reordering and unknown shapes retain the
            # exhaustive JSON check. Cold untouched lists take the fast branch.
            fragments.append(obj)
    inspect(value, prior)
    # Callers consume validity only, never the returned serialization. One dump
    # checks all dirty fragments without materializing unchanged historical data.
    return json.dumps(fragments, sort_keys=True, ensure_ascii=False, allow_nan=False)


@contextmanager
def changed(world, previous, frame, enabled):
    roots = {}
    if enabled:
        root_entry = frame.states.get(id(world))
        for name in world.__dataclass_fields__:
            current = getattr(world, name)
            if isinstance(current, (dict, list, tuple)):
                old = root_entry[1][name] if root_entry else current
                identity = id(current)
                # Aliased roots with conflicting prior identities use full JSON.
                if identity in roots and roots[identity] is not old:
                    roots[identity] = _missing
                else:
                    roots[identity] = old
    token = _selection.set({} if enabled else None)
    json_token = _json_context.set((frame, roots, getattr(world, '_tracker', None)) if enabled else None)
    try:
        yield
    finally:
        _json_context.reset(json_token)
        _selection.reset(token)
