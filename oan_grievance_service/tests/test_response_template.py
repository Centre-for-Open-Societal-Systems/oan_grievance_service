# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from contextlib import contextmanager
from typing import get_args

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from oan_grievance_service.api.v1.response_template import (
	DOCTYPE,
	ResponseType,
	create_template,
	delete_template,
	get_template,
	list_templates,
	update_template,
)
from oan_grievance_service.grievance_masters.doctype.grievance_response_template.grievance_response_template import (
	extract_placeholders,
)

BODY = {
	"action_taken": "We inspected the {{ item }} supplied to you.",
	"resolution_summary": "A refund of {{amount}} will be paid within {{ days }} days.",
}


@contextmanager
def _record_versions():
	"""Frappe skips its change log under test (`ignore_version = frappe.in_test`).

	Production keeps it, and the edit history is read from it, so the tests that assert on
	history switch the test flag off for their duration.
	"""
	original = frappe.in_test
	frappe.in_test = False
	try:
		yield
	finally:
		frappe.in_test = original


class TestGrievanceResponseTemplate(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.category = _category("STG405 Inputs", "Z95")
		self.other_category = _category("STG405 Market", "Z96")
		self.type = _type("STG405 Seed quality", self.category)
		self.other_type = _type("STG405 Price dispute", self.other_category)
		for name in frappe.get_all(DOCTYPE, filters={"title": ["like", "STG405%"]}, pluck="name"):
			frappe.delete_doc(DOCTYPE, name, force=1, ignore_permissions=True)
		frappe.clear_messages()

	def _create(self, title="STG405 Refund", **overrides):
		values = {
			"title": title,
			"service_category": self.category,
			"grievance_type": self.type,
			"response_type": "Resolved",
			**BODY,
			**overrides,
		}
		result = create_template(**values)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["template"]

	def _used(self, template_id, times=1):
		"""Stand in for the response flow, which does not record template use yet."""
		frappe.db.set_value(DOCTYPE, template_id, {"use_count": times, "last_used_on": now_datetime()})

	def test_create_starts_at_version_one(self):
		template = self._create()
		self.assertEqual(template["version"], 1)
		self.assertEqual(template["service_category"], self.category)
		self.assertEqual(template["service_category_name"], self.category)
		self.assertEqual(template["grievance_type"], self.type)
		self.assertEqual(template["grievance_type_name"], "STG405 Seed quality")
		self.assertEqual(template["response_type"], "Resolved")
		self.assertEqual(template["placeholders"], ["item", "amount", "days"])
		self.assertEqual(template["use_count"], 0)
		self.assertIsNone(template["last_used_on"])
		self.assertTrue(template["is_active"])
		self.assertEqual(template["versions"], [])
		self.assertTrue(template["id"].startswith("RT-"))

	def test_create_accepts_category_code_and_omits_subcategory(self):
		template = self._create(service_category="Z95", grievance_type=None)
		self.assertEqual(template["service_category"], self.category)
		self.assertIsNone(template["grievance_type"])

	def test_create_rejects_bad_scope_and_placeholders(self):
		with _keep_transaction():
			cases = {
				"unknown category": {"service_category": "No Such Category"},
				"type from another category": {"grievance_type": self.other_type},
				"unknown type": {"grievance_type": "No Such Type"},
				"unclosed placeholder": {"action_taken": "Pay {{ amount now"},
				"bad placeholder name": {"resolution_summary": "Pay {{ 1amount }}"},
				"unknown response type": {"response_type": "Closed"},
				"blank title": {"title": "  "},
				"action taken over 500": {"action_taken": "x" * 501},
			}
			for label, override in cases.items():
				result = create_template(
					**{
						"title": "STG405 Bad",
						"service_category": self.category,
						"response_type": "Resolved",
						**BODY,
						**override,
					}
				)
				self.assertEqual(result["status"], "error", msg=label)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=label)
		self.assertFalse(frappe.db.exists(DOCTYPE, {"title": "STG405 Bad"}))

	@_record_versions()
	def test_edit_raises_the_version_and_keeps_the_replaced_wording(self):
		created = self._create()
		first = update_template(created["id"], action_taken="We replaced the {{ item }}.")["data"]["template"]
		self.assertEqual(first["version"], 2)
		self.assertEqual(first["action_taken"], "We replaced the {{ item }}.")
		self.assertEqual(len(first["versions"]), 1)
		edit = first["versions"][0]
		self.assertEqual(edit["version"], 2)
		self.assertEqual(edit["edited_by"], "Administrator")
		self.assertTrue(edit["edited_on"])
		changes = {row["field"]: row for row in edit["changes"]}
		self.assertEqual(changes["action_taken"]["old"], BODY["action_taken"])
		self.assertEqual(changes["action_taken"]["new"], "We replaced the {{ item }}.")
		self.assertEqual((changes["version"]["old"], changes["version"]["new"]), ("1", "2"))

		second = update_template(created["id"], title="STG405 Refund v3", response_type="Partially Resolved")[
			"data"
		]["template"]
		self.assertEqual(second["version"], 3)
		self.assertEqual([row["version"] for row in second["versions"]], [3, 2])
		newest = {row["field"]: row for row in second["versions"][0]["changes"]}
		self.assertEqual(newest["title"]["old"], "STG405 Refund")
		self.assertEqual(newest["response_type"]["old"], "Resolved")

		fetched = get_template(created["id"])["data"]["template"]
		self.assertEqual(fetched["version"], 3)
		self.assertEqual(fetched["versions"], second["versions"])

	@_record_versions()
	def test_unchanged_edit_and_flag_toggle_do_not_raise_the_version(self):
		created = self._create()
		same = update_template(created["id"], title="STG405 Refund", action_taken=BODY["action_taken"])
		self.assertEqual(same["data"]["template"]["version"], 1)
		self.assertEqual(same["data"]["template"]["versions"], [])

		off = update_template(created["id"], is_active=False)["data"]["template"]
		self.assertFalse(off["is_active"])
		self.assertEqual(off["version"], 1)
		self.assertEqual([row["field"] for row in off["versions"][0]["changes"]], ["is_active"])
		self.assertEqual(off["versions"][0]["version"], 1)

	@_record_versions()
	def test_scope_change_raises_the_version_and_type_can_be_cleared(self):
		created = self._create()
		cleared = update_template(created["id"], grievance_type=None)["data"]["template"]
		self.assertIsNone(cleared["grievance_type"])
		self.assertEqual(cleared["version"], 2)
		changes = {row["field"]: row for row in cleared["versions"][0]["changes"]}
		self.assertEqual(changes["grievance_type"]["old"], self.type)

		moved = update_template(created["id"], service_category=self.other_category)["data"]["template"]
		self.assertEqual(moved["service_category"], self.other_category)
		self.assertEqual(moved["version"], 3)

		with _keep_transaction():
			stale = update_template(created["id"], grievance_type=self.type)
		self.assertEqual(stale["code"], "VALIDATION_ERROR", msg=stale)

	def test_edit_that_breaks_a_rule_saves_nothing(self):
		created = self._create()
		with _keep_transaction():
			result = update_template(created["id"], action_taken="Broken {{ placeholder")
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		doc = frappe.get_doc(DOCTYPE, created["id"])
		self.assertEqual(doc.version, 1)
		self.assertEqual(doc.action_taken, BODY["action_taken"])

	def test_version_cannot_be_set_directly(self):
		created = self._create()
		doc = frappe.get_doc(DOCTYPE, created["id"])
		doc.version = 9
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.version, 1)

	def test_update_validation(self):
		created = self._create()
		with _keep_transaction():
			for payload in ({}, {"priority": "High"}, {"title": None}, {"version": 5}):
				result = update_template(created["id"], **payload)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=payload)

	def test_unknown_template_is_404(self):
		with _keep_transaction():
			for result in (
				get_template("RT-99999"),
				update_template("RT-99999", title="x"),
				delete_template("RT-99999"),
			):
				self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	def test_list_filters_and_pages(self):
		a = self._create("STG405 Alpha")
		b = self._create("STG405 Beta", response_type="Requires further info", grievance_type=None)
		c = self._create(
			"STG405 Gamma",
			service_category=self.other_category,
			grievance_type=self.other_type,
			is_active=False,
		)

		def ids(**filters):
			result = list_templates(q="STG405", **filters)
			self.assertEqual(result["status"], "success", msg=result)
			return {row["id"] for row in result["data"]["templates"]}

		self.assertEqual(ids(), {a["id"], b["id"], c["id"]})
		self.assertEqual(ids(service_category=self.category), {a["id"], b["id"]})
		self.assertEqual(ids(service_category="Z96"), {c["id"]})
		self.assertEqual(ids(grievance_type=self.type), {a["id"]})
		self.assertEqual(ids(grievance_type="STG405 Seed quality", service_category=self.category), {a["id"]})
		self.assertEqual(ids(response_type="Requires further info"), {b["id"]})
		self.assertEqual(ids(is_active=False), {c["id"]})
		self.assertEqual(ids(is_active="1"), {a["id"], b["id"]})
		self.assertEqual(
			ids(service_category="", grievance_type=" ", is_active=""), {a["id"], b["id"], c["id"]}
		)
		self.assertEqual(list_templates(q="Beta")["data"]["pagination"]["total_count"], 1)
		# LIKE wildcards in the search are literal characters, not patterns.
		self.assertEqual(list_templates(q="STG405 Alp%")["data"]["pagination"]["total_count"], 0)
		self.assertEqual(list_templates(q="STG405_Alpha")["data"]["pagination"]["total_count"], 0)

		first = list_templates(q="STG405", page_size=2)["data"]
		second = list_templates(q="STG405", page_size=2, page=2)["data"]
		self.assertEqual(first["pagination"]["total_count"], 3)
		self.assertEqual(first["pagination"]["total_pages"], 2)
		self.assertTrue(first["pagination"]["has_next"])
		self.assertEqual(len(first["templates"]), 2)
		self.assertEqual(len(second["templates"]), 1)
		self.assertNotIn(second["templates"][0]["id"], {row["id"] for row in first["templates"]})
		self.assertNotIn("versions", first["templates"][0])

		with _keep_transaction():
			for bad in (
				{"service_category": "Nope"},
				{"response_type": "Closed"},
				{"page": 0},
				{"page_size": 500},
				{"category": self.category},
			):
				self.assertEqual(list_templates(**bad)["code"], "VALIDATION_ERROR", msg=bad)

	def test_use_count_and_last_used_are_returned(self):
		created = self._create()
		self._used(created["id"], times=3)
		fetched = get_template(created["id"])["data"]["template"]
		self.assertEqual(fetched["use_count"], 3)
		self.assertTrue(fetched["last_used_on"])
		listed = list_templates(q="STG405 Refund")["data"]["templates"]
		self.assertEqual([(row["id"], row["use_count"]) for row in listed], [(created["id"], 3)])

	def test_delete_removes_an_unused_template(self):
		created = self._create()
		result = delete_template(created["id"])
		self.assertEqual(result["status"], "success", msg=result)
		self.assertTrue(result["data"]["deleted"])
		self.assertFalse(frappe.db.exists(DOCTYPE, created["id"]))

	def test_delete_only_deactivates_a_used_template(self):
		created = self._create()
		update_template(created["id"], title="STG405 Used")
		self._used(created["id"])
		result = delete_template(created["id"])
		self.assertFalse(result["data"]["deleted"])
		self.assertFalse(result["data"]["template"]["is_active"])
		self.assertTrue(frappe.db.exists(DOCTYPE, created["id"]))

		again = delete_template(created["id"])
		self.assertFalse(again["data"]["deleted"])
		self.assertFalse(again["data"]["template"]["is_active"])

	def test_validation_applies_outside_the_api(self):
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"title": "STG405 Desk",
				"service_category": self.category,
				"grievance_type": self.other_type,
				"response_type": "Resolved",
				**BODY,
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong"):
			doc.insert(ignore_permissions=True)

		doc.grievance_type = self.type
		doc.insert(ignore_permissions=True)
		self.assertTrue(doc.name.startswith("RT-"))
		self.assertEqual(doc.version, 1)

	def test_response_types_match_the_doctype_and_the_request_schema(self):
		select = frappe.get_meta(DOCTYPE).get_field("response_type").options.split("\n")
		self.assertEqual(list(get_args(ResponseType)), select)

	def test_extract_placeholders(self):
		self.assertEqual(extract_placeholders("{{a}} {{ b }}", "{{ a }} {{c_1}}", None), ["a", "b", "c_1"])
		self.assertEqual(extract_placeholders("no markers", ""), [])

	def test_officer_and_guest_cannot_manage_templates(self):
		created = self._create()
		officer = _user("stg405-officer@example.com", "Almaz Bekele")
		for user in (officer, "Guest"):
			frappe.set_user(user)
			with _keep_transaction():
				for result in (
					list_templates(),
					get_template(created["id"]),
					create_template(
						title="STG405 Nope",
						service_category=self.category,
						response_type="Resolved",
						**BODY,
					),
					create_template(title="x"),
					update_template(created["id"], title="STG405 Nope"),
					delete_template(created["id"]),
				):
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(DOCTYPE, created["id"], "title"), "STG405 Refund")

	def test_grievance_admin_can_manage_templates(self):
		admin = _user("stg405-admin@example.com", "Selam Admin", role="Grievance Admin")
		frappe.set_user(admin)
		created = self._create("STG405 Admin made")
		self.assertEqual(update_template(created["id"], title="STG405 Admin edit")["status"], "success")
		self.assertEqual(list_templates(q="STG405 Admin")["data"]["pagination"]["total_count"], 1)


@contextmanager
def _keep_transaction():
	"""API errors call frappe.db.rollback(), which would erase this test's fixtures."""
	original = frappe.db.rollback
	frappe.db.rollback = lambda *args, **kwargs: None
	try:
		yield
	finally:
		frappe.db.rollback = original


def _user(email, full_name, role="Grievance Officer"):
	if frappe.db.exists("User", email):
		return email
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"enabled": 1,
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	).insert(ignore_permissions=True)
	return email


def _category(name, code):
	if frappe.db.exists("Grievance Service Category", name):
		return name
	frappe.get_doc(
		{"doctype": "Grievance Service Category", "category_name": name, "code": code, "is_active": 1}
	).insert(ignore_permissions=True)
	return name


def _type(name, category):
	existing = frappe.db.get_value("Grievance Type", {"type_name": name, "service_category": category})
	if existing:
		return existing
	return (
		frappe.get_doc(
			{"doctype": "Grievance Type", "type_name": name, "service_category": category, "is_active": 1}
		)
		.insert(ignore_permissions=True)
		.name
	)
