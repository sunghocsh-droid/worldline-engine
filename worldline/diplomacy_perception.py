"""Received contact envelopes adapted into the existing perception stores."""
import copy
import hashlib
import json

from . import npc_state
from .models import Report


def perception_key(payload, message_kind):
    identity = json.dumps([payload['proposal_id'], message_kind], ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False)
    return 'diplomacy:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


def apply_person(world, dispatch):
    payload, kind = dispatch['payload'], dispatch['message_kind']
    actor = dispatch['recipient']['id']
    key = perception_key(payload, kind)
    as_of = payload['created_at' if kind == 'proposal' else 'responded_at']
    counterpart = payload['proposer_country_id' if kind == 'proposal' else 'recipient_country_id']
    data = {'event_id': key, 'day': as_of, 'subject': counterpart, 'category': 'custom:diplomacy',
            'facts': {'contact': copy.deepcopy(payload)}, 'diplomacy': copy.deepcopy(payload), 'message_kind': kind}
    npc_state.receive_report(world.npc_state, actor, key, data, as_of, payload['source_ref']['id'],
                             confidence=1.0, reliability=1.0)
    world.npc_state['reports'][actor][key]['received_at'] = world.date
    npc_state.remember(world.npc_state, actor, key, interpretation={'contact': copy.deepcopy(payload)},
        valence=0 if kind == 'proposal' else (1 if payload['result'] == 'accepted' else -1),
        reliability=1.0, tags=['diplomacy', 'contact', kind], enduring=False)
    world.npc_state['memories'][actor][-1]['received_date'] = world.date


def player_report(payload, message_kind):
    as_of = payload['created_at' if message_kind == 'proposal' else 'responded_at']
    return Report(as_of=as_of, source=payload['source_ref']['id'], confidence=1.0,
                  data={'message_kind': message_kind, 'contact': copy.deepcopy(payload)})
