// API contract types — mirror backend/app/api/serializers.py.

export type Role = "admin" | "officer" | "approver" | "viewer";

export interface User { id: number; email: string; name: string; role: Role; is_active: boolean }

export interface ConsentInfo { granted: boolean; record_id: number; status: string; effective_at: string; source: string }

export interface DataIssue { field: string; code: string; message: string; severity: "ERROR" | "WARNING" }

export interface Student {
  id: number; roll_number: string; full_name: string; email: string | null; phone: string | null;
  branch: string | null; branch_label: string | null; branch_raw: string | null; cgpa: number | null;
  active_backlogs: number | null; graduation_year: number | null; data_issues: DataIssue[];
  record_version: number; consent: ConsentInfo | null; consent_granted: boolean; updated_at: string; created_at: string;
}

export interface StudentDetail extends Student {
  placement_history: PlacementRow[];
  requests: StudentRequest[];
  emails: EmailItem[];
  consent_history: { id: number; scope: string; status: string; source: string; evidence_ref: string | null; effective_at: string; recorded_by_id: number | null }[];
  decisions: { drive_id: number; drive_title: string; criteria_version: number; decision: Decision; roster_status: RosterStatus; reasons: string[]; rule_results: RuleResult[]; evaluated_at: string }[];
  assignments: { drive_id: number; drive_title: string; status: string; note: string | null; starts_at: string; ends_at: string }[];
}

export interface Paged<T> { items: T[]; total: number; page: number; page_size: number }

export interface Destination {
  id: number; name: string; kind: "LOCAL_MOCK_FORM" | "ORGANISER_FORM"; base_url: string; form_ref: string;
  is_enabled: boolean; config_version: number; form_url: string; is_local_mock: boolean;
}

export interface Criteria {
  id: number; version: number; min_cgpa: number | null; cgpa_comparison: string; backlog_policy: BacklogPolicy;
  max_active_backlogs: number | null; allowed_branches: string[]; graduation_years: number[];
  required_consent_scope: string; source_text: string | null; source_reference: string | null;
  change_note: string | null; created_at: string; created_by_id: number | null;
}

export type BacklogPolicy = "NO_ACTIVE_BACKLOGS" | "MAX_ALLOWED" | "NOT_CONSIDERED";
export type Decision = "ELIGIBLE" | "INELIGIBLE" | "UNKNOWN";
export type RosterStatus = "INCLUDED" | "EXCLUDED" | "HELD";

export interface Drive {
  id: number; title: string; role_title: string | null; location: string | null;
  employer: { id: number; name: string }; starts_at: string; ends_at: string; timezone: string;
  starts_at_local: string; ends_at_local: string; status: string; schedule_version: number;
  source_reference: string | null; notes: string | null; destination: Destination | null;
  criteria: Criteria | null; updated_at: string;
  latest_evaluation?: { included: number; excluded: number; held: number; evaluated_at: string } | null;
}

export interface DriveDetail extends Drive { runs: AgentRun[]; approvals: Approval[]; uploads: Upload[] }

export interface Employer { id: number; name: string; industry: string | null; website: string | null; contact_email: string | null; drive_count: number }

export interface RuleResult { rule: string; outcome: "PASS" | "FAIL" | "UNKNOWN" | "NOT_APPLICABLE"; expected: string; observed: string | null; reason: string }

export interface Clash {
  other_drive_id: number; other_drive_title: string; other_assignment_status: string; other_starts_at: string;
  other_ends_at: string; overlap_start: string | null; overlap_end: string | null; buffer_minutes: number;
  resolution?: string | null; preferred_drive_id?: number | null;
}

export interface StudentOutcome {
  student_id: number; roll_number: string; full_name: string; branch: string | null; cgpa: number | null;
  active_backlogs: number | null; decision: Decision; roster_status: RosterStatus; rule_results: RuleResult[];
  reasons: string[]; missing_evidence: string[]; consent_ok: boolean; conflicts: Clash[]; already_submitted: boolean;
}

export interface Counts { total: number; eligible: number; ineligible: number; unknown: number; included: number; excluded: number; held: number; consent_missing: number; already_submitted: number; new_to_upload: number; no_longer_eligible: number }

export interface EligibilityResponse {
  drive_id: number; criteria: Record<string, unknown> & { version: number }; counts: Counts;
  exclusion_reasons: Record<string, number>; live_proposal_hash: string | null; evaluated_at: string; students: StudentOutcome[];
}

export interface DrivePair {
  drive_a: { id: number; title: string; starts_at: string; ends_at: string; timezone: string };
  drive_b: { id: number; title: string; starts_at: string; ends_at: string; timezone: string };
  overlap_start: string | null; overlap_end: string | null; buffer_minutes: number; students_assigned_to_both: number[];
}

export interface PlanStep { node: string; title: string; status: string; note?: string }

export interface PendingQuestion {
  type: string; question: string; options?: { value: string | number; label: string }[];
  approval_id?: number; upload_id?: number; evidence_id?: number;
}

export interface AgentRun {
  id: number; objective: string; planner_mode: string; llm_model: string | null; intent: string | null;
  drive_id: number | null; drive_title: string | null; status: string; current_step: string | null;
  plan: PlanStep[]; pending_question: PendingQuestion | null; summary: string | null; error: string | null;
  criteria_version: number | null; proposal_id: number | null; created_by_id: number | null;
  created_at: string; updated_at: string; is_test_fixture: boolean;
  state?: Record<string, any>;
  evidence?: { id: number; kind: string; source_url: string | null; sha256: string; retrieved_at: string; content: any }[];
}

export interface AgentEvent {
  id: number; seq: number; type: string; node: string | null; tool_name: string | null; status: string | null;
  message: string | null; payload: any; duration_ms: number | null; created_at: string;
}

export interface Proposal {
  id: number; drive_id: number; drive_title: string; run_id: number | null; criteria_version: number;
  schedule_version: number; destination: Destination; destination_fingerprint: string; roster_fingerprint: string;
  included_roll_numbers: string[]; kind: "FULL" | "SUPPLEMENT"; previously_submitted_roll_numbers: string[]; excluded: { roll_number: string; full_name: string; decision: Decision; reasons: string[] }[];
  held: { roll_number: string; full_name: string; reasons: string[]; conflicts: Clash[] }[];
  unresolved_issues: { type: string; roll_number: string; detail: string }[];
  proposal_hash: string; status: string; invalidated_reason: string | null; created_at: string;
}

export interface Approval {
  id: number; proposal_id: number; proposal_hash: string; status: string; requested_by: string;
  requested_for_user_id: number | null; decided_by: User | null; decided_at: string | null; decision_note: string | null;
  invalidated_reason: string | null; created_at: string; drive_id: number; drive_title: string;
  included_count: number; excluded_count: number; held_count: number; kind: "FULL" | "SUPPLEMENT"; previously_submitted_count: number;
  proposal?: Proposal; live_proposal_hash?: string | null; live_error?: string | null; is_current?: boolean;
}

export interface Verification {
  id: number; upload_id: number; status: string; method: string; expected_count: number; observed_count: number | null;
  missing_roll_numbers: string[]; unexpected_roll_numbers: string[]; duplicate_roll_numbers: string[];
  observed_snapshot: any; detail: string | null; receipt_hash: string | null; created_at: string;
}

export interface Upload {
  id: number; operation_key: string; approval_id: number; proposal_id: number; drive_id: number; drive_title: string;
  destination: Destination; status: string; attempts: number; submitted_roll_numbers: string[]; submitted_count: number;
  attempt_log: { at: string; step: string; [k: string]: unknown }[]; destination_receipt: any; error: string | null;
  executed_by: string; created_at: string; completed_at: string | null; latest_verification: Verification | null;
}

export interface UploadDetail extends Upload { verifications: Verification[]; attendance_sheets: AttendanceSheet[] }

export interface AttendanceSheet {
  id: number; drive_id: number; upload_id: number; verification_id: number; file_name: string; row_count: number;
  roll_numbers: string[]; excluded_at_generation: { roll_number: string; reason: string }[]; sha256: string;
  generated_by: string; created_at: string; purged: boolean; drive_title?: string;
}

export interface ExceptionItem {
  id: number; kind: string; severity: "INFO" | "WARNING" | "BLOCKING"; status: string; title: string; detail: any;
  drive_id: number | null; student_id: number | null; run_id: number | null; resolution: string | null;
  resolution_note: string | null; resolved_by_id: number | null; resolved_at: string | null; created_at: string; updated_at: string;
}

export interface AuditEvent {
  id: number; seq: number; occurred_at: string; actor_type: string; actor_id: string; action: string;
  entity_type: string | null; entity_id: string | null; operation_id: string | null; run_id: number | null;
  drive_id: number | null; details: any; prev_hash: string; hash: string;
}

export interface TestCheck { name: string; expected: unknown; actual: unknown; passed: boolean; detail?: unknown }

export interface TestRun {
  id: number; test_id: string; title: string; description: string; input_fixture: any; expected: any; actual: any;
  passed: boolean; checks: TestCheck[]; evidence: any; failure_details: string | null; environment: string;
  executed_at: string; executed_by_id: number | null; duration_ms: number;
}

export interface RosterImport {
  id: number; filename: string; file_sha256: string; status: string; column_mapping: Record<string, string | null>;
  total_rows: number; valid_rows: number; error_rows: number; created_count: number; updated_count: number;
  unchanged_count: number; issue_count: number; created_at: string; committed_at: string | null;
  rows?: { line: number; action: string; data: Record<string, any>; issues: DataIssue[] }[];
  errors?: (DataIssue & { line: number; roll_number: string | null })[];
  headers?: string[]; available_fields?: string[]; required_fields?: string[];
}

export interface Dashboard {
  metrics: {
    total_students: number; upcoming_drives: number; eligible_candidates: number; included_candidates: number;
    excluded_candidates: number; unresolved_exceptions: number; pending_approvals: number; drive_conflicts: number;
    open_clash_exceptions: number; verification_attention: number;
  };
  drive_readiness: { drive_id: number; title: string; employer: string; starts_at: string; timezone: string; status: string; stage: string; included: number; excluded: number; held: number; unknown: number; open_exceptions: number; pending_approvals: number }[];
  recent_runs: AgentRun[];
  upcoming_schedule: { drive_id: number; title: string; employer: string; starts_at: string; ends_at: string; timezone: string; location: string | null }[];
}

export interface Health { status: string; database: string; database_dialect: string; environment: string; demo_mode: boolean; planner: string; llm_model: string | null; time: string }

export interface StudentRequest {
  id: number; kind: "DRIVE_INVITATION" | "DATA_REQUEST"; status: string; student_id: number; roll_number: string;
  full_name: string; drive_id: number | null; drive_title: string | null; requested_fields: string[]; response: any;
  criteria_version: number | null; expires_at: string; responded_at: string | null; created_at: string;
}

export interface EmailItem {
  id: number; category: string; to_address: string | null; to_name: string | null; subject: string; status: string;
  transport: string; error: string | null; student_id: number | null; drive_id: number | null; upload_id: number | null;
  request_id: number | null; sent_by: string; created_at: string; sent_at: string | null; body_text?: string;
}

export interface PlacementRow {
  drive_id: number; drive_title: string; employer: string; starts_at: string; timezone: string; decision: Decision | null;
  roster_status?: RosterStatus; criteria_version: number | null; reasons: string[]; invitation: string | null;
  sent_to_employer: boolean; outcome: string | null;
}

export interface DriveResultRow {
  student_id: number; roll_number: string; full_name: string; branch: string | null; outcome: string | null;
  note: string | null; updated_at: string | null;
}

export interface PublicRequest {
  kind: "DRIVE_INVITATION" | "DATA_REQUEST"; status: string; first_name: string; roll_number: string; expires_at: string;
  requested_fields: { field: string; label: string }[]; branches: { code: string; label: string }[];
  drive?: { title: string; employer: string; slot: string; location: string | null; criteria: string };
}
