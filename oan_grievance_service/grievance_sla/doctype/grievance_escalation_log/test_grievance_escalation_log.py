# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.tests.fixtures import a_grievance


class TestGrievanceEscalationLog(FrappeTestCase):
	"""FSD 3.7 / 4.3: an escalation is raised, then stood down once a response lands.

	`resolved_at` is the column that records the stand-down. `sla.clear_escalation`
	has always written to it, but the field was missing from the doctype, so the
	write would have failed the first time a genuinely escalated case was answered.
	Nothing caught it because `clear_escalation` returns early on a case that was
	never escalated, which is every case the earlier tests exercised.
	"""

	def tearDown(self):
		frappe.db.rollback()

	def test_resolved_at_field_exists(self):
		self.assertTrue(
			frappe.get_meta("Grievance Escalation Log").has_field("resolved_at"),
			"sla.clear_escalation writes resolved_at; the doctype must carry it",
		)

	def test_an_open_escalation_has_no_resolved_at(self):
		log = frappe.get_doc(
			{
				"doctype": "Grievance Escalation Log",
				"grievance": a_grievance().name,
				"escalation_level": "L1",
				"triggered_at": frappe.utils.now_datetime(),
				"triggered_by": "System",
				"reason": "SLA breached",
			}
		).insert(ignore_permissions=True)
		self.assertIsNone(log.resolved_at)

	def test_standing_down_an_escalation_stamps_resolved_at(self):
		log = frappe.get_doc(
			{
				"doctype": "Grievance Escalation Log",
				"grievance": a_grievance().name,
				"escalation_level": "L1",
				"triggered_at": frappe.utils.now_datetime(),
				"triggered_by": "System",
			}
		).insert(ignore_permissions=True)

		# The same write sla.clear_escalation performs.
		frappe.db.set_value(
			"Grievance Escalation Log",
			{"name": log.name, "resolved_at": ["is", "not set"]},
			"resolved_at",
			frappe.utils.now_datetime(),
			update_modified=False,
		)
		self.assertTrue(frappe.db.get_value("Grievance Escalation Log", log.name, "resolved_at"))
