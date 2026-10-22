# API Reference

Generated from the live OpenAPI schema (`GET /openapi.json`, interactive docs at `/docs`).
Snapshot: [`docs/evidence/openapi.json`](evidence/openapi.json). All routes except auth, health and demo-accounts require a session (cookie `pos_session` or `Authorization: Bearer`).

Errors use `{"detail": str, "code": str, "details": {...}}` with 401 (no session), 403 (role), 404, 409 (state conflict), 412 (precondition failed — stale proposal, consent re-check, blocked upload), 422 (validation).

## agent
| Method | Path | Summary |
|---|---|---|
| POST | `/api/agent/runs` | Create Run |
| GET | `/api/agent/runs` | List Runs |
| GET | `/api/agent/runs/{run_id}` | Get Run |
| GET | `/api/agent/runs/{run_id}/events` | Run Events |
| POST | `/api/agent/runs/{run_id}/answer` | Answer Run |
| GET | `/api/agent/tools` | Tool Catalog |

## approvals
| Method | Path | Summary |
|---|---|---|
| GET | `/api/approvals` | List Approvals |
| GET | `/api/approvals/{approval_id}` | Get Approval |
| POST | `/api/approvals/{approval_id}/approve` | Approve |
| POST | `/api/approvals/{approval_id}/reject` | Reject |
| POST | `/api/approvals/{approval_id}/execute` | Execute an approved proposal that has no agent run attached (or whose run stopped). |

## attendance
| Method | Path | Summary |
|---|---|---|
| GET | `/api/attendance` | List Sheets |
| POST | `/api/attendance` | Create Sheet |
| GET | `/api/attendance/{sheet_id}/download` | Download Sheet |

## audit
| Method | Path | Summary |
|---|---|---|
| GET | `/api/audit` | Audit Log |
| GET | `/api/audit/verify` | Audit Verify |
| GET | `/api/evidence/{evidence_id}` | Get Evidence |

## auth
| Method | Path | Summary |
|---|---|---|
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/me` | Me |
| GET | `/api/auth/demo-accounts` | Demo Accounts |

## conflicts
| Method | Path | Summary |
|---|---|---|
| GET | `/api/drives/{drive_id}/conflicts` | Drive Conflicts |
| GET | `/api/conflicts` | All Conflicts |

## dashboard
| Method | Path | Summary |
|---|---|---|
| GET | `/api/dashboard` | Dashboard |

## drives
| Method | Path | Summary |
|---|---|---|
| GET | `/api/employers` | List Employers |
| POST | `/api/employers` | Create Employer |
| GET | `/api/drives` | List Drives |
| POST | `/api/drives` | Create Drive |
| GET | `/api/drives/{drive_id}` | Get Drive |
| PATCH | `/api/drives/{drive_id}` | Patch Drive |
| GET | `/api/drives/{drive_id}/criteria` | Criteria Versions |
| POST | `/api/drives/{drive_id}/criteria` | Add Criteria |

## eligibility
| Method | Path | Summary |
|---|---|---|
| GET | `/api/drives/{drive_id}/eligibility` | Live, read-only evaluation of the current roster against current criteria. |
| POST | `/api/drives/{drive_id}/simulate` | Simulate |

## exceptions
| Method | Path | Summary |
|---|---|---|
| GET | `/api/exceptions` | List Exceptions |
| POST | `/api/exceptions/{exception_id}/resolve` | Resolve Exception |

## health
| Method | Path | Summary |
|---|---|---|
| GET | `/api/health` | Health |

## imports
| Method | Path | Summary |
|---|---|---|
| GET | `/api/roster-imports/template.csv` | Import Template |
| POST | `/api/roster-imports/preview` | Preview Import |
| GET | `/api/roster-imports` | List Imports |
| GET | `/api/roster-imports/{import_id}` | Get Import |
| POST | `/api/roster-imports/{import_id}/commit` | Commit Import |
| POST | `/api/roster-imports/{import_id}/discard` | Discard Import |
| GET | `/api/roster-imports/{import_id}/errors.csv` | Import Errors |

## integrations
| Method | Path | Summary |
|---|---|---|
| GET | `/api/destinations` | List Destinations |
| POST | `/api/destinations` | Create Destination |
| PATCH | `/api/destinations/{destination_id}` | Patch Destination |
| GET | `/api/integrations` | Integrations |
| GET | `/api/integrations/mock-portal/faults` | List Faults |
| POST | `/api/integrations/mock-portal/faults` | Arm Fault |

## settings
| Method | Path | Summary |
|---|---|---|
| GET | `/api/settings` | Get App Settings |
| PATCH | `/api/settings` | Patch Settings |
| GET | `/api/users` | List Users |
| POST | `/api/admin/demo-reset` | Demo Reset |
| POST | `/api/admin/retention/purge` | Retention Purge |

## students
| Method | Path | Summary |
|---|---|---|
| GET | `/api/students` | List Students |
| GET | `/api/students/{student_id}` | Get Student |
| PATCH | `/api/students/{student_id}` | Patch Student |
| POST | `/api/students/{student_id}/consent` | Set Consent |

## test-lab
| Method | Path | Summary |
|---|---|---|
| GET | `/api/tests` | List Tests |
| POST | `/api/tests/run` | Run Tests |
| GET | `/api/tests/runs` | Test History |
| GET | `/api/tests/runs.csv` | Test History Csv |

## uploads
| Method | Path | Summary |
|---|---|---|
| GET | `/api/uploads` | List Uploads |
| GET | `/api/uploads/{upload_id}` | Get Upload |
| POST | `/api/uploads/{upload_id}/verify` | Verify Upload |
| POST | `/api/uploads/{upload_id}/retry` | Reconcile-then-retry an UNCERTAIN/FAILED upload. Never creates a duplicate submission. |
| GET | `/api/uploads/{upload_id}/receipt` | Upload Receipt |

## LOCAL MOCK portal (separate app at `/mock-portal`, not part of the PlaceCrew API)
| Method | Path | Purpose |
|---|---|---|
| GET | `/forms/{ref}` | Drive form page: published criteria, schedule, field list |
| POST | `/forms/{ref}/submit` | Form-encoded candidate submission (`client_reference` = idempotency key) |
| GET | `/forms/{ref}/roster` | Registered candidates (read-back target) |
| GET | `/forms/{ref}/submissions?client_reference=` | Receipt lookup used for reconciliation |
| POST | `/admin/forms, /admin/faults, /admin/forms/{ref}/reset` | Token-protected setup and fault injection |
