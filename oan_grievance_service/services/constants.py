# The catch-all category and type an intake falls back to when the submitter does
# not pick one. Seeded by setup/install.py; intake defaults to them by name.
FALLBACK_SERVICE_CATEGORY = "Other"
FALLBACK_GRIEVANCE_TYPE = "Other"
UNROUTED_DEPARTMENT = "Unrouted"
UNROUTED_OFFICER = "unrouted@grievance.local"


# Dedicated RQ queues, so a ClamAV backlog or a long SLA sweep cannot starve the
# others. Each must be declared under "workers" in common_site_config.json, or
# frappe.enqueue rejects it.
QUEUE_ROUTING = "grievance_routing"
QUEUE_SLA = "grievance_sla"
QUEUE_SCAN = "grievance_scan"
QUEUE_NOTIFICATIONS = "grievance_notifications"


# Grievance Workflow states, seeded by setup/install.py (WORKFLOW_STATES). Code that
# branches on where a case is compares against these, never a bare string.
STATE_SUBMITTED = "Submitted"
STATE_ASSIGNED = "Assigned"
STATE_IN_PROGRESS = "In Progress"
STATE_MORE_INFO_NEEDED = "More Info Needed"
STATE_RESOLVED = "Resolved"
STATE_CLOSED = "Closed"
STATE_REJECTED = "Rejected"


# Organisation (NGO / FPO / Cooperative) verification lifecycle. Held on Grievance
# Organization.verification_status; services/organization.py owns the legal moves.
ORG_STATUS_IN_REVIEW = "In Review"
ORG_STATUS_ACTIVE = "Active"
ORG_STATUS_SUSPENDED = "Suspended"
ORG_TYPES = ("NGO", "FPO", "Cooperative")


# The group the dashboards count as resolved: Resolved or Closed. The open and
# awaiting-action groups are not listed here; sla.open_states() and
# sla.awaiting_action_states() read them from the Grievance Workflow.
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

# SLA window, in days, for a category created without one.
DEFAULT_SLA_DAYS = 7
# State timers every new SLA Configuration starts with: (state, hours, on_expiry).
DEFAULT_STATE_TIMERS = (
	(STATE_RESOLVED, 7 * 24, "Auto Close"),
	(STATE_MORE_INFO_NEEDED, 7 * 24, "Auto Close"),
)
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
# Granted and revoked by services/officer.py with the top rung; RBAC records decide the scope.
ROLE_DESK_HEAD = "Grievance Desk Head"
# An assisted submitter (e.g. Development Agent, call-centre operator, front-desk intake staff)
# who files grievances on behalf of citizens, recorded as assisted_by_officer.
# Granted via Keycloak.
ROLE_ASSISTED_SUBMITTER = "Grievance Assisted Submitter"
# The OAN dashboards: a machine user that authenticates with its Frappe API key and
# secret and may read the public charts only.
ROLE_DASHBOARD_READER = "Grievance Dashboard Reader"
# Oversight: reads grievances, officer statistics and the administration data, and
# changes nothing. It holds read-only DocPerms and is never listed on a mutating
# endpoint's require_role.
ROLE_REVIEW_OFFICER = "Grievance Review Officer"

# Accounts the officer API never manages, whatever their other roles.
PRIVILEGED_ROLES = frozenset({"System Manager", "Administrator"})
# The role level of a Reviewer's rows on a department's desks. It is inactive, so it is not a
# rung of the escalation chain; it only says "this row is read-only oversight, not an assignee".
ROLE_LEVEL_REVIEW_OFFICER = "review_officer"
ROLE_LEVEL_DEPARTMENT_HEAD = "department_head"
# Role levels excluded from category assignment primary/secondary (L1/L2) desk seats:
# a department head is the escalation rung, and a reviewer is read-only oversight.
NON_ASSIGNEE_LEVELS = (ROLE_LEVEL_DEPARTMENT_HEAD, ROLE_LEVEL_REVIEW_OFFICER)

# Who may file on someone else's behalf: assisted submitters (Development Agents, call centre,
# walk-in intake) and administrators. Officers do not file. The named submitter stays the
# owner of the case and the filing user is recorded as the assisting officer.
ASSISTED_FILER_ROLES = frozenset({ROLE_ASSISTED_SUBMITTER, ROLE_ADMIN, "System Manager", "Administrator"})
STAFF_ROLES = frozenset({ROLE_OFFICER, ROLE_ADMIN, "System Manager", "Administrator"})
ADMIN_ROLES = [ROLE_ADMIN, "System Manager", "Administrator"]
# Admin endpoints that only read: the Review Officer joins the admins here and nowhere
# on a POST, PATCH or DELETE.
ADMIN_READ_ROLES = [*ADMIN_ROLES, ROLE_REVIEW_OFFICER]
# Who may read Reviewer accounts through the officer API. Admins only for now: whether a Review
# Officer may see them is a product decision still to be confirmed. Adding ROLE_REVIEW_OFFICER
# here is the whole change if the answer is yes.
REVIEWER_READ_ROLES = ADMIN_ROLES
