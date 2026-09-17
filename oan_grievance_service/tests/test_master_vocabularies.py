# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Intake channels come from the master, not from a tuple in the code.

Every other master on Grievance was already a Link -- submitter type, area,
category, type. This one was a Select with the five channels written into the
schema, into a CHANNELS tuple, and into the install seed, so opening a channel
meant editing three files and cutting a release.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.grievance import active_channels, get_status_options
from oan_grievance_service.services import constants as C


class TestChannelsAreData(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_the_field_links_to_the_master(self):
		field = frappe.get_meta("Grievance").get_field("submission_channel")

		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Grievance Submission Type")

	def test_the_seeded_channels_are_offered(self):
		self.assertIn("Web Portal", active_channels())

	def test_a_retired_channel_stops_being_offered(self):
		"""is_active is what closes a channel, and it had no effect before."""
		frappe.db.set_value("Grievance Submission Type", "Web Portal", "is_active", 0)

		self.assertNotIn("Web Portal", active_channels())

	def test_a_new_channel_needs_no_code_change(self):
		"""The point of the move: an operator opens a channel from the desk."""
		frappe.get_doc(
			{
				"doctype": "Grievance Submission Type",
				"submission_type_name": "WhatsApp",
				"code": "WA",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)

		self.assertIn("WhatsApp", active_channels())


class TestStatusesAreData(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_the_field_links_to_the_master(self):
		field = frappe.get_meta("Grievance").get_field("status")

		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Grievance Status")
		self.assertEqual(field.default, C.SUBMITTED)

	def test_every_canonical_state_is_seeded(self):
		seeded = set(frappe.get_all("Grievance Status", pluck="name"))

		self.assertEqual(seeded, set(C.ALLOWED_TRANSITIONS))

	def test_the_options_endpoint_reads_the_master(self):
		"""It used to carry its own copy of the list."""
		returned = [row["status"] for row in get_status_options()]

		self.assertEqual(set(returned), set(C.ALLOWED_TRANSITIONS))
		self.assertEqual(returned[0], C.SUBMITTED, "seeded in lifecycle order")

	def test_open_and_terminal_flags_match_the_constants(self):
		for row in frappe.get_all("Grievance Status", fields=["name", "is_open", "is_terminal"]):
			self.assertEqual(bool(row.is_open), row.name in C.OPEN_STATUSES, row.name)
			self.assertEqual(bool(row.is_terminal), row.name in C.TERMINAL_STATUSES, row.name)


class TestTheStatusVocabularyIsNotOpen(FrappeTestCase):
	"""The master removes a duplicated list. It does not make the list editable.

	ALLOWED_TRANSITIONS is keyed on these names, so a state that is not in it is
	one a grievance can enter and then have no legal way out of. That is the
	difference between this master and Grievance Submission Type, where adding a
	row is exactly the point.
	"""

	def tearDown(self):
		frappe.db.rollback()

	def test_a_state_with_no_transition_rule_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Grievance Status", "status_name": "Under Appeal", "sort_order": 90}
			).insert(ignore_permissions=True)

	def test_the_refusal_says_where_the_states_are_defined(self):
		try:
			frappe.get_doc({"doctype": "Grievance Status", "status_name": "Escalated To Court"}).insert(
				ignore_permissions=True
			)
		except frappe.ValidationError:
			self.assertIn("constants.py", frappe.message_log[-1].get("message", ""))
		else:
			self.fail("an unknown state was accepted")
