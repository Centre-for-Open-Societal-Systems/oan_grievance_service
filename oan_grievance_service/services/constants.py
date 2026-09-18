"""Canonical vocabulary from the FSD. Every module imports status names from here.

Hardcoding a status string in more than one place is how a lifecycle drifts, so
FSD 3.4's canonical list lives here once.
"""

# FSD 3.4 canonical lifecycle. "More Info Needed" is absent from the 3.4 table but
# required by 3.5, Appendix C and Appendix D-2, so it is part of the canonical set.
#
# Draft is the state a grievance is born in: docstatus 0, the moment between insert
# and the Submit action. Every other state is a submitted document, and Rejected is a
# cancelled one, which is what makes the two terminal states read-only on every path
# rather than only in the desk (see setup.install.WORKFLOW_STATES).
DRAFT = "Draft"
SUBMITTED = "Submitted"
ASSIGNED = "Assigned"
IN_PROGRESS = "In Progress"
MORE_INFO_NEEDED = "More Info Needed"
PENDING_SUBMITTER = "Pending Submitter"
RESOLVED = "Resolved"
CLOSED = "Closed"
REJECTED = "Rejected"

OPEN_STATUSES = (
	SUBMITTED,
	ASSIGNED,
	IN_PROGRESS,
	MORE_INFO_NEEDED,
	PENDING_SUBMITTER,
)

TERMINAL_STATUSES = (CLOSED, REJECTED)

# The states in which the SLA clock is paused because the case is waiting on the
# submitter, not on the department. Resolves the EC-006 / DeferSLAPopup conflict recorded
# in database-schema.md: the answer is configuration, and this is the seed value. Override
# per site with the `grievance_sla_paused_statuses` config key.
SLA_PAUSED_STATUSES = frozenset({MORE_INFO_NEEDED, PENDING_SUBMITTER})

# FSD 3.4: the legal moves live in the Grievance Workflow record, seeded by
# setup/install.py from WORKFLOW_TRANSITIONS there and enforced by Frappe's workflow
# engine. Code asks for a move by action name; it never decides for itself whether
# the move is legal. These are the Workflow Action Master names.
ACTION_SUBMIT = "Submit"
ACTION_ASSIGN = "Assign"
ACTION_REJECT = "Reject"
ACTION_START_WORK = "Start Work"
ACTION_REQUEST_MORE_INFO = "Request More Info"
ACTION_SUBMIT_RESPONSE = "Submit Response"
ACTION_REFER_ONWARD = "Refer Onward"
ACTION_SUBMITTER_REPLY = "Submitter Reply"
ACTION_CONFIRM_RESOLUTION = "Confirm Resolution"
ACTION_REOPEN = "Reopen"
ACTION_AUTO_CLOSE = "Auto Close"
ACTION_CLOSE_CASE = "Close Case"

# FSD 3.4 / 3.6: the moves a person must justify. Enforced once, in
# GrievanceStatusHistory.validate, so no path -- desk, API or scheduled job -- can
# make them silently. A reopen is only possible inside the confirmation window
# (FSD 3.6); Closed and Rejected have no way out.
REASON_REQUIRED_MOVES = frozenset(
	{
		(SUBMITTED, REJECTED),
		(ASSIGNED, REJECTED),
		(IN_PROGRESS, REJECTED),
		(MORE_INFO_NEEDED, REJECTED),
		(PENDING_SUBMITTER, IN_PROGRESS),
	}
)

# FSD 3.11.3: display groups mapped to canonical statuses. The mapping is required
# to be configurable and documented; this is the documented default.
DISPLAY_GROUPS = {
	"All": None,
	"Pending": (SUBMITTED, ASSIGNED),
	"In Progress": (IN_PROGRESS, MORE_INFO_NEEDED),
	"Under Review": (PENDING_SUBMITTER,),
	"Resolved": (RESOLVED, CLOSED),
	"Rejected": (REJECTED,),
}

# FSD Appendix D-2: the response outcome drives the next move, and with it what the
# SLA clock does. `running` keeps counting, `paused` stops it while the case waits on
# the submitter, `stopped` is for terminal outcomes (none of the four is one).
RESPONSE_OUTCOME_ACTION = {
	"Resolved": ACTION_SUBMIT_RESPONSE,
	"Partially Resolved": ACTION_SUBMIT_RESPONSE,
	"Referred to another dept": ACTION_REFER_ONWARD,
	"Requires further info": ACTION_REQUEST_MORE_INFO,
}
RESPONSE_OUTCOME_SLA_BEHAVIOUR = {
	"Resolved": "paused",
	"Partially Resolved": "paused",
	"Referred to another dept": "running",
	"Requires further info": "paused",
}

# FSD Appendix C event codes. Each is the "method" on one core Notification record per
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
EVENT_SLA_REMINDER_50 = "sla_reminder_50"
EVENT_SLA_REMINDER_80 = "sla_reminder_80"
EVENT_SLA_AT_RISK = "sla_at_risk_report"
EVENT_SLA_BREACH_L1 = "sla_breached_l1"
EVENT_SLA_BREACH_L2 = "sla_breached_l2"
EVENT_MANUAL_ESCALATION = "manual_escalation"
EVENT_REASSIGNMENT_REQUESTED = "reassignment_requested"

# FSD 3.6: default submitter confirmation window.
DEFAULT_CONFIRMATION_DAYS = 7
# FSD 3.7: reminder thresholds as a percentage of the SLA window.
SLA_REMINDER_THRESHOLDS = (50, 80)
# FSD 3.11.7: global default ceiling on a single deferral.
DEFAULT_MAX_DEFERRAL_DAYS = 30
