"""Duplicate grievances: flagging a likely repeat, and the officer's ruling on it.

The system only flags. A new grievance is compared with the same submitter's earlier
grievances; a pair that scores at or above THRESHOLD is recorded as a Grievance Duplicate
row awaiting review. Nothing about the ticket changes until an officer confirms it, which
moves it to the terminal Duplicate state and points it at the main ticket. An officer who
rules it is not a duplicate leaves the ticket exactly as it was.

The comparison is the "Option A" score of GRIEVANCE_SIMILARITY_ALGORITHMS.md, phase 1:

    score = W_DESCRIPTION * description + W_CATEGORY * same category + W_TYPE * same type

The description is a cosine over character n-grams of the normalised text. Only one
submitter's tickets are ever compared (a handful, not millions), so there is no index and
no IDF weighting: the pool is too small for inverse document frequency to mean anything.
Category and type are weights, not filters, because submitters sometimes pick the wrong
one. The weights and thresholds are starting points to be replaced by measurements on
labelled pairs; they live here, in one place, until then.
"""

import math
import re
import unicodedata
from collections import Counter

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle
from oan_grievance_service.services import ticket_number as tn

ACTION_CONFIRM = "Confirm Duplicate"
METHOD_SIMILARITY = "Same Submitter Similarity"
METHOD_MANUAL = "Manual"

# An earlier ticket is a candidate while it is still live or was resolved. Closed, Rejected
# and Duplicate tickets are finished business. Submitted, Assigned and Reopened are included
# on purpose: a double submit is compared with a ticket that has barely been routed.
CANDIDATE_STATES = (
	C.STATE_SUBMITTED,
	C.STATE_ASSIGNED,
	C.STATE_REOPENED,
	C.STATE_IN_PROGRESS,
	C.STATE_MORE_INFO_NEEDED,
	C.STATE_RESOLVED,
)

W_DESCRIPTION = 0.70
W_CATEGORY = 0.10
W_TYPE = 0.20
# Category and type together must stay below the threshold, so unrelated text can never be
# flagged on matching labels alone.
THRESHOLD = 0.65
HIGH_THRESHOLD = 0.85

NGRAM_SIZES = (3, 4, 5)
# One submitter's recent tickets are the pool; the cap only guards a runaway account.
POOL_LIMIT = 200
# A ticket is flagged against its best few matches, not every similar ticket it ever had.
MAX_FLAGS = 3

# Before filing, a farmer on an interactive channel is asked whether this is a new grievance
# when a similar one of theirs is this recent. IVR and SMS are one way: nobody could answer,
# so they are only flagged afterwards, for an officer.
PROMPT_WINDOW_HOURS = 24
INTERACTIVE_CHANNELS = ("Web Portal",)


def _homophone_folds():
	"""Ge'ez letters that sound alike and are used interchangeably, folded onto one form.

	Applied to the copy of the text used for matching only: it can merge words that differ
	in meaning (ድህነት "poverty", ድኅነት "salvation"), so the stored description is never
	changed.
	"""
	folds = {}
	for offset in range(7):
		folds[chr(0x1210 + offset)] = chr(0x1200 + offset)  # HHA row -> HA row
		folds[chr(0x1280 + offset)] = chr(0x1200 + offset)  # XA row -> HA row
		folds[chr(0x12D0 + offset)] = chr(0x12A0 + offset)  # pharyngeal A row -> glottal A row
		folds[chr(0x1338 + offset)] = chr(0x1340 + offset)  # TSA row -> TZA row
	for offset in range(8):
		folds[chr(0x1220 + offset)] = chr(0x1230 + offset)  # SZA row -> SA row
	return str.maketrans(folds)


_FOLD = _homophone_folds()
_NON_WORD = re.compile(r"\W+")


def normalize(text):
	"""Lower-cased, punctuation-free, single-spaced text with Ge'ez homophones folded.

	Numbers, place names and cooperative names stay: they are strong evidence.
	"""
	text = unicodedata.normalize("NFC", text or "").casefold().translate(_FOLD)
	return " ".join(_NON_WORD.sub(" ", text).split())


def _vector(text):
	"""Character n-gram counts, taken within words so a phrase does not match across them."""
	counts = Counter()
	for word in text.split():
		padded = f" {word} "
		for size in NGRAM_SIZES:
			for start in range(len(padded) - size + 1):
				counts[padded[start : start + size]] += 1
	return counts


def description_similarity(first, second):
	"""Cosine of the two descriptions' character n-grams, 0 to 1."""
	one, two = _vector(normalize(first)), _vector(normalize(second))
	if not one or not two:
		return 0.0
	dot = sum(count * two[gram] for gram, count in one.items() if gram in two)
	norm = math.sqrt(sum(v * v for v in one.values())) * math.sqrt(sum(v * v for v in two.values()))
	return dot / norm if norm else 0.0


def score(grievance, other):
	"""How alike two grievances are, 0 to 1. A ranking score, not a probability."""
	return round(
		W_DESCRIPTION * description_similarity(grievance.get("description"), other.get("description"))
		+ W_CATEGORY * (grievance.get("service_category") == other.get("service_category"))
		+ W_TYPE * (grievance.get("grievance_type") == other.get("grievance_type")),
		4,
	)


def match_level(value):
	return "High" if value >= HIGH_THRESHOLD else "Medium"


def candidates_for(grievance, since=None):
	"""The submitter's earlier grievances a new one is compared with, newest first.

	Earlier only, so the older ticket is always the main one and two tickets can never flag
	each other. A grievance with no submitter (anonymous) has no history to compare. `since`
	limits the pool to tickets filed after it. Works on a grievance not yet inserted, which
	has no name or creation time to exclude.
	"""
	if not grievance.submitter:
		return []
	filters = [
		["submitter", "=", grievance.submitter],
		["status", "in", CANDIDATE_STATES],
	]
	if grievance.get("name"):
		filters.append(["name", "!=", grievance.name])
	if grievance.get("creation"):
		filters.append(["creation", "<", grievance.creation])
	if since:
		filters.append(["creation", ">=", since])
	return frappe.get_all(
		"Grievance",
		filters=filters,
		fields=["name", "status", "description", "service_category", "grievance_type", "creation"],
		order_by="creation desc, name desc",
		limit_page_length=POOL_LIMIT,
	)


def matches_for(grievance, since=None):
	"""[(score, earlier ticket)] at or above the threshold, best first; the older wins a tie."""
	matches = []
	for other in candidates_for(grievance, since):
		value = score(grievance, other)
		if value >= THRESHOLD:
			matches.append((value, other))
	matches.sort(key=lambda match: (-match[0], match[1].creation, match[1].name))
	return matches[:MAX_FLAGS]


def likely_repeats(grievance):
	"""The submitter's recent similar tickets, to ask before a new one is filed.

	`grievance` is the unsaved filing. Only tickets from the last PROMPT_WINDOW_HOURS count: a
	farmer filing the same thing again within a day, perhaps through another channel, is
	probably repeating themselves. Older matches are left to the officer's flag.
	"""
	since = add_to_date(now_datetime(), hours=-PROMPT_WINDOW_HOURS)
	return [{"match_level": match_level(value), **other} for value, other in matches_for(grievance, since)]


def flag_possible_duplicates(grievance):
	"""Record the earlier tickets this one likely repeats. Returns the new review rows.

	Best match first; the older ticket wins a tie. Safe to run twice: a pair already
	recorded is not recorded again.
	"""
	rows = []
	for value, other in matches_for(grievance):
		if frappe.db.exists("Grievance Duplicate", {"grievance": grievance.name, "duplicate_of": other.name}):
			continue
		rows.append(
			frappe.get_doc(
				{
					"doctype": "Grievance Duplicate",
					"grievance": grievance.name,
					"duplicate_of": other.name,
					"detected_at": now_datetime(),
					"detection_method": METHOD_SIMILARITY,
					"similarity_score": value,
					"match_level": match_level(value),
				}
			).insert(ignore_permissions=True)
		)
	return rows


def pending_reviews(grievance_name):
	"""The rows awaiting an officer's ruling on a grievance, best match first."""
	return frappe.get_all(
		"Grievance Duplicate",
		filters={"grievance": grievance_name, "is_confirmed": 0, "is_dismissed": 0},
		fields=["name", "duplicate_of", "similarity_score", "match_level", "detected_at"],
		order_by="similarity_score desc, detected_at asc, name asc",
	)


def is_read_only(grievance):
	return grievance.workflow_state == C.STATE_DUPLICATE


def assert_not_duplicate(grievance):
	"""A confirmed duplicate takes no comments, deferrals or reassignment: the main ticket does."""
	if is_read_only(grievance):
		frappe.throw(
			_("This ticket is a duplicate of {0}. All further handling happens on the main ticket.").format(
				tn.display(frappe.db.get_value("Grievance", grievance.duplicate_of, "ticket_number"))
				if grievance.duplicate_of
				else _("another ticket")
			),
			frappe.ValidationError,
			title=_("Duplicate Ticket"),
		)


def validate_main_ticket(grievance, main_name):
	if main_name == grievance.name:
		frappe.throw(_("A ticket cannot be a duplicate of itself."), title=_("Invalid Main Ticket"))
	main_state = frappe.db.get_value("Grievance", main_name, "workflow_state")
	if main_state == C.STATE_DUPLICATE:
		frappe.throw(
			_("The main ticket is itself a duplicate. Choose the ticket it points to."),
			title=_("Invalid Main Ticket"),
		)


def confirm(grievance, main_name=None, note=None):
	"""An officer rules this ticket is a duplicate of `main_name`.

	The main ticket defaults to the best pending match. Moves the case to Duplicate through
	the workflow, which records the status history, stops the SLA clock and, with the
	terminal state, makes the ticket read-only. Returns the Grievance Status History row and
	the timeline entry.
	"""
	pending = pending_reviews(grievance.name)
	main_name = main_name or (pending[0].duplicate_of if pending else None)
	if not main_name:
		frappe.throw(
			_("Name the main ticket this ticket duplicates."),
			frappe.ValidationError,
			title=_("Main Ticket Required"),
		)
	validate_main_ticket(grievance, main_name)
	if ACTION_CONFIRM not in lifecycle.actions_available(grievance):
		frappe.throw(
			_("A grievance in status '{0}' cannot be confirmed as a duplicate.").format(
				_(grievance.workflow_state)
			),
			frappe.ValidationError,
			title=_("Action Not Permitted"),
		)

	# The workflow engine reloads the case from the database before it moves it, so the
	# link is saved first and the move validates against it.
	grievance.duplicate_of = main_name
	grievance.save(ignore_permissions=True)
	history = lifecycle.transition(grievance, ACTION_CONFIRM, reason=note)

	now, user = now_datetime(), frappe.session.user
	review = next((row for row in pending if row.duplicate_of == main_name), None)
	if review:
		row = frappe.get_doc("Grievance Duplicate", review.name)
	else:
		row = frappe.get_doc(
			{
				"doctype": "Grievance Duplicate",
				"grievance": grievance.name,
				"duplicate_of": main_name,
				"detected_at": now,
				"detection_method": METHOD_MANUAL,
			}
		)
	row.update({"is_confirmed": 1, "decided_by": user, "decided_at": now, "decision_note": note})
	row.save(ignore_permissions=True)

	main_ticket = frappe.db.get_value("Grievance", main_name, "ticket_number")
	entry = GrievanceTimeline.record(
		grievance=grievance.name,
		entry_type="status_change",
		is_internal=False,
		body=_("Marked as duplicate of {0}. All further handling will happen on the main ticket.").format(
			tn.display(main_ticket)
		),
		author_user=user,
		author_type="officer",
		ref_doctype="Grievance Status History",
		ref_docname=history.name if history else None,
	)
	return history, entry


def dismiss(grievance, note=None):
	"""An officer rules the flag is wrong. The ticket carries on as a normal one."""
	pending = pending_reviews(grievance.name)
	if not pending:
		frappe.throw(
			_("This ticket is not flagged as a possible duplicate."),
			frappe.ValidationError,
			title=_("Nothing To Review"),
		)
	now, user = now_datetime(), frappe.session.user
	for review in pending:
		row = frappe.get_doc("Grievance Duplicate", review.name)
		row.update({"is_dismissed": 1, "decided_by": user, "decided_at": now, "decision_note": note})
		row.save(ignore_permissions=True)
	return GrievanceTimeline.record(
		grievance=grievance.name,
		entry_type="note",
		is_internal=True,
		body=_("Reviewed as a possible duplicate of {0} and ruled not a duplicate.").format(
			", ".join(
				tn.display(frappe.db.get_value("Grievance", row.duplicate_of, "ticket_number"))
				for row in pending
			)
		),
		author_user=user,
		author_type="officer",
	)
