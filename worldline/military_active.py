"""Military scheduler selections using the shared private query indexes."""
from .runtime_queries import items, values, latest_by_group


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
