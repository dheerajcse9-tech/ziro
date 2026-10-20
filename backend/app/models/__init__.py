from app.models.entities import *  # noqa: F401,F403
from app.models.entities import (  # noqa: F401
    AgentEvent, AgentRun, AppSetting, ApprovalRequest, AttendanceSheet, AuditEvent,
    ConsentRecord, Destination, Drive, DriveAssignment, DriveCriteria, EligibilityDecision,
    Employer, ExceptionItem, RosterImport, RosterProposal, SourceEvidence, Student, TestRun,
    UploadOperation, User, VerificationResult,
)
from app.mock_portal import models as _mock_portal_models  # noqa: F401  (register tables)
