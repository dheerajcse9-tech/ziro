"""The agent crew: named agents, what each is responsible for, and which tools it may use.

Every workflow step (graph node) is executed by exactly one agent. While a
node runs, `CURRENT_AGENT` names that agent: its events are labelled with
it, and `call_tool` refuses any tool outside the agent's own list. Agents
that react to events (a student submitting a form) or run on a timer
(autopilot) act through services and record themselves in the audit ledger
as `agent:<key>`.

No agent can approve anything: approval is a human action in the Approval
Center, bound to the exact roster hash the approver reviewed.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

STAGES = {
    1: "Drive intake — Drive upload officer",
    2: "Drive run — Placement officer",
    3: "Approval & follow-up — Approver",
}


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    stage: int
    owner: str  # the human role this agent works for
    purpose: str
    tools: tuple[str, ...] = ()
    nodes: tuple[str, ...] = ()
    trigger: str = "workflow"  # workflow | autopilot | event
    uses_ai: bool = False

    @property
    def actor(self) -> str:
        return f"agent:{self.key}"


AGENTS: tuple[AgentSpec, ...] = (
    # ---- Stage 1: company email -> drive -> consent form -> every student
    AgentSpec("mailbox", "Mailbox Agent", 1, "coordinator",
              "Watches the placement mailbox (Gmail) and picks up new company recruitment emails. Read-only.",
              trigger="autopilot"),
    AgentSpec("email_reader", "Email Reader Agent", 1, "coordinator",
              "Reads a company email and extracts company, role, date, time, venue, deadlines and eligibility criteria. "
              "Uses Gemini when configured and always cross-checks it with rule-based reading.",
              tools=("read_company_email",), nodes=("read_email",), uses_ai=True),
    AgentSpec("criteria_check", "Criteria Check Agent", 1, "coordinator",
              "Checks that everything needed is present and valid: dates, registration deadline, criteria, sender, "
              "duplicates and suspicious instructions. Stops for the drive upload officer when something is wrong.",
              tools=("check_company_email",), nodes=("check_email",)),
    AgentSpec("drive_builder", "Drive Builder Agent", 1, "coordinator",
              "Creates the drive with versioned eligibility criteria, the registration deadline and the student-list due date.",
              tools=("create_drive_from_email",), nodes=("create_drive",)),
    AgentSpec("consent_form", "Consent Form Agent", 1, "coordinator",
              "Prepares the drive's consent form: what will be shared, with whom, the criteria and when it closes.",
              tools=("create_consent_form",), nodes=("consent_form",)),
    AgentSpec("outreach", "Student Outreach Agent", 1, "coordinator",
              "Emails every student a personal link to the drive's consent form (one form per student per drive).",
              tools=("send_drive_invitations",), nodes=("publish", "invite")),
    # ---- Stage 2: the placement officer runs the drive
    AgentSpec("planner", "Goal Planner Agent", 2, "officer",
              "Understands the officer's request, picks the drive and the workflow. Read-only tools only.",
              tools=("list_drives", "get_student_roster", "get_drive_requirements", "simulate_roster_changes"),
              nodes=("interpret_goal", "validate_scope"), uses_ai=True),
    AgentSpec("evidence", "Evidence Agent", 2, "officer",
              "Retrieves the employer's published form and cross-checks it against the configured criteria.",
              tools=("get_drive_requirements",), nodes=("retrieve_evidence", "interpret_criteria")),
    AgentSpec("eligibility", "Eligibility Agent", 2, "officer",
              "Applies the criteria (CGPA, backlogs, branch, batch) to every student with the deterministic engine.",
              tools=("validate_roster", "evaluate_eligibility", "simulate_roster_changes"), nodes=("evaluate_roster", "simulate")),
    AgentSpec("consent", "Consent Agent", 2, "officer",
              "Keeps only students who answered YES on the drive's consent form; reports declined and unanswered.",
              tools=("consent_status",), nodes=("check_consent",)),
    AgentSpec("conflict", "Conflict Agent", 2, "officer",
              "Finds schedule clashes with the student's other drives and holds those students for a decision.",
              tools=("detect_drive_conflicts",), nodes=("check_conflicts",)),
    AgentSpec("roster", "Roster Agent", 2, "officer",
              "Builds the hashed roster proposal (or a supplementary one for late additions) and sends it to the approver.",
              tools=("create_approval_request",), nodes=("propose",)),
    # ---- Stage 3: the approver
    AgentSpec("review", "Approval Review Agent", 3, "approver",
              "Independently re-checks every student on the proposal (criteria, consent, clashes, hash, deadlines) and "
              "lists who is missing details or consent. Advises only — it cannot approve.",
              tools=("review_roster_proposal",), nodes=("review",)),
    AgentSpec("form_builder", "Form Builder Agent", 3, "approver",
              "Builds a personal form for each student with exactly their missing details (and consent, if unanswered) "
              "and a closing date and time.",
              tools=("plan_followup_forms",), nodes=("build_forms",)),
    AgentSpec("followup_mailer", "Follow-up Mailer Agent", 3, "approver",
              "Emails each student their personal form link.",
              tools=("send_followup_forms", "request_missing_data"), nodes=("send_forms", "request_data")),
    AgentSpec("response_reader", "Response Reader Agent", 3, "approver",
              "Reads each submitted form, validates the values and updates the student's record (marked self-reported).",
              trigger="event"),
    AgentSpec("recheck", "Eligibility Re-check Agent", 3, "approver",
              "Re-checks that student against the drive's criteria and consent; if they now qualify, has the Roster Agent "
              "add them (updated or supplementary proposal for the approver).",
              trigger="event"),
    AgentSpec("deadline", "Deadline Agent", 3, "approver",
              "Closes consent forms and personal forms at their closing time; nothing is accepted after that.",
              trigger="autopilot"),
    AgentSpec("reminder", "Reminder Agent", 3, "approver",
              "Sends reminders: unanswered consent forms, unfilled personal forms, and shortlisted students before the drive.",
              trigger="autopilot"),
    AgentSpec("upload", "Upload & Verification Agent", 3, "approver",
              "After human approval: uploads the exact approved roster, reads it back and verifies every ID, then makes "
              "the attendance sheet.",
              tools=("execute_approved_upload", "verify_uploaded_roster", "generate_attendance_sheet"),
              nodes=("execute_upload", "verify", "attendance")),
    AgentSpec("employer_update", "Employer Update Agent", 3, "approver",
              "Emails the company the verified list of shortlisted students from the placement mailbox, tells each "
              "shortlisted student, and tells consenting students who did not qualify why.",
              tools=("send_roster_notifications",), nodes=("notify",)),
)

BY_KEY = {a.key: a for a in AGENTS}
NODE_AGENT = {node: a for a in AGENTS for node in a.nodes}
ALWAYS_ALLOWED = {"record_audit_event"}

CURRENT_AGENT: ContextVar[AgentSpec | None] = ContextVar("current_agent", default=None)


def agent_for_node(node: str | None) -> AgentSpec | None:
    return NODE_AGENT.get(node or "")


def may_use(agent: AgentSpec | None, tool: str) -> bool:
    return agent is None or tool in agent.tools or tool in ALWAYS_ALLOWED


def describe() -> list[dict]:
    return [{"key": a.key, "name": a.name, "stage": a.stage, "stage_label": STAGES[a.stage], "owner": a.owner,
             "purpose": a.purpose, "tools": list(a.tools), "nodes": list(a.nodes), "trigger": a.trigger,
             "uses_ai": a.uses_ai, "actor": a.actor} for a in AGENTS]
