# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestGrievanceDepartment(FrappeTestCase):
	def test_create_barebone_department(self):
		dept_name = f"Test Department {frappe.generate_hash(length=6)}"
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Department",
				"dept_name": dept_name,
				"short_name": "TD",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.dept_name, dept_name)
		self.assertEqual(doc.short_name, "TD")
		self.assertEqual(doc.name, dept_name)
