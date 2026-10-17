"""Pure phase-transition gate for Worldline's future Chat/LLM adapter.

The existing Engine owns authoritative time, RNG, persistence, and military
resolution. This module only plans and validates the user-triggered transition.
No ordinary chat call should ever advance world time.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Literal

ApprovalKind = Literal[
    'routine_approvable', 'explicitly_ordered', 'contested',
    'sovereign_choice', 'contradictory', 'unauthorized'
]
Status = Literal['proposed', 'approved', 'issued', 'resolved', 'rejected']


class PhaseConflict(ValueError):
    """Stale, duplicate, forged or otherwise invalid phase transition."""


@dataclass(frozen=True)
class Matter:
    id: str
    kind: ApprovalKind
    status: Status = 'proposed'
    authorized: bool = True
    vetoed: bool = False
    resolved_by: str | None = None


@dataclass(frozen=True)
class Conflict:
    id: str
    origin_phase_id: str
    status: Literal['active', 'resolved'] = 'active'
    resolution_receipt: str | None = None


@dataclass(frozen=True)
class PhaseSnapshot:
    phase_id: str
    phase_seq: int
    date_label: str
    matters: tuple[Matter, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    last_committed_phase_id: str | None = None
    committed_effect_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TransitionPlan:
    expected_phase_id: str
    approval_ids: tuple[str, ...]
    deferred_ids: tuple[str, ...]
    warnings: tuple[str, ...]


def plan_transition(snapshot: PhaseSnapshot, requested_phase_id: str) -> TransitionPlan:
    """Only plan auto approvals; do not change the state or clock."""
    if requested_phase_id != snapshot.phase_id:
        raise PhaseConflict('stale phase id; transition not applied')
    if snapshot.last_committed_phase_id == snapshot.phase_id:
        raise PhaseConflict('phase already committed')
    ids = [m.id for m in snapshot.matters]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        raise PhaseConflict('duplicate or blank matter id')
    conflict_ids = [c.id for c in snapshot.conflicts]
    if len(conflict_ids) != len(set(conflict_ids)):
        raise PhaseConflict('duplicate conflict id')
    approved, deferred, warnings = [], [], []
    for m in snapshot.matters:
        if m.status != 'proposed':
            continue
        if m.kind == 'routine_approvable' and m.authorized and not m.vetoed:
            approved.append(m.id)
        else:
            deferred.append(m.id)
            if m.kind in ('contradictory', 'unauthorized') or m.vetoed:
                warnings.append(f'{m.id}: requires review or remains contested')
    return TransitionPlan(snapshot.phase_id, tuple(approved), tuple(deferred), tuple(warnings))


def commit_transition(
    snapshot: PhaseSnapshot,
    plan: TransitionPlan,
    *,
    next_date: Callable[[str], str],
    resolution_receipts: frozenset[str] = frozenset(),
) -> PhaseSnapshot:
    """Compute the next phase. The caller owns the authoritative transaction.

    next_date must be pure; actual clock, RNG, military events and persistence
    must be controlled by Engine and committed atomically with these changes.
    """
    if plan.expected_phase_id != snapshot.phase_id:
        raise PhaseConflict('plan is stale')
    if snapshot.last_committed_phase_id == snapshot.phase_id:
        raise PhaseConflict('phase already committed')
    if plan_transition(snapshot, snapshot.phase_id) != plan:
        raise PhaseConflict('transition plan changed or forged')
    new_date = next_date(snapshot.date_label)
    if not isinstance(new_date, str) or not new_date or new_date == snapshot.date_label:
        raise PhaseConflict('next world time must differ from current time')
    valid_resolutions = {c.resolution_receipt for c in snapshot.conflicts if c.resolution_receipt}
    if resolution_receipts - valid_resolutions:
        raise PhaseConflict('unknown political resolution receipt')
    approved = set(plan.approval_ids)
    next_matters = tuple(replace(m, status='approved') if m.id in approved else m
                         for m in snapshot.matters)
    next_conflicts = tuple(replace(c, status='resolved')
                           if c.resolution_receipt in resolution_receipts else c
                           for c in snapshot.conflicts)
    next_seq = snapshot.phase_seq + 1
    return PhaseSnapshot(
        phase_id=f'P{next_seq:04d}',
        phase_seq=next_seq,
        date_label=new_date,
        matters=next_matters,
        conflicts=next_conflicts,
        last_committed_phase_id=snapshot.phase_id,
        committed_effect_ids=snapshot.committed_effect_ids |
            frozenset(f'approve:{snapshot.phase_id}:{i}' for i in approved),
    )


@dataclass(frozen=True)
class EscapeSituation:
    enemy_at_inner_defenses: bool
    escort_available: bool
    exit_route_verified: bool
    route_contested: bool


def may_report_escape_success(situation: EscapeSituation, *, receipts: frozenset[str]) -> bool:
    """Evidence gate only; does not simulate escape or promise success."""
    if 'escape_completed' not in receipts:
        return False
    if not situation.escort_available and 'escort_reconstituted' not in receipts:
        return False
    if not situation.exit_route_verified and 'route_opened' not in receipts:
        return False
    if situation.route_contested and 'breakthrough_resolved' not in receipts:
        return False
    if situation.enemy_at_inner_defenses and 'enemy_contact_resolved' not in receipts:
        return False
    return True
