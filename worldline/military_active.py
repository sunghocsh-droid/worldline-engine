"""Private incremental indexes over military records; history remains intact.

Indexes live on tracked dictionaries, outside logical World/save data. A plain
World uses the same predicates with direct scans. Rollback and bounded-log gaps
rebuild indexes, while newly inserted plain records are checked on every query
until the mutation journal's next instrumentation barrier.
"""
from .state_runtime import changes_since


class _Index:
    def __init__(self):
        self.token = None
        self.untracked = set()

    def refresh(self, collection):
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
            if key in collection and getattr(value, '_tracker', None) is not tracker:
                self.untracked.add(key)
            else:
                self.untracked.discard(key)
            self.update(key, value)


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


def active_operations(collection):
    from .military_operations import ACTIVE
    return sorted(values(collection, 'active-operations', lambda operation: operation['status'] in ACTIVE),
                  key=lambda operation: operation['id'])


def active_shipments(collection):
    return items(collection, 'active-shipments', lambda shipment: shipment['status'] not in ('delivered', 'cancelled', 'lost'))


def executing_operations(collection):
    return values(collection, 'executing-operations', lambda operation: operation['status'] == 'in_progress')


def command_group(message):
    return message['operation_id'], message['recipient'], message['message_kind']


def queued_commands(collection):
    queued = values(collection, 'queued-commands', lambda message: message['status'] == 'queued')
    seen = set()
    for message in queued:
        order = (message['expected_received_at'], (message['as_of'], message['issued_at'],
                 message['transmitted_at'], message['sequence']))
        if order in seen:
            # deliver_due uses a stable sort over this exact key. Preserve its
            # original mapping insertion order when equal imported orders exist.
            return [value for value in collection.values() if value['status'] == 'queued']
        seen.add(order)
    return queued
