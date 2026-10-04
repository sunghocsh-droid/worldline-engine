"""Durable contact messages and explicit, simulation-time recipient knowledge.

Contacts are acknowledgements only. They never execute legacy diplomatic
orders or change relations, finance, resources, pressure, or war state.
"""
import copy
import hashlib
from datetime import timedelta

from . import runtime_queries
from .models import canonical_date
from .intelligence import _id, freeze, same_json
from .diplomacy_validation import (typed_reference, proposal_payload, response_payload,
    validate_proposal_record, validate_dispatch_record, validate_receipt_record)


def _state(world):
    if world.diplomacy_state is None:
        raise ValueError('Contact requires explicit D&I activation')
    return world.diplomacy_state


def _unavailable():
    # Neither pending/terminal truth nor a foreign person's private evidence
    # is disclosed by an unsuccessful actor-facing execution request.
    raise ValueError('Contact action unavailable')


def _recipient(world, reference, *, delivery=False):
    from .diplomacy_intelligence import _recipient as validate
    typed_reference(reference)
    validate(world, reference, _state(world)['config']['player_bindings'])
    if delivery and reference['kind'] == 'person' and world.npc_state['persons'][reference['id']]['active'] is not True:
        _unavailable()


def _content(content):
    if not isinstance(content, dict) or set(content) - {'message', 'topic'} or 'message' not in content:
        raise ValueError('Contact content requires a bounded message and optional topic')
    result = freeze({'message': content['message'], 'topic': content.get('topic', 'contact')}, 32768)
    for key, limit in (('message', 4096), ('topic', 128)):
        if not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > limit:
            raise ValueError('Invalid bounded contact content')
    return result


def _request_group(receipt):
    ref = receipt.get('principal_ref', {})
    return ref.get('kind'), ref.get('id'), receipt.get('request_id')


def request_receipt(world, request_id, principal_ref):
    state = _state(world)
    _id(request_id)
    typed_reference(principal_ref)
    return runtime_queries.first_grouped(state['receipts'], 'diplomacy.requests', _request_group,
        (principal_ref['kind'], principal_ref['id'], request_id))


def _retry(world, request_id, principal_ref, intent):
    receipt = request_receipt(world, request_id, principal_ref)
    if receipt is not None and not same_json(receipt['intent'], intent):
        raise ValueError('Contact request ID conflict: different intent')
    return receipt


def _authority(world, principal_ref, country, person_id):
    from .npc import _authority as authorized
    state = _state(world)
    typed_reference(principal_ref)
    npc = world.npc_state
    person = npc['persons'].get(person_id)
    government = npc['governments'].get(country)
    if (country not in world.countries or person is None or government is None
            or not authorized(npc, person_id, country, 'diplomatic_engagement')):
        _unavailable()
    binding = None
    if principal_ref['kind'] == 'player':
        binding = state['config']['player_bindings'].get(principal_ref['id'])
        if (binding is None or binding['country_id'] != country
                or binding['representative_person_id'] != person_id
                or country not in world.crisis_state['player_countries']):
            _unavailable()
    elif (principal_ref['id'] != person_id or not government['autonomous']
            or country in world.crisis_state['player_countries']):
        _unavailable()
    return freeze({'country_id': country, 'person_id': person_id, 'active': person['active'],
        'authority': person['authority'], 'ruler': government['ruler'], 'cabinet': government['cabinet'],
        'decision_rule': government['decision_rule'], 'player_binding': binding})


def _allocate(state, kind, table, reserved=()):
    number = state['next_ids'][kind]
    identities = set(reserved) | set(state['proposals']) | set(state['dispatches']) | set(state['receipts'])
    while 'diplomacy-' + kind + '-' + str(number) in identities:
        number += 1
    state['next_ids'][kind] = number + 1
    return 'diplomacy-' + kind + '-' + str(number)


def _dispatch(world, proposal, message_kind, recipient, payload):
    state = _state(world)
    _recipient(world, recipient)
    delay = state['config']['proposal_delay_days' if message_kind == 'proposal' else 'response_delay_days']
    dispatch = {'id': _allocate(state, 'dispatch', state['dispatches'], [proposal['id']]),
        'proposal_id': proposal['id'], 'message_kind': message_kind, 'recipient': copy.deepcopy(recipient),
        'payload': copy.deepcopy(payload), 'sequence': state['next_ids']['sequence'],
        'sent_at': world.date, 'delivery_date': (canonical_date(world.date) + timedelta(days=delay)).isoformat(),
        'status': 'queued', 'received_at': None, 'delivery_receipt_id': None,
        'application_receipt_id': None, 'belief_accepted': False}
    validate_dispatch_record(dispatch)
    state['next_ids']['sequence'] += 1
    state['dispatches'][dispatch['id']] = dispatch
    state['outbox'].append(dispatch['id'])
    return dispatch


def receipt_projection(receipt):
    result = {key: copy.deepcopy(receipt[key]) for key in ('id', 'kind', 'date', 'request_id', 'proposal_id', 'dispatch_ids')}
    if receipt['kind'] == 'submission':
        result['status'] = 'submitted'
    else:
        result.update(result=receipt['result'], executing_person_id=receipt['executing_person_id'])
    return result


def propose(world, request_id, proposer_country_id, recipient_country_id,
            sender_person_id, recipient_person_id, content, *, principal_ref, proposal_id=None):
    state = _state(world)
    _id(request_id)
    for identity in (proposer_country_id, recipient_country_id, sender_person_id, recipient_person_id):
        _id(identity)
    if proposal_id is not None:
        _id(proposal_id)
    typed_reference(principal_ref)
    intent = freeze({'operation': 'propose_contact', 'proposal_id': proposal_id,
        'proposer_country_id': proposer_country_id, 'recipient_country_id': recipient_country_id,
        'sender_person_id': sender_person_id, 'recipient_person_id': recipient_person_id,
        'content': _content(content), 'principal_ref': principal_ref})
    old = _retry(world, request_id, principal_ref, intent)
    if old is not None:
        return receipt_projection(old), []
    authorization = _authority(world, principal_ref, proposer_country_id, sender_person_id)
    recipient = world.npc_state['persons'].get(recipient_person_id)
    if (proposer_country_id == recipient_country_id or recipient_country_id not in world.countries
            or recipient is None or recipient['country'] != recipient_country_id or recipient['active'] is not True
            or recipient_country_id not in world.npc_state['governments']):
        _unavailable()
    if proposal_id in state['proposals'] or proposal_id in state['dispatches'] or proposal_id in state['receipts']:
        raise ValueError('Contact proposal ID conflict')
    replies = (state['config']['reply_recipients'].get(principal_ref['id'], [principal_ref])
               if principal_ref['kind'] == 'player' else [principal_ref])
    for reference in replies:
        _recipient(world, reference)
    identity = proposal_id or _allocate(state, 'proposal', state['proposals'])
    if proposal_id is not None:
        state['next_ids']['proposal'] += 1
        prefix = 'diplomacy-proposal-'
        suffix = proposal_id[len(prefix):] if proposal_id.startswith(prefix) else ''
        if suffix.isascii() and suffix.isdigit():
            state['next_ids']['proposal'] = max(state['next_ids']['proposal'], int(suffix) + 1)
    receipt_id = _allocate(state, 'receipt', state['receipts'], [identity])
    proposal = {'id': identity, 'body': {'request_id': request_id, 'kind': 'contact',
        **{key: intent[key] for key in ('principal_ref', 'proposer_country_id', 'recipient_country_id',
            'sender_person_id', 'recipient_person_id', 'content')},
        'reply_recipients': copy.deepcopy(replies), 'created_at': world.date,
        'source_ref': {'domain': 'person', 'id': sender_person_id}},
        'status': 'pending', 'submission_receipt_id': receipt_id, 'response_receipt_id': None}
    validate_proposal_record(proposal)
    dispatch = _dispatch(world, proposal, 'proposal', {'kind': 'person', 'id': recipient_person_id}, proposal_payload(proposal))
    receipt = {'id': receipt_id, 'kind': 'submission', 'date': world.date, 'request_id': request_id,
        'proposal_id': identity, 'principal_ref': copy.deepcopy(principal_ref), 'intent': intent,
        'authorization': authorization, 'dispatch_ids': [dispatch['id']]}
    validate_receipt_record(receipt)
    state['proposals'][identity] = proposal
    state['receipts'][receipt_id] = receipt
    return receipt_projection(receipt), [{'system': 'diplomacy', 'kind': 'proposal_submitted',
        'proposal': copy.deepcopy(proposal), 'submission_receipt': copy.deepcopy(receipt), 'dispatches': [copy.deepcopy(dispatch)]}]


def delivery_order(dispatch):
    return dispatch['delivery_date'], dispatch['sequence'], dispatch['id']


def recipient_key(dispatch):
    return dispatch['recipient']['kind'], dispatch['recipient']['id']


def received_proposal(world, actor, proposal_id):
    state = _state(world)
    candidates = runtime_queries.grouped_items(state['dispatches'], 'diplomacy.recipient', recipient_key, ('person', actor))
    return next((d for _, d in candidates if d['proposal_id'] == proposal_id
        and d['message_kind'] == 'proposal' and d['status'] == 'delivered'), None)


def _traces(world, proposal, receipt, recommendation):
    if recommendation is None:
        return [], None
    state = world.npc_state
    decisions = []
    for actor, advice in sorted(recommendation['recommendations'].items()):
        evidence = received_proposal(world, actor, proposal['id'])
        if evidence is None:
            _unavailable()
        decision = copy.deepcopy(advice)
        decision.update(id='decision-' + str(state['next_decision_id']), date=world.date,
            country=proposal['body']['recipient_country_id'], actor=actor,
            context={'kind': 'contact', 'proposal_id': proposal['id'],
                     'proposal_delivery_receipt_id': evidence['delivery_receipt_id']})
        state['next_decision_id'] += 1
        decisions.append(decision)
    action = {'id': 'action-contact-' + hashlib.sha256(proposal['id'].encode('utf-8')).hexdigest(),
        'date': world.date, 'country': proposal['body']['recipient_country_id'],
        'actor': receipt['executing_person_id'], 'kind': 'contact_response', 'proposal_id': proposal['id'],
        'response_receipt_id': receipt['id'], 'decision_ids': [d['id'] for d in decisions],
        'proposer': recommendation['proposer'], 'executor': receipt['executing_person_id'],
        'option': recommendation['chosen'], 'coalition': copy.deepcopy(recommendation['coalition']),
        'result': {'status': 'executed', 'contact_result': receipt['result']}}
    state['decisions'] = (state['decisions'] + decisions)[-128:]
    state['actions'] = (state['actions'] + [action])[-128:]
    receipt.update(decision_ids=action['decision_ids'], action_id=action['id'])
    return decisions, action


def respond(world, request_id, proposal_id, executing_person_id, accept_or_reject,
            *, principal_ref, _recommendation=None):
    state = _state(world)
    for identity in (request_id, proposal_id, executing_person_id):
        _id(identity)
    typed_reference(principal_ref)
    if accept_or_reject not in ('accept', 'reject'):
        raise ValueError('Contact response must be accept or reject')
    intent = freeze({'operation': 'respond_contact', 'proposal_id': proposal_id,
        'executing_person_id': executing_person_id, 'accept_or_reject': accept_or_reject, 'principal_ref': principal_ref})
    old = _retry(world, request_id, principal_ref, intent)
    if old is not None:
        return receipt_projection(old), []
    proposal = state['proposals'].get(proposal_id)
    evidence = received_proposal(world, executing_person_id, proposal_id)
    if proposal is None or evidence is None or proposal['status'] != 'pending':
        _unavailable()
    authorization = _authority(world, principal_ref, proposal['body']['recipient_country_id'], executing_person_id)
    result = 'accepted' if accept_or_reject == 'accept' else 'rejected'
    if _recommendation is not None and (_recommendation['proposal_id'] != proposal_id
            or _recommendation['executor'] != executing_person_id
            or _recommendation['chosen'] != ('accept_contact' if result == 'accepted' else 'reject_contact')):
        _unavailable()
    receipt = {'id': _allocate(state, 'receipt', state['receipts']), 'kind': 'response', 'date': world.date,
        'request_id': request_id, 'proposal_id': proposal_id, 'principal_ref': copy.deepcopy(principal_ref),
        'intent': intent, 'authorization': authorization, 'dispatch_ids': [],
        'proposal_delivery_receipt_id': evidence['delivery_receipt_id'], 'result': result,
        'executing_person_id': executing_person_id, 'decision_ids': [], 'action_id': None}
    decisions, action = _traces(world, proposal, receipt, _recommendation)
    payload = response_payload(proposal, receipt)
    dispatches = [_dispatch(world, proposal, 'response', ref, payload) for ref in proposal['body']['reply_recipients']]
    receipt['dispatch_ids'] = [dispatch['id'] for dispatch in dispatches]
    validate_receipt_record(receipt)
    proposal.update(status=result, response_receipt_id=receipt['id'])
    state['receipts'][receipt['id']] = receipt
    return receipt_projection(receipt), [{'system': 'diplomacy', 'kind': 'contact_responded',
        'proposal_id': proposal_id, 'response_receipt': copy.deepcopy(receipt),
        'dispatches': copy.deepcopy(dispatches), 'decisions': decisions, 'action': action}]


def due(world):
    state = _state(world)
    return [dispatch for dispatch in runtime_queries.ordered_values(state['dispatches'],
        'diplomacy.queued', lambda d: d['status'] == 'queued', delivery_order) if dispatch['delivery_date'] <= world.date]


def _application_key(receipt):
    reference = receipt.get('recipient', {})
    return receipt['kind'], reference.get('kind'), reference.get('id'), receipt.get('proposal_id'), receipt.get('message_kind')


def deliver_due(world):
    from .diplomacy_perception import apply_person
    state = _state(world)
    events = []
    for dispatch in due(world):
        _recipient(world, dispatch['recipient'], delivery=True)
        application = runtime_queries.first_grouped(state['receipts'], 'diplomacy.applications',
            _application_key, ('application', *recipient_key(dispatch), dispatch['proposal_id'], dispatch['message_kind']))
        accepted = application is None
        new_application = None
        if application is None:
            application = {'id': _allocate(state, 'receipt', state['receipts']), 'kind': 'application',
                'date': world.date, 'dispatch_id': dispatch['id'], 'proposal_id': dispatch['proposal_id'],
                'message_kind': dispatch['message_kind'], 'recipient': copy.deepcopy(dispatch['recipient']), 'applied': True}
            state['receipts'][application['id']] = application
            new_application = copy.deepcopy(application)
        if accepted and dispatch['recipient']['kind'] == 'person':
            apply_person(world, dispatch)
        receipt = {'id': _allocate(state, 'receipt', state['receipts']), 'kind': 'delivery', 'date': world.date,
            'dispatch_id': dispatch['id'], 'proposal_id': dispatch['proposal_id'], 'message_kind': dispatch['message_kind'],
            'recipient': copy.deepcopy(dispatch['recipient']), 'application_receipt_id': application['id'], 'belief_accepted': accepted}
        state['receipts'][receipt['id']] = receipt
        dispatch.update(status='delivered', received_at=world.date, delivery_receipt_id=receipt['id'],
            application_receipt_id=application['id'], belief_accepted=accepted)
        state['outbox'].remove(dispatch['id'])
        events.append({'system': 'diplomacy', 'kind': 'dispatch_delivered', 'dispatch': copy.deepcopy(dispatch),
            'delivery_receipt': copy.deepcopy(receipt), 'application_receipt': new_application})
    return events


def pending_received(world):
    state = _state(world)
    received = runtime_queries.ordered_values(state['dispatches'], 'diplomacy.received_proposals',
        lambda d: d['message_kind'] == 'proposal' and d['status'] == 'delivered', delivery_order)
    return list(dict.fromkeys(d['proposal_id'] for d in received if state['proposals'][d['proposal_id']]['status'] == 'pending'))


def actionable(world):
    from .npc_contact import recommend
    return any(recommend(world, identity) is not None for identity in pending_received(world))


def tick(world):
    from .npc_contact import recommend
    events = deliver_due(world)
    for identity in pending_received(world):
        recommendation = recommend(world, identity)
        if recommendation is None:
            continue
        executor = recommendation['executor']
        request = 'npc-contact-response:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
        _, response_events = respond(world, request, identity, executor,
            'accept' if recommendation['chosen'] == 'accept_contact' else 'reject',
            principal_ref={'kind': 'person', 'id': executor}, _recommendation=recommendation)
        events.extend(response_events)
    events.extend(deliver_due(world))
    return events


def _visible_receipt(receipt):
    if receipt['kind'] in ('submission', 'response'):
        return receipt_projection(receipt)
    return {key: copy.deepcopy(receipt[key]) for key in ('id', 'kind', 'date', 'dispatch_id', 'proposal_id', 'message_kind')}


def _principal_owner(receipt):
    reference = receipt.get('principal_ref', {})
    return reference.get('kind'), reference.get('id')


def _person_owner(receipt):
    if receipt['kind'] == 'submission':
        return receipt['intent']['sender_person_id']
    return receipt.get('executing_person_id') if receipt['kind'] == 'response' else None


def _message_key(dispatch):
    return (*recipient_key(dispatch), dispatch['proposal_id'], dispatch['message_kind'])


def view(world, subject_ref):
    """Build knowledge from submission/receipt evidence, never proposal truth."""
    state = _state(world)
    _recipient(world, subject_ref)
    key = subject_ref['kind'], subject_ref['id']
    history = [d for _, d in runtime_queries.grouped_items(state['dispatches'],
        'diplomacy.recipient', recipient_key, key) if d['status'] == 'delivered']
    history.sort(key=delivery_order)
    latest = runtime_queries.latest_by_group(state['dispatches'], 'diplomacy.latest_received',
        _message_key, delivery_order, lambda d: d['status'] == 'delivered', [_message_key(d) for d in history])
    contacts, receipts = {}, []

    def contact(identity):
        return contacts.setdefault(identity, {'proposal_id': identity, 'proposal': None, 'status': 'pending',
            'submitted_at': None, 'proposal_received_at': None, 'response': None,
            'response_received_at': None, 'response_executed_at': None})

    own_receipts = dict(runtime_queries.grouped_items(state['receipts'], 'diplomacy.principal_receipts', _principal_owner, key))
    if subject_ref['kind'] == 'person':
        own_receipts.update(runtime_queries.grouped_items(state['receipts'], 'diplomacy.person_receipts', _person_owner, subject_ref['id']))
    for receipt in own_receipts.values():
        kind = receipt['kind']
        if kind not in ('submission', 'response'):
            continue
        own = receipt['principal_ref'] == subject_ref or (subject_ref['kind'] == 'person' and
            (receipt['intent'].get('sender_person_id') if kind == 'submission' else receipt['executing_person_id']) == subject_ref['id'])
        if not own:
            continue
        entry = contact(receipt['proposal_id'])
        if kind == 'submission':
            intent = receipt['intent']
            entry['proposal'] = {'proposal_id': receipt['proposal_id'], 'kind': 'contact',
                **{field: copy.deepcopy(intent[field]) for field in ('proposer_country_id', 'recipient_country_id',
                    'sender_person_id', 'recipient_person_id', 'content')},
                'created_at': receipt['date'], 'source_ref': {'domain': 'person', 'id': intent['sender_person_id']}}
            entry['submitted_at'] = receipt['date']
        else:
            entry['status'] = receipt['result']
            entry['response_executed_at'] = receipt['date']
        receipts.append(_visible_receipt(receipt))
    for dispatch in history:
        entry = contact(dispatch['proposal_id'])
        current = latest[_message_key(dispatch)]['id'] == dispatch['id']
        if current and dispatch['message_kind'] == 'proposal':
            entry['proposal'] = copy.deepcopy(dispatch['payload'])
            entry['proposal_received_at'] = dispatch['received_at']
        elif current:
            entry.update(status=dispatch['payload']['result'], response=copy.deepcopy(dispatch['payload']),
                         response_received_at=dispatch['received_at'],
                         response_executed_at=dispatch['payload']['responded_at'])
        receipts.append(_visible_receipt(state['receipts'][dispatch['delivery_receipt_id']]))
    public_history = [{key: copy.deepcopy(dispatch[key]) for key in ('id', 'proposal_id', 'message_kind',
        'recipient', 'payload', 'sequence', 'sent_at', 'delivery_date', 'received_at', 'delivery_receipt_id')}
        for dispatch in history]
    return copy.deepcopy({'subject': subject_ref, 'contacts': [contacts[key] for key in sorted(contacts)],
        'history': public_history, 'receipts': receipts})
