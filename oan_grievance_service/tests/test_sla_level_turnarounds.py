# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.tests.fixtures import a_grievance_type, a_service_category

SLA_DOCTYPE = "Grievance SLA Configuration"
L1 = "nodal_officer"
L2 = "senior_nodal_officer"


def _config(rows, category=None):
	"""An inactive configuration, so it never competes with the seeded one for the category."""
	return frappe.get_doc(
		{
			"doctype": SLA_DOCTYPE,
			"service_category": category or a_service_category(),
			"active": 0,
			"level_turnarounds": rows,
		}
	)


class TestSlaLevelTurnarounds(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")

	def test_a_category_and_a_type_can_each_have_a_turnaround_per_level(self):
		kind = a_grievance_type()
		doc = _config(
			[
				{"role_level": L1, "turnaround_days": 7},
				{"role_level": L2, "turnaround_days": 10},
				{"role_level": L1, "grievance_type": kind, "turnaround_days": 3},
				{"role_level": L2, "grievance_type": kind, "turnaround_days": 4},
			]
		).insert(ignore_permissions=True)
		saved = frappe.get_doc(SLA_DOCTYPE, doc.name)
		self.assertEqual(
			[(r.role_level, r.grievance_type, r.turnaround_days) for r in saved.level_turnarounds],
			[(L1, None, 7), (L2, None, 10), (L1, kind, 3), (L2, kind, 4)],
		)

	def test_a_turnaround_is_a_positive_number_of_days(self):
		for days in (0, -2):
			with self.assertRaises(frappe.ValidationError):
				_config([{"role_level": L1, "turnaround_days": days}]).insert(ignore_permissions=True)

	def test_a_level_has_one_turnaround_per_type(self):
		kind = a_grievance_type()
		for rows in (
			[{"role_level": L1, "turnaround_days": 5}, {"role_level": L1, "turnaround_days": 6}],
			[
				{"role_level": L2, "grievance_type": kind, "turnaround_days": 5},
				{"role_level": L2, "grievance_type": kind, "turnaround_days": 6},
			],
		):
			with self.assertRaises(frappe.ValidationError):
				_config(rows).insert(ignore_permissions=True)

	def test_only_an_active_level_can_be_used(self):
		with self.assertRaises(frappe.ValidationError):
			_config([{"role_level": "review_officer", "turnaround_days": 5}]).insert(ignore_permissions=True)

	def test_a_type_must_belong_to_the_configurations_category(self):
		elsewhere = frappe.get_doc(
			{
				"doctype": "Grievance Service Category",
				"category_name": "STG512 Elsewhere",
				"code": "Q5Z",
				"sort_order": 99,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		foreign = frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "STG512 Foreign",
				"service_category": elsewhere.name,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			_config([{"role_level": L1, "grievance_type": foreign.name, "turnaround_days": 5}]).insert(
				ignore_permissions=True
			)
		# The same row is fine on the category the type belongs to.
		_config(
			[{"role_level": L1, "grievance_type": foreign.name, "turnaround_days": 5}],
			category=elsewhere.name,
		).insert(ignore_permissions=True)
