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

from oan_grievance_service.api.v1.grievance import active_channels


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
