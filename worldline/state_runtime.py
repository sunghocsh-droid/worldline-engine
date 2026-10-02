"""Private mutation journal. Logical data and public detached copies are unchanged.

Only Engine-owned graphs are instrumented. Newly inserted plain containers keep
their identity for the rest of the synchronous callback; a barrier instruments
them before the next callback/tick. Undo stores shallow container contents, so
unchanged historical bodies are never deep-copied for a transaction.
"""
import copy
import weakref
from collections import deque
from dataclasses import fields, is_dataclass


class Record:
    def __copy__(self):
        result = type(self).__new__(type(self))
        private = {'_tracker', '_parents', '_version', '_changes', '_active_indexes'}
        for field in fields(self):
            if field.name not in private:
                object.__setattr__(result, field.name, getattr(self, field.name))
        for key, value in self.__dict__.items():
            if key not in private:
                object.__setattr__(result, key, value)
        return result

    def __setattr__(self, key, value):
        tracker = self.__dict__.get('_tracker')
        if tracker is not None and not key.startswith('_'):
            tracker.touch(self, key)
            tracker.pending[id(self)] = self
        object.__setattr__(self, key, value)

    def __delattr__(self, key):
        tracker = self.__dict__.get('_tracker')
        if tracker is not None and not key.startswith('_'):
            tracker.touch(self, key)
            tracker.pending[id(self)] = self
        object.__delattr__(self, key)

    def __deepcopy__(self, memo):
        result = type(self).__new__(type(self))
        memo[id(self)] = result
        for field in fields(self):
            object.__setattr__(result, field.name, copy.deepcopy(getattr(self, field.name), memo))
        return result


class TrackedDict(dict):
    def __init__(self, *a, **kw):
        dict.__init__(self, *a, **kw)
        self._tracker = None
        self._parents = []
        self._version = 0

    def _change(self, key):
        if self._tracker is not None:
            self._tracker.touch(self, key)
            self._tracker.pending[id(self)] = self

    def __setitem__(self, key, value):
        self._change(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        self._change(key)
        dict.__delitem__(self, key)

    def update(self, *a, **kw):
        for key, value in dict(*a, **kw).items():
            self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key, *default):
        if key in self:
            self._change(key)
        return dict.pop(self, key, *default)

    def popitem(self):
        if self:
            self._change(next(reversed(self)))
        return dict.popitem(self)

    def clear(self):
        for key in tuple(self):
            self._change(key)
        dict.clear(self)

    def __ior__(self, other):
        self.update(other)
        return self

    def __copy__(self):
        # Temporary shallow projections must not inherit live journal ancestry.
        # Borrowed nested values retain their original live mutation tracking.
        return dict(self)

    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        result.update((copy.deepcopy(k, memo), copy.deepcopy(v, memo)) for k, v in self.items())
        return result


class TrackedList(list):
    def __init__(self, values=()):
        list.__init__(self, values)
        self._tracker = None
        self._parents = []
        self._version = 0

    def _change(self):
        if self._tracker is not None:
            self._tracker.touch(self, None)
            self._tracker.pending[id(self)] = self

    def __setitem__(self, key, value):
        self._change()
        list.__setitem__(self, key, value)

    def __delitem__(self, key):
        self._change()
        list.__delitem__(self, key)

    def append(self, value):
        self._change()
        list.append(self, value)

    def extend(self, values):
        self._change()
        list.extend(self, values)

    def insert(self, key, value):
        self._change()
        list.insert(self, key, value)

    def pop(self, key=-1):
        self._change()
        return list.pop(self, key)

    def remove(self, value):
        self._change()
        list.remove(self, value)

    def clear(self):
        self._change()
        list.clear(self)

    def reverse(self):
        self._change()
        list.reverse(self)

    def sort(self, *a, **kw):
        self._change()
        list.sort(self, *a, **kw)

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __imul__(self, count):
        self._change()
        list.__imul__(self, count)
        return self

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(v, memo) for v in self)
        return result


def detached(value, *, canonical=False):
    """Plain independent data; canonical=True matches a sorted JSON DB read."""
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if is_dataclass(value):
        return {f.name: detached(getattr(value, f.name), canonical=canonical) for f in fields(value)}
    if isinstance(value, dict):
        keys = sorted(value) if canonical else value
        return {k: detached(value[k], canonical=canonical) for k in keys}
    if isinstance(value, list):
        return [detached(v, canonical=canonical) for v in value]
    if isinstance(value, tuple):
        values = [detached(v, canonical=canonical) for v in value]
        return values if canonical else tuple(values)
    return copy.deepcopy(value)


def projection(world):
    """Borrow JSON dictionaries and convert only the small dataclass skeleton."""
    def record(value):
        return {f.name: getattr(value, f.name) for f in fields(value)}
    raw = record(world)
    for field in ('countries', 'regions', 'armies', 'factions'):
        raw[field] = {k: record(v) for k, v in raw[field].items()}
    for c in raw['countries'].values():
        c['npc'] = record(c['npc'])
    for f in raw['factions'].values():
        f['npc'] = record(f['npc'])
    for field in ('npc_state', 'crisis_state', 'military_state'):
        if raw[field] is None:
            raw.pop(field)
    return raw


class Frame:
    def __init__(self):
        self.states = {}
        self.keys = {}


class Journal:
    def __init__(self, root):
        self.frames = []
        self.pending = {}
        self.external_dirty = False
        self.root = self.wrap(root)

    def wrap(self, value, parent=None, key=None, memo=None):
        memo = {} if memo is None else memo
        if id(value) in memo:
            result = memo[id(value)]
        elif getattr(value, '_tracker', None) is self:
            result = value
        elif isinstance(value, dict):
            result = TrackedDict()
            memo[id(value)] = result
            result._tracker = self
            for k, item in value.items():
                dict.__setitem__(result, k, self.wrap(item, result, k, memo))
        elif isinstance(value, list):
            result = TrackedList()
            memo[id(value)] = result
            result._tracker = self
            for i, item in enumerate(value):
                list.append(result, self.wrap(item, result, i, memo))
        elif is_dataclass(value):
            result = value
            memo[id(value)] = result
            object.__setattr__(result, '_tracker', self)
            object.__setattr__(result, '_parents', [])
            object.__setattr__(result, '_version', 0)
            for f in fields(value):
                object.__setattr__(result, f.name, self.wrap(getattr(result, f.name), result, f.name, memo))
        elif isinstance(value, tuple):
            return tuple(self.wrap(item, parent, key, memo) for item in value)
        else:
            return value
        if parent is not None and not any(p() is parent and k == key for p, k in result._parents):
            result._parents.append((weakref.ref(parent), key))
        return result

    def barrier(self):
        pending, self.pending = self.pending, {}
        memo = {}
        for obj in pending.values():
            if isinstance(obj, dict):
                for k, item in tuple(obj.items()):
                    dict.__setitem__(obj, k, self.wrap(item, obj, k, memo))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    list.__setitem__(obj, i, self.wrap(item, obj, i, memo))
            else:
                for f in fields(obj):
                    object.__setattr__(obj, f.name, self.wrap(getattr(obj, f.name), obj, f.name, memo))

    def begin(self):
        self.barrier()
        frame = Frame()
        self.frames.append(frame)
        return frame

    def touch(self, obj, key, visited=None):
        visited = set() if visited is None else visited
        identity = id(obj)
        if identity in visited:
            return
        visited.add(identity)
        if not self.frames:
            self.external_dirty = True
        for frame in self.frames:
            if identity not in frame.states:
                before = dict(obj) if isinstance(obj, dict) else list(obj) if isinstance(obj, list) else {
                    f.name: getattr(obj, f.name) for f in fields(obj)}
                frame.states[identity] = (obj, before)
            frame.keys.setdefault(identity, set()).add(key)
        object.__setattr__(obj, '_version', obj._version + 1)
        changes = getattr(obj, '_changes', None)
        if changes is not None:
            changes.append((obj._version, key))
        for ref, child_key in obj._parents:
            parent = ref()
            if parent is not None:
                self.touch(parent, child_key, visited)

    def view(self, obj, frame, memo=None):
        memo = {} if memo is None else memo
        if id(obj) in memo:
            return memo[id(obj)]
        entry = frame.states.get(id(obj))
        if entry is None:
            return obj
        old = entry[1]
        if isinstance(obj, dict):
            result = {}
            memo[id(obj)] = result
            result.update((k, self.view(v, frame, memo)) for k, v in old.items())
        elif isinstance(obj, list):
            result = []
            memo[id(obj)] = result
            result.extend(self.view(v, frame, memo) for v in old)
        else:
            result = type(obj).__new__(type(obj))
            memo[id(obj)] = result
            for k, v in old.items():
                object.__setattr__(result, k, self.view(v, frame, memo))
        return result

    def end(self, frame, *, rollback=False):
        assert self.frames[-1] is frame
        self.frames.pop()
        if rollback:
            for obj, old in reversed(list(frame.states.values())):
                object.__setattr__(obj, '_version', obj._version + 1)
                changes = getattr(obj, '_changes', None)
                if changes is not None:
                    # A rollback changes membership without calling mutators.
                    changes.clear()
                    changes.append((obj._version, None))
                if isinstance(obj, dict):
                    dict.clear(obj)
                    dict.update(obj, old)
                elif isinstance(obj, list):
                    list.__setitem__(obj, slice(None), old)
                else:
                    for k, v in old.items():
                        object.__setattr__(obj, k, v)
            self.pending.clear()


def version(value):
    tracker = getattr(value, '_tracker', None)
    return (id(value), value._version) if tracker is not None else None


def changes_since(value, previous):
    """Watched dictionary keys since a token, or None when a rebuild is needed.

    Logging is allocated only for explicitly watched collections, stays bounded,
    and is private derived state. Parent propagation includes nested mutations.
    Plain containers always require a scan because their mutations are invisible.
    """
    current = version(value)
    if current is None or not isinstance(value, TrackedDict):
        return current, None
    changes = getattr(value, '_changes', None)
    if changes is None:
        value._changes = deque(maxlen=4096)
        return current, None
    if previous == current:
        return current, set()
    if previous is None or previous[0] != current[0] or previous[1] > current[1]:
        return current, None
    if not changes or previous[1] < changes[0][0] - 1:
        return current, None
    keys = set()
    for serial, key in reversed(changes):
        if serial <= previous[1]:
            break
        if key is None:
            return current, None
        keys.add(key)
    return current, keys
