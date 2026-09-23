# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestGrievanceResponseType(FrappeTestCase):
	def test_response_types_exist(self):
		for name in ("Resolved", "Partially Resolved", "Referred to another dept", "Requires further info"):
			self.assertTrue(
				frappe.db.exists("Grievance Response Type", name),
				f"Expected Grievance Response Type '{name}' to exist",
			)
