# Product Specification

## Problem
A placement officer must, for every employer drive: collect a consented roster, apply the employer's rules (CGPA,
backlogs, branch), avoid putting a student on two drives at the same time, upload the eligible list to the employer's
drive form, produce an attendance sheet, and be able to prove what was sent and why others were excluded. Today this is
spreadsheet work with silent failure modes: name-based matching ("which Rahul Kumar?"), students shared without
consent, clashes discovered on the day, uploads that partially fail, and no record of who approved what.

## Users
| User | Needs |
|---|---|
| Placement officer | Prepare rosters fast, see exactly why each student is in/out, resolve exceptions |
| Head of placements (approver) | Approve exactly what will be sent, nothing more; know it can't change after approval |
| Administrator | Configure destinations, policy settings, users; run demos safely |
| Auditor / reviewer | Trace any upload back to inputs, criteria version, approver and verification |

## Product promise
Delegate the drive workflow to an agent → inspect its evidence and proposal → approve the exact operation → verify the result.

## MVP scope (implemented)
1. Student registry with CSV/XLSX import, column mapping, validation preview, safe re-import by roll number, consent history.
2. Drives with timezone-aware schedules, versioned eligibility criteria, allowlisted destinations.
3. Deterministic eligibility engine (ELIGIBLE / INELIGIBLE / UNKNOWN, per-rule evidence).
4. Consent enforcement (excluded from proposal, payload, destination and attendance; re-checked before upload).
5. Schedule-clash detection (half-open UTC intervals, configurable buffer) with HOLD + officer resolution.
6. Agent with natural-language goals, typed tools, durable state, clarification and approval interrupts.
7. Approval Center: hash-bound approvals, approver role only, automatic invalidation on change.
8. Idempotent upload with reconciliation of uncertain outcomes; independent read-back verification.
9. Receipt and attendance sheet (CSV/XLSX) from verified uploads only.
10. Exception Inbox, What-if simulator, Test Lab (TC1–TC4), hash-chained Audit Explorer, Settings & Integrations.

## Explicit non-goals
Real student data; contacting real employer systems; bypassing OTP/CAPTCHA; an LLM deciding eligibility;
multi-tenant institutions; SSO.

## Institutional policy that is configurable (not invented)
Clash buffer minutes · attendance fields · attendance retention days · default timezone. Employer rules are always
per-drive criteria versions entered by an officer; the agent cross-checks them against the employer's published text
but never changes them.

## Success criteria
- All four official scenarios pass from fixture to destination read-back (see TEST_EVIDENCE).
- No path exists for an upload without an approver's approval of the current hash (tested).
- Every upload is VERIFIED only when the exact ID multiset matches (tested with partial/duplicate/extra faults).
- Every consequential action appears in a verifiable audit chain.
