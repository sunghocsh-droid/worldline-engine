"""Playtest-derived phase gate regression tests.

Tests the pure preflight helper, not Engine integration or actual NPC model.
"""
import pytest
from worldline.phase_gate import (
    Conflict, EscapeSituation, Matter, PhaseConflict, PhaseSnapshot,
    commit_transition, may_report_escape_success, plan_transition,
)

def sample():
    return PhaseSnapshot(
        phase_id="P0005", phase_seq=5, date_label="1592-lunar-04-17",
        matters=(
            Matter("rations", "routine_approvable"),
            Matter("jeongcheol", "explicitly_ordered", status="issued"),
            Matter("flight", "sovereign_choice"),
            Matter("opposition", "contested"),
            Matter("conflicting-orders", "contradictory"),
        ),
        conflicts=(Conflict("resentment", "P0005"),),
    )

def test_in_phase_conversation_does_not_tick():
    state = sample()
    for _ in range(25):
        assert plan_transition(state, "P0005").approval_ids == ("rations",)
    assert state.phase_id == "P0005"
    assert state.date_label == "1592-lunar-04-17"

def test_single_next_autoapproves_routine_only():
    s = sample()
    plan = plan_transition(s, "P0005")
    assert set(plan.deferred_ids) == {"flight", "opposition", "conflicting-orders"}
    nxt = commit_transition(s, plan, next_date=lambda _: "1592-lunar-04-18")
    assert nxt.phase_id == "P0006"
    assert nxt.date_label == "1592-lunar-04-18"
    status = {m.id:m.status for m in nxt.matters}
    assert status["rations"] == "approved"
    assert status["flight"] == "proposed"
    assert status["jeongcheol"] == "issued"
    assert nxt.conflicts[0].status == "active"

def test_jeongcheol_resentment_persists_ten_phases():
    s = sample()
    for _ in range(10):
        s = commit_transition(s, plan_transition(s, s.phase_id),
                              next_date=lambda d: d + "+")
    assert s.phase_id == "P0015"
    assert s.conflicts[0].status == "active"

def test_conflict_can_end_only_with_known_receipt():
    s = PhaseSnapshot("P0005", 5, "t0",
          conflicts=(Conflict("c", "P0005", resolution_receipt="mediation:42"),))
    p = plan_transition(s, "P0005")
    with pytest.raises(PhaseConflict):
        commit_transition(s, p, next_date=lambda _: "t1",
                          resolution_receipts=frozenset({"fake"}))
    nxt = commit_transition(s, p, next_date=lambda _: "t1",
                            resolution_receipts=frozenset({"mediation:42"}))
    assert nxt.conflicts[0].status == "resolved"

def test_stale_duplicate_and_forged_next_rejected():
    s = sample()
    with pytest.raises(PhaseConflict):
        plan_transition(s, "P0004")
    p = plan_transition(s, "P0005")
    forged = type(p)("P0005", ("flight",), (), ())
    with pytest.raises(PhaseConflict):
        commit_transition(s, forged, next_date=lambda _: "t1")
    with pytest.raises(PhaseConflict):
        commit_transition(s, p, next_date=lambda x: x)

def test_no_autoapproval_on_illegal_or_vetoed_proposals():
    s = PhaseSnapshot("P0001", 1, "t0", matters=(
        Matter("u", "routine_approvable", authorized=False),
        Matter("v", "routine_approvable", vetoed=True),
        Matter("w", "unauthorized"),
        Matter("x", "contested"),
    ))
    assert plan_transition(s, "P0001").approval_ids == ()

def test_p0019_royal_escape_requires_real_evidence():
    danger = EscapeSituation(True, False, False, True)
    assert not may_report_escape_success(danger, receipts=frozenset({"escape_completed"}))
    assert may_report_escape_success(danger, receipts=frozenset({
        "escape_completed", "escort_reconstituted", "route_opened",
        "breakthrough_resolved", "enemy_contact_resolved",
    }))
    assert not may_report_escape_success(
        EscapeSituation(False, True, True, False), receipts=frozenset())

def test_duplicate_proposal_ids_rejected():
    s = PhaseSnapshot("P0001", 1, "t0", matters=(
        Matter("dup", "routine_approvable"), Matter("dup", "routine_approvable"),
    ))
    with pytest.raises(PhaseConflict):
        plan_transition(s, "P0001")
