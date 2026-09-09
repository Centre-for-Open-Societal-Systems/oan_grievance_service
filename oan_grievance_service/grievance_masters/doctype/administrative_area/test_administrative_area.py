# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestAdministrativeArea(FrappeTestCase):
	def test_tree_structure_and_lft_rgt(self):
		if not frappe.db.exists("Administrative Area", "Test Country"):
			root = frappe.get_doc(
				{
					"doctype": "Administrative Area",
					"area_name": "Test Country",
					"area_type": "Country",
					"code": "TCT",
					"is_group": 1,
				}
			).insert(ignore_permissions=True)
		else:
			root = frappe.get_doc("Administrative Area", "Test Country")

		if not frappe.db.exists("Administrative Area", "Test Region 1"):
			child1 = frappe.get_doc(
				{
					"doctype": "Administrative Area",
					"area_name": "Test Region 1",
					"area_type": "Region",
					"code": "TR1",
					"parent_administrative_area": root.name,
					"is_group": 1,
				}
			).insert(ignore_permissions=True)
		else:
			child1 = frappe.get_doc("Administrative Area", "Test Region 1")

		if not frappe.db.exists("Administrative Area", "Test Woreda 1"):
			child2 = frappe.get_doc(
				{
					"doctype": "Administrative Area",
					"area_name": "Test Woreda 1",
					"area_type": "Woreda",
					"code": "TW1",
					"parent_administrative_area": child1.name,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		else:
			child2 = frappe.get_doc("Administrative Area", "Test Woreda 1")

		root.reload()
		child1.reload()
		child2.reload()

		# Containment checks
		self.assertLess(root.lft, child1.lft)
		self.assertGreater(root.rgt, child1.rgt)
		self.assertLess(child1.lft, child2.lft)
		self.assertGreater(child1.rgt, child2.rgt)
