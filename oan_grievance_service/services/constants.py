# The catch-all category and type an intake falls back to when the submitter does
# not pick one. Seeded by setup/install.py; draft intake defaults to them by name.
FALLBACK_SERVICE_CATEGORY = "Other"
FALLBACK_GRIEVANCE_TYPE = "Other"


# Grievance Workflow states, seeded by setup/install.py (WORKFLOW_STATES). Code that
# branches on where a case is compares against these, never a bare string.
#
# Draft is the only state at docstatus 0, so "is this still a draft?" is asked of
# docstatus, not of the state name.
STATE_DRAFT = "Draft"
STATE_SUBMITTED = "Submitted"
STATE_ASSIGNED = "Assigned"
STATE_IN_PROGRESS = "In Progress"
STATE_MORE_INFO_NEEDED = "More Info Needed"
STATE_RESOLVED = "Resolved"
STATE_CLOSED = "Closed"
STATE_REJECTED = "Rejected"


# The groups the dashboards count by. Awaiting Action is every state in which an
# officer owes the next move. More Info Needed waits on the submitter, so it is open
# but not awaiting. Resolved on the dashboards means Resolved or Closed.
AWAITING_ACTION_STATES = (STATE_SUBMITTED, STATE_ASSIGNED, STATE_IN_PROGRESS)
OPEN_STATES = (*AWAITING_ACTION_STATES, STATE_MORE_INFO_NEEDED)
RESOLVED_STATES = (STATE_RESOLVED, STATE_CLOSED)


# Notification event codes. Each is the "method" on one core Notification record per
# channel, seeded by setup/install.py and editable from the desk thereafter.
EVENT_SUBMISSION_RECEIVED = "submission_received"
EVENT_DUPLICATE_DETECTED = "duplicate_detected"
EVENT_ASSIGNED_AUTO = "grievance_assigned_auto"
EVENT_ASSIGNED_MANUAL = "grievance_assigned_manual"
EVENT_STATUS_IN_PROGRESS = "status_in_progress"
EVENT_MORE_INFO_REQUESTED = "more_info_requested"
EVENT_SUBMITTER_RESPONDED = "submitter_responds_to_info"
EVENT_RESPONSE_SENT = "structured_response_sent"
EVENT_CONFIRMATION_WINDOW = "confirmation_window_open"
EVENT_CONFIRMED = "grievance_confirmed"
EVENT_REOPENED = "grievance_reopened"
EVENT_AUTO_CLOSED = "auto_closed_no_response"
EVENT_CLOSED = "grievance_closed"
EVENT_STATUS_REJECTED = "status_rejected"
EVENT_SLA_REMINDER_50 = "sla_reminder_50"
EVENT_SLA_REMINDER_80 = "sla_reminder_80"
EVENT_SLA_AT_RISK = "sla_at_risk_report"
EVENT_SLA_BREACH = "sla_breached"
EVENT_MANUAL_ESCALATION = "manual_escalation"
EVENT_REASSIGNMENT_REQUESTED = "reassignment_requested"
EVENT_ANONYMITY_DISCLOSURE_REQUESTED = "anonymity_disclosure_requested"

# FSD Appendix C event ids (EC-001 to EC-019), keyed by event code. EC-017 (SLA breach,
# L2) has no code of its own; the escalation ladder sends a single EVENT_SLA_BREACH for
# every level, which is EC-016's. EVENT_CLOSED, EVENT_STATUS_REJECTED and
# EVENT_ANONYMITY_DISCLOSURE_REQUESTED are not in Appendix C.
EVENT_EC_ID = {
	EVENT_SUBMISSION_RECEIVED: "EC-001",
	EVENT_DUPLICATE_DETECTED: "EC-002",
	EVENT_ASSIGNED_AUTO: "EC-003",
	EVENT_ASSIGNED_MANUAL: "EC-004",
	EVENT_STATUS_IN_PROGRESS: "EC-005",
	EVENT_MORE_INFO_REQUESTED: "EC-006",
	EVENT_SUBMITTER_RESPONDED: "EC-007",
	EVENT_RESPONSE_SENT: "EC-008",
	EVENT_CONFIRMATION_WINDOW: "EC-009",
	EVENT_CONFIRMED: "EC-010",
	EVENT_REOPENED: "EC-011",
	EVENT_AUTO_CLOSED: "EC-012",
	EVENT_SLA_REMINDER_50: "EC-013",
	EVENT_SLA_REMINDER_80: "EC-014",
	EVENT_SLA_AT_RISK: "EC-015",
	EVENT_SLA_BREACH: "EC-016",
	EVENT_MANUAL_ESCALATION: "EC-018",
	EVENT_REASSIGNMENT_REQUESTED: "EC-019",
}

# Default submitter confirmation window.
DEFAULT_CONFIRMATION_DAYS = 7
# Reminder thresholds as a percentage of the SLA window.
SLA_REMINDER_THRESHOLDS = (50, 80)
# Global default ceiling on a single deferral.
DEFAULT_MAX_DEFERRAL_DAYS = 30
# Global default share of the SLA window consumed before a case escalates: at the deadline.
DEFAULT_ESCALATION_THRESHOLD = 100


# Canonical Frappe user roles used for API authentication and authorization.
ROLE_SUBMITTER = "Grievance Submitter"
ROLE_OFFICER = "Grievance Officer"
ROLE_ADMIN = "Grievance Admin"
# The OAN dashboards: a machine user that authenticates with its Frappe API key and
# secret and may read the public charts only.
ROLE_DASHBOARD_READER = "Grievance Dashboard Reader"
# Oversight: reads grievances, officer statistics and the administration data, and
# changes nothing. It holds read-only DocPerms and is never listed on a mutating
# endpoint's require_role.
ROLE_REVIEW_OFFICER = "Grievance Review Officer"

STAFF_ROLES = frozenset({ROLE_OFFICER, ROLE_ADMIN, "System Manager", "Administrator"})
ADMIN_ROLES = [ROLE_ADMIN, "System Manager", "Administrator"]
# Admin endpoints that only read: the Review Officer joins the admins here and nowhere
# on a POST, PATCH or DELETE.
ADMIN_READ_ROLES = [*ADMIN_ROLES, ROLE_REVIEW_OFFICER]
ALLOWED_GRIEVANCE_ROLES = [
	ROLE_SUBMITTER,
	ROLE_OFFICER,
	ROLE_ADMIN,
	"System Manager",
	"Administrator",
]
ALLOWED_GRIEVANCE_READ_ROLES = [*ALLOWED_GRIEVANCE_ROLES, ROLE_REVIEW_OFFICER]
