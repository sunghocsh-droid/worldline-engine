"""Private incremental query indexes; logical data and saved records stay intact.

Tracked dictionaries cache derived indexes outside World/save data. Plain
containers use equivalent direct scans. Nested mutation, deletion, replacement,
rollback and bounded journal gaps refresh or rebuild candidates. New plain
records are rechecked until the next instrumentation barrier.
"""
from dataclasses import fields, is_dataclass

from .state_runtime import changes_since


def _plain_descendant(value, tracker, seen=None):
    """Only changed records pay for nested pre-barrier container inspection."""
    if not isinstance(value, (dict, list, tuple)) and not is_dataclass(value):
        return False
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    if not isinstance(value, tuple) and getattr(value, '_tracker', None) is not tracker:
        return True
    children = value.values() if isinstance(value, dict) else (
        (getattr(value, f.name) for f in fields(value)) if is_dataclass(value) else value)
    return any(_plain_descendant(child, tracker, seen) for child in children)


class _Index:
    def __init__(self):
        self.token = None
        self.untracked = set()

    def refresh(self, collection):
        try:
            self.token, changed = changes_since(collection, self.token)
            if changed is None:
                self.clear()
                self.untracked.clear()
                changed = collection.keys()
            else:
                changed |= self.untracked
            tracker = getattr(collection, '_tracker', None)
            for key in changed:
                value = collection.get(key)
                if key in collection and (getattr(value, '_tracker', None) is not tracker or
                                           tracker.pending and _plain_descendant(value, tracker)):
                    # A tracked record can contain newly assigned plain nested
                    # containers. Raw edits of those children are invisible
                    # until barrier(), so keep touched records under review.
                    self.untracked.add(key)
                else:
                    self.untracked.discard(key)
                self.update(key, value)
        except BaseException:
            # A failed descriptor/predicate must not leave a partial update
            # marked as current, even if the caller repairs plain nested data.
            self.token = None
            raise


class _Selection(_Index):
    def __init__(self, predicate):
        super().__init__()
        self.predicate = predicate
        self.keys = set()

    def clear(self):
        self.keys.clear()

    def update(self, key, value):
        if value is not None and self.predicate(value):
            self.keys.add(key)
        else:
            self.keys.discard(key)


class _OrderedSelection(_Selection):
    """Validate inactive order keys without sorting cold history every query.

    Internal order functions are pure and produce comparable canonical keys.
    Remember only invalid identities, never exception objects or record bodies.
    Invalid history takes the original full-sort path to preserve its first
    exception in mapping insertion order, before any record is applied.
    """
    def __init__(self, predicate, order):
        super().__init__(predicate)
        self.order = order
        self.invalid = set()

    def clear(self):
        super().clear()
        self.invalid.clear()

    def refresh(self, collection):
        # Presence distinguishes a stored None from a deleted key. Keep this
        # reference only during refresh so indexes never retain payload graphs.
        self._collection = collection
        try:
            super().refresh(collection)
        finally:
            del self._collection

    def update(self, key, value):
        self.keys.discard(key)
        self.invalid.discard(key)
        if key not in self._collection:
            return
        try:
            self.order(value)
        except Exception:
            self.invalid.add(key)
            return
        if self.predicate(value):
            self.keys.add(key)


class _Latest(_Index):
    def __init__(self, group, order, predicate):
        super().__init__()
        self.group, self.order, self.predicate = group, order, predicate
        self.entries, self.groups, self.winners, self.top_counts = {}, {}, {}, {}

    def clear(self):
        self.entries.clear()
        self.groups.clear()
        self.winners.clear()
        self.top_counts.clear()

    def update(self, key, value):
        descriptor = (self.group(value), self.order(value)) if value is not None and self.predicate(value) else None
        previous = self.entries.get(key)
        if previous == descriptor:
            return
        if previous is not None:
            group, previous_order = self.entries.pop(key)
            candidates = self.groups[group]
            del candidates[key]
            if not candidates:
                del self.groups[group]
                del self.winners[group]
                del self.top_counts[group]
            elif self.winners[group] == key:
                self.winners[group] = max(candidates, key=candidates.get)
                maximum = candidates[self.winners[group]]
                self.top_counts[group] = sum(order == maximum for order in candidates.values())
            elif previous_order == candidates[self.winners[group]]:
                self.top_counts[group] -= 1
        if descriptor is not None:
            group, order = descriptor
            self.entries[key] = descriptor
            candidates = self.groups.setdefault(group, {})
            candidates[key] = order
            winner = self.winners.get(group)
            if winner is None or order > candidates[winner]:
                self.winners[group] = key
                self.top_counts[group] = 1
            elif order == candidates[winner]:
                self.top_counts[group] += 1


class _Grouped(_Index):
    def __init__(self, group):
        super().__init__()
        self.group = group
        self.entries, self.groups = {}, {}

    def clear(self):
        self.entries.clear()
        self.groups.clear()

    def update(self, key, value):
        present = value is not None
        descriptor = self.group(value) if present else None
        if key in self.entries:
            previous = self.entries[key]
            if present and previous == descriptor:
                return
            keys = self.groups[previous]
            keys.remove(key)
            if not keys:
                del self.groups[previous]
            del self.entries[key]
        if present:
            self.entries[key] = descriptor
            self.groups.setdefault(descriptor, set()).add(key)


def _cache(collection, name, factory):
    if getattr(collection, '_tracker', None) is None:
        return None
    caches = getattr(collection, '_active_indexes', None)
    if caches is None:
        caches = collection._active_indexes = {}
    if name not in caches:
        caches[name] = factory()
    result = caches[name]
    result.refresh(collection)
    return result


def items(collection, name, predicate):
    """Selected records in the scheduler's original sorted dictionary order."""
    index = _cache(collection, name, lambda: _Selection(predicate))
    keys = index.keys if index is not None else {key for key, value in collection.items() if predicate(value)}
    return [(key, collection[key]) for key in sorted(keys)]


def values(collection, name, predicate):
    return [value for _, value in items(collection, name, predicate)]


def ordered_values(collection, name, predicate, order):
    """Preserve full-sort validation and original insertion ties.

    Valid tracked history validates order keys once, then only changed records.
    Plain mappings and invalid keys take the original full-sort-before-filter
    path, including errors from records that the predicate would exclude.
    """
    index = _cache(collection, name, lambda: _OrderedSelection(predicate, order))
    if index is None or index.invalid:
        return [value for value in sorted(collection.values(), key=order) if predicate(value)]
    selected = [collection[key] for key in sorted(index.keys)]
    seen = set()
    for value in selected:
        descriptor = order(value)
        if descriptor in seen:
            # A stable original sort resolves equal imported orders by mapping
            # insertion. Ordinary distinct orders never scan cold history.
            selected = [value for value in collection.values() if predicate(value)]
            break
        seen.add(descriptor)
    return sorted(selected, key=order)


def grouped_items(collection, name, group, requested):
    """One hashable group's records in the original sorted-key query order.

    A name identifies one fixed grouping rule on this collection. Returned
    values are borrowed live records, for internal use like items().
    """
    try:
        index = _cache(collection, name, lambda: _Grouped(group))
        keys = index.groups.get(requested, ()) if index is not None else None
    except TypeError:
        # Raw malformed history may contain an unhashable grouping field that
        # the original equality scan simply skipped. Auditors still reject it.
        keys = None
    if keys is None:
        keys = {key for key, value in collection.items() if group(value) == requested}
    return [(key, collection[key]) for key in sorted(keys)]


def grouped_count(collection, name, group, requested):
    """Count a complete historical group without allocating its records."""
    try:
        index = _cache(collection, name, lambda: _Grouped(group))
        if index is not None:
            return len(index.groups.get(requested, ()))
    except TypeError:
        pass
    return sum(group(value) == requested for value in collection.values())


def first_grouped(collection, name, group, requested):
    """First inserted matching record, retaining imported duplicate semantics."""
    matches = grouped_items(collection, name, group, requested)
    if len(matches) < 2:
        return matches[0][1] if matches else None
    # Imported duplicate groups use the original mapping insertion order.
    return next(value for value in collection.values() if group(value) == requested)


def latest_by_group(collection, name, group, order, predicate, requested):
    """Latest matching records for requested groups without scanning old records."""
    requested = set(requested)
    if not requested:
        return {}
    index = _cache(collection, name, lambda: _Latest(group, order, predicate))
    if index is not None:
        result = {key: collection[index.winners[key]] for key in requested if key in index.winners}
        tied = {key for key in result if index.top_counts[key] > 1}
        if tied:
            # max() in the original scheduler picks the first inserted record
            # on equal order. Imported records may have equal command sequence
            # values; only that uncommon case pays for the original traversal.
            for value in collection.values():
                key = group(value)
                if key in tied and predicate(value) and order(value) == order(result[key]):
                    result[key] = value
                    tied.remove(key)
                    if not tied:
                        break
        return result
    latest = {}
    for value in collection.values():
        key = group(value)
        if key in requested and predicate(value) and (key not in latest or order(value) > order(latest[key])):
            latest[key] = value
    return latest


