# Chat phase integration v1 — 1592 playtest handoff for Codex

## Status

The new worldline/phase_gate.py and tests/test_phase_gate.py are **a pure, deterministic preflight slice only**. They do not connect Chat Lite to Engine.advance, alter time, resolve battles, or update SQLite saves. Full integration is future work. Existing Engine, World, saved RNG and ledgers are authoritative, not generated LLM prose.

## Player contract and time model

1. One turn = one phase; phase duration is event-driven.
2. World clock stays exactly unchanged during in-phase meetings, conversations, drafting or edits. Same-scene immediate royal signatures are possible; distant courier arrivals and enemy movements cannot magically complete.
3. Only a user NEXT_PHASE with matching phase_id triggers clock advance. A stale or double-clicked request has zero effect.
4. At that boundary, automatically approve *legal, non-vetoed, non-conflicting routine administrative proposals only*. Approval is not execution/completion. Sovereign choices like fleeing the capital, abdication and execution remain pending without express authorization. Conflicting orders do not both get approved.
5. During the same NEXT_PHASE transition, advance all NPC, enemy, faction, crisis, military, diplomatic, economy and logistics systems according to authority and physical constraints; the enemy never waits for the king. The next phase begins at the earliest meaningful event, potentially minutes or hours during battle. The current engine is day-granular and does not yet implement sub-day time; this needs explicit design, not a false claim.
6. Political disputes, ministers' grievances and resistance are persistent keyed objects requiring explicit resolution receipts. Jeong Cheol's appointment does not magically dissolve factional opposition. The same plea should not repeat indefinitely without new evidence, deadline, consequence or strategic change.
7. Commands have distinct proposed, authorized, dispatched, received, attempted, executed, failed, completed stages. A user saying "flee" does not create an escape_completed receipt.

## Playtest regressions to reproduce

- P0002, council: minister asks whether an army marching through Joseon to conquer Ming is actually a threat to Joseon. Enforce geopolitical knowledge and motive-risk coherence; intentional politically foolish statements are valid if their motives and resulting reactions persist.
- P0005, appoint Jeong Cheol: multiple factions oppose appointment; the conflict remains after advancing ten phases absent compromise, punishment or abandonment.
- P0009–P0014, supply authorization: repeated identical pleas disappear from view only when genuinely resolved, rejected or changed. Automatic routine approval can create administrative authority but cannot force warehouse officials to comply.
- P0011–P0019, king repeatedly presses NEXT_PHASE without deciding capital flight: clock only advances on those presses, but enemy continuously advances at every press. Court members may independently flee, refuse, maneuver or fail; king and enemy cannot remain locked in endless harmless requests.
- P0019–P0020, enemy at palace: king orders escape. Gate must block invented effortless success given an unverified exit, exhausted escorts, and enemy at inner defenses; campaign outcome might be capture, desperate breakout, separation, death or credible escape with receipts, not an authored auto-win.
- Longer 50+ phase no-action war and politics replay: time, RNG, unresolved matters, deliveries and results reproducible from save/load.

## Existing engine integration work delegated to Codex

1. Inspect existing Engine.advance, order_execution, npc_decision, npc_state, military_operations, diplomacy/intelligence, crisis, persistence and state_runtime first. Preserve current stable APIs, public behavior and tests.
2. Build a minimal opt-in ChatPhaseCoordinator, connecting a legal NEXT_PHASE request to a single-writer Engine transaction; perform the phase gate plan, validated routine receipts, NPC/adversary decisions and world advance atomically. No implicit clock tick during chat. Prevent replay of stale requests even after save/load.
3. Do not let LLM text mutate authoritative military or geopolitical state. Convert player intent into structured, validated orders and demand actual physical/travel/command completion evidence.
4. Preserve political-conflict objects and actor memories until a justified resolution. Implement in compatible state or document required migration and provide tests.
5. Separate full true_world from observed/player-known facts and individual NPC belief state. No unseen troop numbers or future historical information.
6. Add regression and replay tests on a fixed seed. Run adjacent targeted tests and the full suite. No unexplained changes in existing save schema, legacy results, or tests; do not weaken or skip any tests.
7. Keep new work on feature/chat-phase-gate-playtest-20261018. **Do not merge into master.** Supply test output, diff, remaining technical gaps and a reviewable PR update.

## Important quality/product notes

All tiers and reasoning levels must obey identical game-world rules and lose/win feasibility checks. Instant should remain believable. Higher reasoning effort may improve dialogue, nuanced NPC motivations and complex interdependencies, not safety from defeat or invented new physical capabilities. The Chat Lite v0.5 plugin is still prompt-driven and not yet wired to Python Engine.

## Codex task prompt

Inspect this file and the new worldline/phase_gate.py / tests/test_phase_gate.py. Implement a minimal opt-in, backward-compatible integration with the existing deterministic worldline-engine, preserving single-writer event ledger, RNG replay, previous save data and all tests. Time must advance **only on NEXT_PHASE**. Ordinary dialogues must never tick the world. Routine requests may be approved at that event, but rival factions, enemy commanders and logistics resolve according to their own state. Add tests for P0005 enduring conflict, P0011-P0019 no-action enemy progress, and P0019 palace escape where an escape command cannot bypass contested/unverified physical routes. Do not rewrite the core engine, do not merge master, do not claim that pure helper tests validate integration.