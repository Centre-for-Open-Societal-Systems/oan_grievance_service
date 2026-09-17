# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestAdministrativeAreaTicketCode(FrappeTestCase):
	"""The ticket character is checked on save, not when a farmer files.

	A bad code is otherwise invisible until the first submission in that region
	fails, and a duplicate never fails at all — it quietly gives two regions the
	same ticket prefix.
	"""

	def _region(self, name, ticket_code):
		existing = frappe.db.get_value("Grievance Administrative Area", {"area_name": name}, "name")
		if existing:
			doc = frappe.get_doc("Grievance Administrative Area", existing)
			doc.ticket_code = ticket_code
			return doc
		return frappe.get_doc(
			{
				"doctype": "Grievance Administrative Area",
				"area_name": name,
				"level_name": "Region",
				"code": name.replace(" ", "")[:8].upper(),
				"ticket_code": ticket_code,
				"is_group": 1,
			}
		)

	def test_valid_single_character_is_accepted_and_uppercased(self):
		doc = self._region("Code Valid Region", "k")
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.ticket_code, "K")

	def test_excluded_characters_are_refused(self):
		for char in "ILOU":
			with self.assertRaises(frappe.ValidationError, msg=f"{char} should be refused"):
				self._region(f"Code Excluded {char}", char).insert(ignore_permissions=True)

	def test_wrong_width_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._region("Code Too Long Region", "AB").insert(ignore_permissions=True)

	def test_duplicate_region_code_is_refused(self):
		self._region("Code First Region", "Q").insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			self._region("Code Second Region", "Q").insert(ignore_permissions=True)

	def test_only_regions_carry_a_ticket_code(self):
		parent = self._region("Code Parent Region", "W")
		parent.insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Code Child Woreda",
					"level_name": "Woreda",
					"code": "CCW",
					"ticket_code": "X",
					"parent_administrative_area": parent.name,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

	def test_a_region_without_a_code_still_saves(self):
		"""Most of the tree has none; only regions used for filing need one."""
		doc = self._region("Code Absent Region", None)
		doc.insert(ignore_permissions=True)
		self.assertFalse(doc.ticket_code)


class TestAdministrativeArea(FrappeTestCase):
	def test_tree_structure_and_lft_rgt(self):
		root_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Test Country"}, "name"
		)
		if not root_name:
			root = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Country",
					"level_name": "Country",
					"code": "TCT",
					"is_group": 1,
				}
			).insert(ignore_permissions=True)
		else:
			root = frappe.get_doc("Grievance Administrative Area", root_name)

		child1_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Test Region 1"}, "name"
		)
		if not child1_name:
			child1 = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Region 1",
					"level_name": "Region",
					"code": "TR1",
					"parent_administrative_area": root.name,
					"is_group": 1,
				}
			).insert(ignore_permissions=True)
		else:
			child1 = frappe.get_doc("Grievance Administrative Area", child1_name)

		child2_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Test Woreda 1"}, "name"
		)
		if not child2_name:
			child2 = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Woreda 1",
					"level_name": "Woreda",
					"code": "TW1",
					"parent_administrative_area": child1.name,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		else:
			child2 = frappe.get_doc("Grievance Administrative Area", child2_name)

		root.reload()
		child1.reload()
		child2.reload()

		# Containment checks
		self.assertLess(root.lft, child1.lft)
		self.assertGreater(root.rgt, child1.rgt)
		self.assertLess(child1.lft, child2.lft)
		self.assertGreater(child1.rgt, child2.rgt)

	def test_get_areas_endpoint(self):
		from oan_grievance_service.api.v1.administrative_area import get_areas

		# Default root view returns regions
		res = get_areas()
		self.assertIn("data", res)
		self.assertIn("areas", res["data"])
		self.assertGreaterEqual(res["data"]["count"], 1)

		# Cascading drilldown with parent
		res_child = get_areas(parent="region-ET14")
		self.assertIn("data", res_child)
		self.assertGreaterEqual(res_child["data"]["count"], 1)
		for area in res_child["data"]["areas"]:
			self.assertEqual(area["parent_administrative_area"], "region-ET14")

		# Ancestor breadcrumbs lookup
		res_ancestors = get_areas(ancestors_of="region-ET14")
		self.assertIn("data", res_ancestors)
		self.assertIn("breadcrumbs", res_ancestors["data"])
		self.assertGreaterEqual(len(res_ancestors["data"]["breadcrumbs"]), 1)
