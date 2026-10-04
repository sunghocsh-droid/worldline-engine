"""Narrow contact record contracts, including frozen historical authority proof."""
import copy
import json
from datetime import timedelta

from .models import canonical_date
from .intelligence import _id, freeze, same_json, source_reference

PROPOSAL_KEYS = {'id', 'body', 'status', 'submission_receipt_id', 'response_receipt_id'}
BODY_KEYS = {'request_id', 'kind', 'principal_ref', 'proposer_country_id', 'recipient_country_id',
             'sender_person_id', 'recipient_person_id', 'reply_recipients', 'content', 'created_at', 'source_ref'}
PROPOSAL_PAYLOAD_KEYS = (BODY_KEYS - {'request_id', 'principal_ref', 'reply_recipients'}) | {'proposal_id'}
RESPONSE_PAYLOAD_KEYS = {'proposal_id', 'kind', 'proposer_country_id', 'recipient_country_id',
                         'sender_person_id', 'recipient_person_id', 'result', 'responded_at',
                         'executing_person_id', 'source_ref'}
DISPATCH_KEYS = {'id', 'proposal_id', 'message_kind', 'recipient', 'payload', 'sequence', 'sent_at',
                 'delivery_date', 'status', 'received_at', 'delivery_receipt_id',
                 'application_receipt_id', 'belief_accepted'}
SUBMISSION_KEYS = {'id', 'kind', 'date', 'request_id', 'proposal_id', 'principal_ref', 'intent',
                   'authorization', 'dispatch_ids'}
RESPONSE_KEYS = SUBMISSION_KEYS | {'proposal_delivery_receipt_id', 'result', 'executing_person_id',
                                   'decision_ids', 'action_id'}
DELIVERY_KEYS = {'id', 'kind', 'date', 'dispatch_id', 'proposal_id', 'message_kind', 'recipient',
                 'application_receipt_id', 'belief_accepted'}
APPLICATION_KEYS = {'id', 'kind', 'date', 'dispatch_id', 'proposal_id', 'message_kind', 'recipient', 'applied'}
AUTHORIZATION_KEYS = {'country_id', 'person_id', 'active', 'authority', 'ruler', 'cabinet',
                      'decision_rule', 'player_binding'}
PROPOSE_INTENT_KEYS = {'operation', 'proposal_id', 'proposer_country_id', 'recipient_country_id',
                       'sender_person_id', 'recipient_person_id', 'content', 'principal_ref'}
RESPOND_INTENT_KEYS = {'operation', 'proposal_id', 'executing_person_id', 'accept_or_reject', 'principal_ref'}
DECISION_KEYS = {'id', 'date', 'country', 'actor', 'context', 'selected', 'evaluations',
                 'reason_trace', 'perceived', 'basis'}
ACTION_KEYS = {'id', 'date', 'country', 'actor', 'kind', 'proposal_id', 'response_receipt_id',
               'decision_ids', 'proposer', 'executor', 'option', 'coalition', 'result'}


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Invalid diplomacy ' + label + ' keys')


def typed_reference(value):
    _object(value, {'kind', 'id'}, 'typed reference')
    if value['kind'] not in ('person', 'player'):
        raise ValueError('Diplomacy requires explicit person/player references')
    _id(value['id'])


def _ids(values, *, nonempty=False):
    if not isinstance(values, list) or nonempty and not values:
        raise ValueError('Malformed diplomacy identity list')
    for value in values:
        _id(value)
    if len(set(values)) != len(values):
        raise ValueError('Repeated diplomacy identity')


def _content(value):
    _object(value, {'message', 'topic'}, 'content')
    for key, limit in (('message', 4096), ('topic', 128)):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > limit:
            raise ValueError('Invalid bounded diplomacy content')
    freeze(value)


def validate_authorization(value, principal):
    """Validate the historical certificate; never consult current roles."""
    _object(value, AUTHORIZATION_KEYS, 'authorization')
    for key in ('country_id', 'person_id'):
        _id(value[key])
    _ids(value['authority'])
    _ids(value['cabinet'])
    if value['ruler'] is not None:
        _id(value['ruler'])
    if (value['active'] is not True or value['decision_rule'] not in ('executive', 'cabinet')
            or not ({'all', 'diplomatic_engagement'} & set(value['authority']))
            or value['person_id'] not in [value['ruler'], *value['cabinet']]):
        raise ValueError('Invalid historical diplomacy execution authority')
    typed_reference(principal)
    binding = value['player_binding']
    if principal['kind'] == 'person':
        if binding is not None or principal['id'] != value['person_id']:
            raise ValueError('Diplomacy authority differs from its person principal')
    else:
        _object(binding, {'country_id', 'representative_person_id'}, 'historical player binding')
        if binding['country_id'] != value['country_id'] or binding['representative_person_id'] != value['person_id']:
            raise ValueError('Diplomacy authority differs from its historical player binding')


def validate_proposal_record(record):
    _object(record, PROPOSAL_KEYS, 'proposal')
    _id(record['id'])
    _id(record['submission_receipt_id'])
    body = record['body']
    _object(body, BODY_KEYS, 'proposal body')
    for key in ('request_id', 'proposer_country_id', 'recipient_country_id', 'sender_person_id', 'recipient_person_id'):
        _id(body[key])
    if body['kind'] != 'contact' or body['proposer_country_id'] == body['recipient_country_id']:
        raise ValueError('Diplomacy supports contact between distinct countries only')
    typed_reference(body['principal_ref'])
    replies = body['reply_recipients']
    if not isinstance(replies, list) or not replies:
        raise ValueError('Diplomacy requires explicit reply recipients')
    for reference in replies:
        typed_reference(reference)
    if len({(ref['kind'], ref['id']) for ref in replies}) != len(replies):
        raise ValueError('Duplicate diplomacy reply recipients')
    _content(body['content'])
    canonical_date(body['created_at'])
    source_reference(body['source_ref'])
    if body['source_ref'] != {'domain': 'person', 'id': body['sender_person_id']}:
        raise ValueError('Diplomacy proposal source differs from its sender')
    if record['status'] == 'pending':
        if record['response_receipt_id'] is not None:
            raise ValueError('Pending diplomacy proposal has a response')
    elif record['status'] in ('accepted', 'rejected'):
        _id(record['response_receipt_id'])
    else:
        raise ValueError('Invalid diplomacy proposal status')


def proposal_payload(proposal):
    return {'proposal_id': proposal['id'], **{key: copy.deepcopy(value)
            for key, value in proposal['body'].items() if key not in ('request_id', 'principal_ref', 'reply_recipients')}}


def response_payload(proposal, receipt):
    body = proposal['body']
    return {'proposal_id': proposal['id'], 'kind': 'contact',
            **{key: body[key] for key in ('proposer_country_id', 'recipient_country_id', 'sender_person_id', 'recipient_person_id')},
            'result': receipt['result'], 'responded_at': receipt['date'],
            'executing_person_id': receipt['executing_person_id'],
            'source_ref': {'domain': 'person', 'id': receipt['executing_person_id']}}


def validate_dispatch_record(record):
    _object(record, DISPATCH_KEYS, 'dispatch')
    for key in ('id', 'proposal_id'):
        _id(record[key])
    typed_reference(record['recipient'])
    payload = record['payload']
    kind = record['message_kind']
    if kind == 'proposal':
        _object(payload, PROPOSAL_PAYLOAD_KEYS, 'proposal payload')
        _content(payload['content'])
        origin = canonical_date(payload['created_at'])
        source_person = payload['sender_person_id']
    elif kind == 'response':
        _object(payload, RESPONSE_PAYLOAD_KEYS, 'response payload')
        if payload['result'] not in ('accepted', 'rejected'):
            raise ValueError('Invalid diplomacy response payload')
        _id(payload['executing_person_id'])
        origin = canonical_date(payload['responded_at'])
        source_person = payload['executing_person_id']
    else:
        raise ValueError('Invalid diplomacy message kind')
    for key in ('proposal_id', 'proposer_country_id', 'recipient_country_id', 'sender_person_id', 'recipient_person_id'):
        _id(payload[key])
    source_reference(payload['source_ref'])
    if (payload['kind'] != 'contact' or payload['proposal_id'] != record['proposal_id']
            or payload['proposer_country_id'] == payload['recipient_country_id']
            or payload['source_ref'] != {'domain': 'person', 'id': source_person}):
        raise ValueError('Invalid frozen diplomacy message identity')
    if type(record['sequence']) is not int or record['sequence'] < 1:
        raise ValueError('Invalid diplomacy dispatch sequence')
    sent, due = canonical_date(record['sent_at']), canonical_date(record['delivery_date'])
    if origin > sent or sent > due or type(record['belief_accepted']) is not bool:
        raise ValueError('Invalid diplomacy dispatch date/application flag')
    if record['status'] == 'queued':
        if record['belief_accepted'] or any(record[key] is not None for key in
                ('received_at', 'delivery_receipt_id', 'application_receipt_id')):
            raise ValueError('Queued diplomacy dispatch has receipt/perception effects')
    elif record['status'] == 'delivered':
        if canonical_date(record['received_at']) < due:
            raise ValueError('Diplomacy received before delivery date')
        _id(record['delivery_receipt_id'])
        _id(record['application_receipt_id'])
    else:
        raise ValueError('Invalid diplomacy dispatch status')


def validate_receipt_record(record):
    if not isinstance(record, dict):
        raise ValueError('Malformed diplomacy receipt')
    kind = record.get('kind')
    if kind == 'activation':
        from .diplomacy_intelligence import validate_activation_receipt
        validate_activation_receipt(record)
        return
    keys = {'submission': SUBMISSION_KEYS, 'response': RESPONSE_KEYS,
            'delivery': DELIVERY_KEYS, 'application': APPLICATION_KEYS}.get(kind)
    if keys is None:
        raise ValueError('Invalid diplomacy receipt kind')
    _object(record, keys, 'receipt')
    for key in ('id', 'proposal_id'):
        _id(record[key])
    canonical_date(record['date'])
    if kind in ('submission', 'response'):
        _id(record['request_id'])
        typed_reference(record['principal_ref'])
        validate_authorization(record['authorization'], record['principal_ref'])
        _ids(record['dispatch_ids'], nonempty=True)
        intent = record['intent']
        _object(intent, PROPOSE_INTENT_KEYS if kind == 'submission' else RESPOND_INTENT_KEYS, 'request intent')
        if not same_json(intent['principal_ref'], record['principal_ref']):
            raise ValueError('Diplomacy request principal mismatch')
        if kind == 'submission':
            if intent['operation'] != 'propose_contact':
                raise ValueError('Invalid diplomacy submission operation')
            if intent['proposal_id'] is not None and intent['proposal_id'] != record['proposal_id']:
                raise ValueError('Diplomacy explicit proposal identity mismatch')
            for key in ('proposer_country_id', 'recipient_country_id', 'sender_person_id', 'recipient_person_id'):
                _id(intent[key])
            _content(intent['content'])
        else:
            if (intent['operation'] != 'respond_contact' or intent['proposal_id'] != record['proposal_id']
                    or intent['executing_person_id'] != record['executing_person_id']
                    or intent['accept_or_reject'] not in ('accept', 'reject')
                    or record['result'] != ('accepted' if intent['accept_or_reject'] == 'accept' else 'rejected')):
                raise ValueError('Invalid diplomacy response intent/result')
            _id(record['executing_person_id'])
            _id(record['proposal_delivery_receipt_id'])
            _ids(record['decision_ids'])
            if record['action_id'] is not None:
                _id(record['action_id'])
            if bool(record['decision_ids']) != (record['action_id'] is not None):
                raise ValueError('Incomplete diplomacy decision/action trace references')
    else:
        _id(record['dispatch_id'])
        typed_reference(record['recipient'])
        if record['message_kind'] not in ('proposal', 'response'):
            raise ValueError('Invalid diplomacy receipt message kind')
        flag = 'belief_accepted' if kind == 'delivery' else 'applied'
        if type(record[flag]) is not bool:
            raise ValueError('Invalid diplomacy perception flag')
        if kind == 'delivery':
            _id(record['application_receipt_id'])


def queued_projection(dispatch):
    result = copy.deepcopy(dispatch)
    result.update(status='queued', received_at=None, delivery_receipt_id=None,
                  application_receipt_id=None, belief_accepted=False)
    return result


def pending_projection(proposal):
    result = copy.deepcopy(proposal)
    result.update(status='pending', response_receipt_id=None)
    return result


def _existing_reference(world, reference):
    typed_reference(reference)
    if reference['kind'] == 'person':
        if reference['id'] not in world.npc_state['persons']:
            raise ValueError('Diplomacy references an unknown person')
    elif reference['id'] not in world.diplomacy_state['config']['player_bindings']:
        raise ValueError('Diplomacy references an unbound player')


def validate_trace_records(proposal, receipt, decisions, action, deliveries):
    """Link existing NPC traces to each participant's delivered evidence."""
    if not isinstance(decisions, list):
        raise ValueError('Malformed diplomacy NPC decision traces')
    if not receipt['decision_ids']:
        if decisions or action is not None:
            raise ValueError('Manual diplomacy response has unexpected NPC traces')
        return
    if [decision.get('id') for decision in decisions if isinstance(decision, dict)] != receipt['decision_ids']:
        raise ValueError('Diplomacy response decision references differ from traces')
    choices = {}
    for decision in decisions:
        _object(decision, DECISION_KEYS, 'NPC contact decision')
        for key in ('id', 'country', 'actor'):
            _id(decision[key])
        context = decision['context']
        _object(context, {'kind', 'proposal_id', 'proposal_delivery_receipt_id'}, 'decision context')
        proof = deliveries.get(context['proposal_delivery_receipt_id'])
        if (context['kind'] != 'contact' or context['proposal_id'] != proposal['id']
                or decision['date'] != receipt['date'] or decision['country'] != proposal['body']['recipient_country_id']
                or not proof or proof['kind'] != 'delivery' or proof['proposal_id'] != proposal['id']
                or proof['message_kind'] != 'proposal' or proof['date'] > receipt['date']
                or proof['recipient'] != {'kind': 'person', 'id': decision['actor']}
                or decision['actor'] in choices):
            raise ValueError('Diplomacy NPC decision lacks participant-specific delivery evidence')
        if decision['selected'] not in ('accept_contact', 'reject_contact', None):
            raise ValueError('Unsupported selected diplomacy alternative')
        if (not isinstance(decision['evaluations'], list) or not isinstance(decision['reason_trace'], list)
                or not isinstance(decision['basis'], dict)):
            raise ValueError('Malformed diplomacy NPC scorer trace')
        choices[decision['actor']] = decision['selected']
    _object(action, ACTION_KEYS, 'NPC contact action')
    for key in ('id', 'country', 'actor', 'proposer', 'executor', 'option'):
        _id(action[key])
    if (action['id'] != receipt['action_id'] or action['date'] != receipt['date']
            or action['kind'] != 'contact_response' or action['proposal_id'] != proposal['id']
            or action['response_receipt_id'] != receipt['id'] or action['country'] != proposal['body']['recipient_country_id']
            or action['actor'] != receipt['executing_person_id'] or action['executor'] != receipt['executing_person_id']
            or action['option'] != ('accept_contact' if receipt['result'] == 'accepted' else 'reject_contact')
            or not same_json(action['decision_ids'], receipt['decision_ids'])
            or action['proposer'] not in choices or choices[action['proposer']] != action['option']
            or not same_json(action['coalition'], choices)
            or action['result'] != {'status': 'executed', 'contact_result': receipt['result']}):
        raise ValueError('Diplomacy NPC execution trace conflicts with response receipt')
    json.dumps({'decisions': decisions, 'action': action}, sort_keys=True, allow_nan=False)


def validate_state(world, previous=None):
    """Audit resumable contact state without rejudging historical authority."""
    from .diplomacy_intelligence import ACTIVATION_ID
    state = world.diplomacy_state
    proposals, dispatches, receipts = (state[key] for key in ('proposals', 'dispatches', 'receipts'))
    if any(not isinstance(table, dict) for table in (proposals, dispatches, receipts)):
        raise ValueError('Malformed diplomacy record tables')
    if set(proposals) & set(dispatches) or set(proposals) & set(receipts) or set(dispatches) & set(receipts):
        raise ValueError('Diplomacy record identities collide across authoritative buckets')
    for identity, receipt in receipts.items():
        validate_receipt_record(receipt)
        if identity != receipt['id'] or receipt['date'] > world.date:
            raise ValueError('Invalid diplomacy receipt identity/date')
        if receipt['kind'] == 'activation' and identity != ACTIVATION_ID:
            raise ValueError('Invalid diplomacy activation identity')
    linked_receipts, linked_dispatches, requests = {ACTIVATION_ID}, set(), set()
    for identity, proposal in proposals.items():
        validate_proposal_record(proposal)
        body = proposal['body']
        if identity != proposal['id'] or body['created_at'] > world.date:
            raise ValueError('Invalid diplomacy proposal identity/date')
        for key in ('proposer_country_id', 'recipient_country_id'):
            if body[key] not in world.countries:
                raise ValueError('Diplomacy proposal references an unknown country')
        for key in ('sender_person_id', 'recipient_person_id'):
            _existing_reference(world, {'kind': 'person', 'id': body[key]})
        for reference in [body['principal_ref'], *body['reply_recipients']]:
            _existing_reference(world, reference)
        principal = body['principal_ref']
        expected_replies = (state['config']['reply_recipients'].get(principal['id'], [principal])
                            if principal['kind'] == 'player' else [principal])
        if not same_json(body['reply_recipients'], expected_replies):
            raise ValueError('Diplomacy reply recipients differ from frozen configured principal route')
        submission = receipts.get(proposal['submission_receipt_id'])
        if (submission is None or submission['kind'] != 'submission'
                or submission['proposal_id'] != identity or submission['date'] != body['created_at']
                or submission['request_id'] != body['request_id']
                or not same_json(submission['principal_ref'], body['principal_ref'])):
            raise ValueError('Diplomacy proposal lacks its submission receipt')
        auth = submission['authorization']
        if auth['country_id'] != body['proposer_country_id'] or auth['person_id'] != body['sender_person_id']:
            raise ValueError('Diplomacy submission authority/body mismatch')
        for key in ('proposer_country_id', 'recipient_country_id', 'sender_person_id', 'recipient_person_id', 'content'):
            if not same_json(submission['intent'][key], body[key]):
                raise ValueError('Diplomacy submission intent differs from immutable body')
        routes = [(submission, 'proposal', [{'kind': 'person', 'id': body['recipient_person_id']}])]
        if proposal['status'] != 'pending':
            response = receipts.get(proposal['response_receipt_id'])
            if (response is None or response['kind'] != 'response' or response['proposal_id'] != identity
                    or response['result'] != proposal['status'] or response['date'] < body['created_at']
                    or response['authorization']['country_id'] != body['recipient_country_id']
                    or response['authorization']['person_id'] != response['executing_person_id']):
                raise ValueError('Diplomacy terminal state lacks its response execution receipt')
            proof = receipts.get(response['proposal_delivery_receipt_id'])
            if (not proof or proof['kind'] != 'delivery' or proof['proposal_id'] != identity
                    or proof['message_kind'] != 'proposal' or proof['date'] > response['date']
                    or proof['recipient'] != {'kind': 'person', 'id': response['executing_person_id']}):
                raise ValueError('Diplomacy response lacks executor-specific proposal delivery evidence')
            routes.append((response, 'response', body['reply_recipients']))
        for receipt, kind, recipients in routes:
            key = receipt['principal_ref']['kind'], receipt['principal_ref']['id'], receipt['request_id']
            if key in requests or receipt['id'] in linked_receipts:
                raise ValueError('Repeated diplomacy request or execution receipt')
            requests.add(key)
            linked_receipts.add(receipt['id'])
            messages = [dispatches.get(key) for key in receipt['dispatch_ids']]
            expected_payload = proposal_payload(proposal) if kind == 'proposal' else response_payload(proposal, receipt)
            if (any(message is None for message in messages) or len(messages) != len(recipients)
                    or {tuple(sorted(message['recipient'].items())) for message in messages}
                    != {tuple(sorted(reference.items())) for reference in recipients}):
                raise ValueError('Diplomacy dispatches differ from explicit receipt routes')
            for message in messages:
                if (message['id'] in linked_dispatches or message['proposal_id'] != identity
                        or message['message_kind'] != kind or message['sent_at'] != receipt['date']
                        or not same_json(message['payload'], expected_payload)):
                    raise ValueError('Diplomacy frozen dispatch differs from execution receipt')
                linked_dispatches.add(message['id'])
    if linked_dispatches != set(dispatches):
        raise ValueError('Orphan diplomacy dispatch')
    sequences, applications = set(), set()
    for identity, dispatch in dispatches.items():
        validate_dispatch_record(dispatch)
        _existing_reference(world, dispatch['recipient'])
        delay = state['config']['proposal_delay_days' if dispatch['message_kind'] == 'proposal' else 'response_delay_days']
        if (identity != dispatch['id'] or dispatch['sent_at'] > world.date
                or dispatch['sequence'] in sequences or dispatch['sequence'] >= state['next_ids']['sequence']
                or dispatch['delivery_date'] != (canonical_date(dispatch['sent_at']) + timedelta(days=delay)).isoformat()):
            raise ValueError('Invalid diplomacy dispatch sequence/date/delay')
        sequences.add(dispatch['sequence'])
        if dispatch['status'] == 'delivered':
            delivery = receipts.get(dispatch['delivery_receipt_id'])
            application = receipts.get(dispatch['application_receipt_id'])
            if not delivery or not application or delivery['kind'] != 'delivery' or application['kind'] != 'application':
                raise ValueError('Missing diplomacy delivery/application receipt')
            for receipt in (delivery, application):
                if (receipt['proposal_id'] != dispatch['proposal_id'] or receipt['message_kind'] != dispatch['message_kind']
                        or not same_json(receipt['recipient'], dispatch['recipient'])):
                    raise ValueError('Diplomacy receipt dispatch identity mismatch')
                linked_receipts.add(receipt['id'])
            if (delivery['dispatch_id'] != identity or delivery['date'] != dispatch['received_at']
                    or delivery['application_receipt_id'] != application['id']
                    or delivery['belief_accepted'] is not dispatch['belief_accepted']
                    or dispatch['received_at'] > world.date or application['date'] > dispatch['received_at']
                    or dispatch['belief_accepted'] != (application['dispatch_id'] == identity and application['applied'])):
                raise ValueError('Invalid diplomacy receipt lifecycle')
    for identity, receipt in receipts.items():
        if receipt['kind'] not in ('delivery', 'application'):
            continue
        dispatch = dispatches.get(receipt['dispatch_id'])
        if not dispatch or dispatch['status'] != 'delivered':
            raise ValueError('Orphan diplomacy perception/delivery receipt')
        if receipt['kind'] == 'application':
            key = receipt['recipient']['kind'], receipt['recipient']['id'], receipt['proposal_id'], receipt['message_kind']
            if key in applications or dispatch['application_receipt_id'] != identity or receipt['applied'] is not True:
                raise ValueError('Repeated or unapplied diplomacy application receipt')
            applications.add(key)
        elif dispatch['delivery_receipt_id'] != identity:
            raise ValueError('Orphan diplomacy delivery receipt')
    if linked_receipts != set(receipts):
        raise ValueError('Orphan diplomacy receipt')
    if (not isinstance(state['outbox'], list) or len(set(state['outbox'])) != len(state['outbox'])
            or set(state['outbox']) != {identity for identity, dispatch in dispatches.items() if dispatch['status'] == 'queued'}):
        raise ValueError('Diplomacy outbox differs from queued dispatches')
    for kind, table in (('proposal', proposals), ('dispatch', dispatches), ('receipt', receipts)):
        prefix = 'diplomacy-' + kind + '-'
        allocated = [int(key[len(prefix):]) for key in table if key.startswith(prefix)
                     and key[len(prefix):].isascii() and key[len(prefix):].isdigit()]
        if state['next_ids'][kind] <= len(table) or allocated and state['next_ids'][kind] <= max(allocated):
            raise ValueError('Diplomacy counter precedes allocated records')
    decision_prefix = 'decision-'
    contact_decisions = [identity for receipt in receipts.values() if receipt['kind'] == 'response'
                         for identity in receipt['decision_ids']]
    allocated_decisions = [int(identity[len(decision_prefix):]) for identity in contact_decisions
                          if identity.startswith(decision_prefix) and identity[len(decision_prefix):].isascii()
                          and identity[len(decision_prefix):].isdigit()]
    if allocated_decisions and world.npc_state['next_decision_id'] <= max(allocated_decisions):
        raise ValueError('NPC decision counter precedes durable contact decision references')
    old = previous.diplomacy_state if previous is not None else None
    if old is not None:
        for bucket in ('proposals', 'dispatches', 'receipts'):
            for identity, record in old[bucket].items():
                new = state[bucket].get(identity)
                if new is None:
                    raise ValueError('Immutable diplomacy record removed')
                if bucket == 'receipts' and not same_json(new, record):
                    raise ValueError('Immutable diplomacy receipt changed')
                if bucket == 'proposals' and (not same_json(pending_projection(new), pending_projection(record))
                        or record['status'] != 'pending' and not same_json(new, record)):
                    raise ValueError('Immutable diplomacy proposal changed')
                if bucket == 'dispatches' and (not same_json(queued_projection(new), queued_projection(record))
                        or record['status'] == 'delivered' and not same_json(new, record)):
                    raise ValueError('Immutable diplomacy dispatch changed')
