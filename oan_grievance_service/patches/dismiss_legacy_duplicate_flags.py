# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Dismiss the duplicate flags the old detector left unreviewed.

The old detector flagged any two grievances of one submitter with the same type within seven
days, scored them 1.0 and left them with no review. Read as pending reviews under the
similarity rule they would reach officers with no match level and inflate the pending count,
although they came from a much looser rule. Confirmed flags are kept as they are.
"""

import frappe
from frappe.utils import now_datetime

LEGACY_METHOD = "Identity + Type + Time Proximity"
NOTE = (
	"Flagged by the earlier same-type detector and never reviewed; dismissed when similarity detection began."
)


def execute():
	duplicate = frappe.qb.DocType("Grievance Duplicate")
	(
		frappe.qb.update(duplicate)
		.set(duplicate.is_dismissed, 1)
		.set(duplicate.decided_at, now_datetime())
		.set(duplicate.decision_note, NOTE)
		.where(duplicate.detection_method == LEGACY_METHOD)
		.where(duplicate.is_confirmed == 0)
		.where(duplicate.is_dismissed == 0)
	).run()
