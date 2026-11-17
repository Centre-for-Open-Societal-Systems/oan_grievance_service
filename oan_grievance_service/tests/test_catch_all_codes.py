# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import importlib.util
import os
from contextlib import contextmanager
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.patches import seed_catch_all_sys_codes
from oan_grievance_service.services import constants as C
from oan_grievance_service.setup.install import seed_grievance_types, seed_unrouted_desk
from oan_grievance_service.tests.fixtures import a_department


def _loader():
	path = os.path.join(frappe.get_app_path("oan_grievance_service"), "..", "ci", "seed", "load.py")
	spec = importlib.util.spec_from_file_location("seed_load", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


@contextmanager
def _tickets_issued(issued: bool):
	"""Pretend the catch-all category has (or has not) issued a ticket number."""
	real = frappe.db.exists

	def exists(doctype, *args, **kwargs):
		if doctype == "Grievance":
			return "GRV-X" if issued else None
		return real(doctype, *args, **kwargs)

	with mock.patch.object(frappe.db, "exists", side_effect=exists):
		yield


class TestCatchAllCodes(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		seed_unrouted_desk()
		self.category = C.FALLBACK_SERVICE_CATEGORY
		self.type_name = frappe.db.get_value(
			"Grievance Type",
			{"type_name": C.FALLBACK_GRIEVANCE_TYPE, "service_category": self.category},
			"name",
		)
		self.assertTrue(self.type_name, "the catch-all type is seeded on install")
		# An older site: the old code, no type code, no owning department.
		frappe.db.set_value("Grievance Service Category", self.category, {"code": "006", "sort_order": 6})
		frappe.db.set_value("Grievance Type", self.type_name, {"type_code": None, "department": None})

	def _category(self):
		return frappe.db.get_value(
			"Grievance Service Category", self.category, ["code", "sort_order"], as_dict=True
		)

	def _type(self):
		return frappe.db.get_value(
			"Grievance Type", self.type_name, ["type_code", "department"], as_dict=True
		)

	def test_patch_moves_an_unused_catch_all_to_sys(self):
		with _tickets_issued(False):
			seed_catch_all_sys_codes.execute()
		self.assertEqual(self._category().code, C.FALLBACK_CATEGORY_CODE)
		self.assertEqual(self._category().sort_order, 20)
		kind = self._type()
		self.assertEqual((kind.type_code, kind.department), (C.FALLBACK_TYPE_CODE, C.UNROUTED_DEPARTMENT))

	def test_patch_keeps_a_code_that_tickets_already_carry(self):
		with _tickets_issued(True):
			seed_catch_all_sys_codes.execute()
		self.assertEqual(self._category().code, "006")
		# The type does not take part in the ticket number, so it is still brought up to date.
		kind = self._type()
		self.assertEqual((kind.type_code, kind.department), (C.FALLBACK_TYPE_CODE, C.UNROUTED_DEPARTMENT))

	def test_patch_runs_twice_and_keeps_admin_choices(self):
		own = a_department()
		frappe.db.set_value("Grievance Type", self.type_name, "department", own)
		with _tickets_issued(False):
			seed_catch_all_sys_codes.execute()
			seed_catch_all_sys_codes.execute()
		kind = self._type()
		self.assertEqual(kind.department, own)
		self.assertEqual(kind.type_code, C.FALLBACK_TYPE_CODE)

	def test_loader_does_not_add_a_second_other_type(self):
		load = _loader()
		with _tickets_issued(False):
			seed_catch_all_sys_codes.execute()
		row = {
			"name": "SYS",
			"type_code": "SYS",
			"type_name": C.FALLBACK_GRIEVANCE_TYPE,
			"service_category": self.category,
			"is_active": 1,
			"department": C.UNROUTED_DEPARTMENT,
		}
		before = frappe.db.count("Grievance Type", {"type_name": "Other", "service_category": self.category})
		with mock.patch.object(frappe.db, "commit"):
			load.load_doctype("Grievance Type", [dict(row)], None)
		after = frappe.db.count("Grievance Type", {"type_name": "Other", "service_category": self.category})
		self.assertEqual(before, after)

	def test_a_later_seed_run_gives_the_catch_all_its_desk(self):
		# Made before the desk existed: nothing but a later run can repair it.
		self.assertIsNone(self._type().department)
		seed_grievance_types()
		kind = self._type()
		self.assertEqual((kind.type_code, kind.department), (C.FALLBACK_TYPE_CODE, C.UNROUTED_DEPARTMENT))
		own = a_department()
		frappe.db.set_value("Grievance Type", self.type_name, "department", own)
		seed_grievance_types()
		self.assertEqual(self._type().department, own, "an administrator's desk stays")

	def test_rows_that_link_to_a_skipped_row_point_at_the_real_record(self):
		load = _loader()
		with mock.patch.object(frappe.db, "commit"):
			load.load_doctype(
				"Grievance Type",
				[
					{
						"name": "SYS",
						"type_code": "SYS",
						"type_name": C.FALLBACK_GRIEVANCE_TYPE,
						"service_category": self.category,
						"is_active": 1,
					}
				],
				None,
			)
			load.load_doctype(
				"Grievance RBAC Assignment",
				[
					{
						"name": "GR-RBAC-STG512",
						"category_scope": self.category,
						"department_scope": C.UNROUTED_DEPARTMENT,
						"grievance_type_scope": "SYS",
						"active": 1,
						"effective_from": "2026-11-17",
						"officers": [],
					}
				],
				None,
			)
		scope = frappe.db.get_value("Grievance RBAC Assignment", "GR-RBAC-STG512", "grievance_type_scope")
		self.assertEqual(scope, self.type_name)

	def test_loader_stores_blank_codes_as_null_and_upper_cases_the_rest(self):
		load = _loader()
		rows = [
			{
				"name": f"ZZ-{n}",
				"type_code": code,
				"type_name": f"STG512 {n}",
				"service_category": self.category,
				"is_active": 1,
			}
			for n, code in ((1, ""), (2, ""), (3, "zz-3"))
		]
		with mock.patch.object(frappe.db, "commit"):
			load.load_doctype("Grievance Type", rows, None)
		codes = [frappe.db.get_value("Grievance Type", f"ZZ-{n}", "type_code") for n in (1, 2, 3)]
		self.assertEqual(codes, [None, None, "ZZ-3"])

	def test_preflight_stops_a_seed_that_is_ahead_of_the_app(self):
		load = _loader()
		seed = os.path.join(os.path.dirname(frappe.get_app_path("oan_grievance_service")), "ci", "seed", "_t")
		os.makedirs(os.path.join(seed, "data"), exist_ok=True)
		with open(os.path.join(seed, "data", "Grievance_Type.json"), "w") as f:
			f.write('[{"name": "X", "type_name": "X", "service_category": "Other", "no_such_field": 1}]')
		try:
			manifest = {"files": {"Grievance Type": "Grievance_Type.json"}}
			with self.assertRaises(SystemExit) as caught:
				load.preflight(seed, manifest)
			self.assertIn("no_such_field", str(caught.exception))
		finally:
			os.remove(os.path.join(seed, "data", "Grievance_Type.json"))
			os.rmdir(os.path.join(seed, "data"))
			os.rmdir(seed)

	def test_loader_skips_by_name_or_code_when_the_type_has_no_code_yet(self):
		load = _loader()
		by_name = {"type_code": "SYS", "type_name": "Other", "service_category": self.category}
		self.assertTrue(load.exists_under_another_name("Grievance Type", by_name))
		self.assertFalse(
			load.exists_under_another_name(
				"Grievance Type",
				{"type_code": "ZZ9-99", "type_name": "No such", "service_category": self.category},
			)
		)
		self.assertTrue(
			load.exists_under_another_name(
				"Grievance Service Category", {"code": "006", "category_name": "x"}
			)
		)
