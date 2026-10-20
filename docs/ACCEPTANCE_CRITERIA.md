# Acceptance Criteria

Status reflects automated evidence in `docs/evidence/` (✅ = covered by a passing automated test).

| # | Criterion | Status | Evidence |
|---|---|---|---|
| A1 | CGPA threshold is inclusive (7.00 passes ≥ 7.0) | ✅ | TC1, `test_cgpa_and_backlog_rules` |
| A2 | Backlog policy follows employer setting (none / ≤ N / not considered) | ✅ | `test_max_allowed_backlog_policy_and_missing_limit` |
| A3 | Missing essential data → UNKNOWN with missing-evidence list, never ELIGIBLE | ✅ | TC1 (TC1-F, TC1-G) |
| A4 | Each decision stores per-rule results, reasons, criteria version, record version | ✅ | TC1, `eligibility_decisions` |
| A5 | Students matched only by roll number; same-name students handled | ✅ | TC2, `test_reimport_updates_by_roll_number_and_is_idempotent` |
| A6 | No consent → excluded from proposal, payload, destination, attendance | ✅ | TC3 |
| A7 | Consent re-checked immediately before upload | ✅ | TC3 phase B |
| A8 | Overlapping slots compared in UTC, half-open, flagged before approval, student held | ✅ | TC4, `test_interval_half_open_and_timezones` |
| A9 | Officer can resolve a clash; affected approvals re-validated | ✅ | `test_conflict_resolution_moves_student_and_invalidates_other_proposal`, e2e test 3 |
| A10 | Upload requires approver-role approval of the current proposal hash | ✅ | `test_unauthorized_users_cannot_approve`, live API test, e2e tests 1–2 |
| A11 | Any change after approval invalidates it | ✅ | `test_stale_approval_after_criteria_change`, `test_destination_change_after_approval_blocks_upload`, `test_revoked_consent_invalidates_pending_approval` |
| A12 | Agent cannot approve its own proposal | ✅ | `test_agent_has_no_approval_tool`, `test_llm_planner_uses_only_permitted_tools` |
| A13 | Retries never duplicate submissions | ✅ | `test_happy_path_and_duplicate_execution_is_idempotent`, `test_timeout_after_commit_is_reconciled_without_duplicate` |
| A14 | VERIFIED only on exact ID + count match; discrepancies exposed | ✅ | `test_destination_discrepancies_fail_verification[*]` |
| A15 | Unreadable destination never yields VERIFIED | ✅ | `test_unreadable_destination_gives_pending_verification_then_recovers` |
| A16 | Attendance only from VERIFIED uploads, minimal fields | ✅ | `test_attendance_sheet_contents`, discrepancy tests |
| A17 | Receipt with eligible/excluded counts, reasons, approval, verification | ✅ | live API test, `evidence/upload-receipt-local-mock.json` |
| A18 | Retrieved documents cannot change behaviour | ✅ | `test_prompt_injection_in_source_is_flagged_not_obeyed` |
| A19 | Audit history append-only and tamper-evident | ✅ | `test_audit_chain_valid_and_tamper_evident`, PG trigger check in TEST_EVIDENCE |
| A20 | What-if never modifies live data | ✅ | `test_what_if_is_read_only` |
| A21 | Every dashboard metric computed from DB and links to records | ✅ (manual + e2e screenshot) | `api/routes_core.dashboard`, `01-overview.png` |
| A22 | Deployed public URL | ❌ not yet | needs a PostgreSQL database decision — see DEPLOYMENT.md |
| A23 | Integration with organiser's official form | ❌ not available | LOCAL MOCK used; adapter is replaceable |
| A24 | Live LLM planning | ⚠️ implemented, not exercised live | no API key in build environment |
