"""Adapt accepted frozen intelligence into existing NPC/player perceptions."""
import copy
import hashlib
import json

from . import npc_state
from .models import Report


def perception_key(payload):
    identity = json.dumps([payload['subject'], payload['topic']],
                          ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return 'intelligence:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


def apply_person(world, dispatch):
    payload = dispatch['payload']
    actor = dispatch['recipient']['id']
    key = perception_key(payload)
    provenance = payload['provenance']
    data = {'event_id': key, 'day': payload['as_of'],
            'subject': payload['subject'], 'category': 'custom:intelligence',
            'facts': copy.deepcopy(payload['observed_data']),
            'intelligence': copy.deepcopy(payload)}
    npc_state.receive_report(world.npc_state, actor, key, data,
        payload['as_of'], provenance['label'],
        confidence=provenance['confidence'], reliability=provenance['reliability'])
    world.npc_state['reports'][actor][key]['received_at'] = world.date
    npc_state.remember(world.npc_state, actor, key,
        interpretation={'intelligence': copy.deepcopy(payload)},
        reliability=provenance['reliability'], tags=['intelligence'], enduring=False)
    # Observation time stays old; autobiographical experience begins on receipt.
    world.npc_state['memories'][actor][-1]['received_date'] = world.date


def player_report(payload):
    return Report(as_of=payload['as_of'], source=payload['provenance']['label'],
                  confidence=payload['provenance']['confidence'],
                  data=copy.deepcopy(payload))
