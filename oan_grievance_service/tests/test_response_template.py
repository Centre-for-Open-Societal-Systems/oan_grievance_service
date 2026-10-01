# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from contextlib import contextmanager

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.response_template import (
	create_template,
	delete_template,
	get_template,
	list_templates,
	update_template,
)
from oan_grievance_service.services import response_template as service
from oan_grievance_service.tests.fixtures import a_grievance

BODY = {
	"action_taken": "We inspected the {{ item }} supplied to you.",
	"resolution_summary": "A refund of {{amount}} will be paid within {{ days }} days.",
}


class TestGrievanceResponseTemplate(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.category = _category("STG405 Inputs", "Z95")
		self.other_category = _category("STG405 Market", "Z96")
		self.type = _type("STG405 Seed quality", self.category)
		self.other_type = _type("STG405 Price dispute", self.other_category)
		for name in frappe.get_all(service.DOCTYPE, filters={"title": ["like", "STG405%"]}, pluck="name"):
			frappe.delete_doc(service.DOCTYPE, name, force=1, ignore_permissions=True)
		frappe.clear_messages()

	def _respond(self, template):
		"""File a formal response, optionally started from `template`."""
		return frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": a_grievance(status="In Progress").name,
				"response_type": "Resolved",
				"response_template": template,
				"action_taken": "Handled.",
				"resolution_summary": "<p>Handled.</p>",
				"proposed_close_date": frappe.utils.add_days(None, 5),
			}
		).insert(ignore_permissions=True)

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
		self.assertFalse(frappe.db.exists(service.DOCTYPE, {"title": "STG405 Bad"}))

	def test_edit_keeps_prior_wording_as_history(self):
		created = self._create()
		first = update_template(
			created["id"],
			action_taken="We replaced the {{ item }}.",
			change_note="Tighter wording",
		)["data"]["template"]
		self.assertEqual(first["version"], 2)
		self.assertEqual(first["action_taken"], "We replaced the {{ item }}.")
		self.assertEqual(len(first["versions"]), 1)
		self.assertEqual(first["versions"][0]["version"], 1)
		self.assertEqual(first["versions"][0]["action_taken"], BODY["action_taken"])
		self.assertEqual(first["versions"][0]["change_note"], "Tighter wording")
		self.assertEqual(first["versions"][0]["replaced_by"], "Administrator")
		self.assertTrue(first["versions"][0]["replaced_on"])

		second = update_template(created["id"], title="STG405 Refund v3", response_type="Partially Resolved")[
			"data"
		]["template"]
		self.assertEqual(second["version"], 3)
		self.assertEqual([row["version"] for row in second["versions"]], [2, 1])
		self.assertEqual(second["versions"][0]["title"], "STG405 Refund")
		self.assertEqual(second["versions"][0]["response_type"], "Resolved")
		self.assertEqual(second["versions"][0]["action_taken"], "We replaced the {{ item }}.")
		self.assertEqual(second["versions"][1]["action_taken"], BODY["action_taken"])

		fetched = get_template(created["id"])["data"]["template"]
		self.assertEqual(fetched["version"], 3)
		self.assertEqual(len(fetched["versions"]), 2)

	def test_unchanged_edit_and_flag_toggle_do_not_make_versions(self):
		created = self._create()
		same = update_template(created["id"], title="STG405 Refund", action_taken=BODY["action_taken"])
		self.assertEqual(same["data"]["template"]["version"], 1)

		off = update_template(created["id"], is_active=False)["data"]["template"]
		self.assertFalse(off["is_active"])
		self.assertEqual(off["version"], 1)
		self.assertEqual(off["versions"], [])

	def test_scope_change_is_a_new_version_and_type_can_be_cleared(self):
		created = self._create()
		cleared = update_template(created["id"], grievance_type=None)["data"]["template"]
		self.assertIsNone(cleared["grievance_type"])
		self.assertEqual(cleared["version"], 2)
		self.assertEqual(cleared["versions"][0]["grievance_type"], self.type)

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
		doc = frappe.get_doc(service.DOCTYPE, created["id"])
		self.assertEqual(doc.version, 1)
		self.assertEqual(doc.action_taken, BODY["action_taken"])
		self.assertEqual(len(doc.versions), 0)

	def test_expected_version_guards_a_stale_edit(self):
		created = self._create()
		update_template(created["id"], title="STG405 Edited elsewhere")
		with _keep_transaction():
			stale = update_template(created["id"], title="STG405 Mine", expected_version=1)
		self.assertEqual(stale["code"], "VALIDATION_ERROR", msg=stale)
		self.assertEqual(
			frappe.db.get_value(service.DOCTYPE, created["id"], "title"), "STG405 Edited elsewhere"
		)
		fresh = update_template(created["id"], title="STG405 Mine", expected_version=2)
		self.assertEqual(fresh["data"]["template"]["version"], 3)

	def test_update_validation(self):
		created = self._create()
		with _keep_transaction():
			for payload in ({}, {"priority": "High"}, {"title": None}, {"expected_version": 0}):
				result = update_template(created["id"], **payload)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=payload)

	def test_get_unknown_template_is_404(self):
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
			return {row["id"] for row in result["data"]["templates"]}, result

		self.assertEqual(ids()[0], {a["id"], b["id"], c["id"]})
		self.assertEqual(ids(service_category=self.category)[0], {a["id"], b["id"]})
		self.assertEqual(ids(service_category="Z96")[0], {c["id"]})
		self.assertEqual(ids(grievance_type=self.type)[0], {a["id"]})
		self.assertEqual(
			ids(grievance_type="STG405 Seed quality", service_category=self.category)[0], {a["id"]}
		)
		self.assertEqual(ids(response_type="Requires further info")[0], {b["id"]})
		self.assertEqual(ids(is_active=False)[0], {c["id"]})
		self.assertEqual(ids(is_active="1")[0], {a["id"], b["id"]})
		self.assertEqual(
			ids(service_category="", grievance_type=" ", is_active="")[0], {a["id"], b["id"], c["id"]}
		)
		self.assertEqual(list_templates(q="Beta")["data"]["pagination"]["total_count"], 1)
		self.assertEqual(list_templates(q="STG405 Alp%")["data"]["pagination"]["total_count"], 0)
		self.assertEqual(list_templates(q="STG405_Alpha")["data"]["pagination"]["total_count"], 0)

		first = list_templates(q="STG405", page_size=2)
		second = list_templates(q="STG405", page_size=2, page=2)
		self.assertEqual(first["data"]["pagination"]["total_count"], 3)
		self.assertEqual(first["data"]["pagination"]["total_pages"], 2)
		self.assertTrue(first["data"]["pagination"]["has_next"])
		self.assertEqual(len(first["data"]["templates"]), 2)
		self.assertEqual(len(second["data"]["templates"]), 1)
		self.assertFalse(
			second["data"]["templates"][0]["id"] in {r["id"] for r in first["data"]["templates"]}
		)
		self.assertNotIn("versions", first["data"]["templates"][0])

		with _keep_transaction():
			for bad in (
				{"service_category": "Nope"},
				{"response_type": "Closed"},
				{"page": 0},
				{"page_size": 500},
			):
				self.assertEqual(list_templates(**bad)["code"], "VALIDATION_ERROR", msg=bad)

	def test_unknown_list_filters_are_rejected(self):
		with _keep_transaction():
			for bad in ({"category": self.category}, {"status": "Resolved"}):
				result = list_templates(**bad)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=bad)

	def test_usage_is_counted_from_responses_not_stored(self):
		created = self._create()
		self.assertEqual((created["use_count"], created["last_used_on"]), (0, None))
		self._respond(created["id"])
		self._respond(created["id"])
		self._respond(None)
		fetched = get_template(created["id"])["data"]["template"]
		self.assertEqual(fetched["use_count"], 2)
		self.assertTrue(fetched["last_used_on"])
		self.assertEqual(fetched["version"], 1)
		listed = list_templates(q="STG405 Refund")["data"]["templates"]
		self.assertEqual([(row["id"], row["use_count"]) for row in listed], [(created["id"], 2)])
		self.assertFalse(frappe.get_meta(service.DOCTYPE).has_field("use_count"))

	def test_delete_removes_an_unused_template(self):
		created = self._create()
		result = delete_template(created["id"])
		self.assertEqual(result["status"], "success", msg=result)
		self.assertTrue(result["data"]["deleted"])
		self.assertFalse(frappe.db.exists(service.DOCTYPE, created["id"]))
		self.assertEqual(frappe.db.count(service.VERSION_DOCTYPE, {"parent": created["id"]}), 0)

	def test_delete_only_deactivates_a_used_template(self):
		created = self._create()
		update_template(created["id"], title="STG405 Used")
		self._respond(created["id"])
		result = delete_template(created["id"])
		self.assertFalse(result["data"]["deleted"])
		self.assertFalse(result["data"]["template"]["is_active"])
		self.assertEqual(len(result["data"]["template"]["versions"]), 1)
		self.assertTrue(frappe.db.exists(service.DOCTYPE, created["id"]))

		again = delete_template(created["id"])
		self.assertFalse(again["data"]["deleted"])
		self.assertFalse(again["data"]["template"]["is_active"])

	def test_validation_applies_outside_the_api(self):
		doc = frappe.new_doc(service.DOCTYPE)
		doc.update(
			{
				"template_code": "STG405-DESK",
				"title": "STG405 Desk",
				"service_category": self.category,
				"grievance_type": self.other_type,
				"response_type": "Resolved",
				**BODY,
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong"):
			doc.insert(ignore_permissions=True)

	def test_response_types_match_the_doctype_and_the_request_schema(self):
		from typing import get_args

		from oan_grievance_service.api.v1.response_template import ResponseType

		select = frappe.get_meta(service.DOCTYPE).get_field("response_type").options.split("\n")
		self.assertEqual(list(service.RESPONSE_TYPES), select)
		self.assertEqual(list(get_args(ResponseType)), select)

	def test_extract_placeholders(self):
		self.assertEqual(
			service.extract_placeholders("{{a}} {{ b }}", "{{ a }} {{c_1}}", None), ["a", "b", "c_1"]
		)
		self.assertEqual(service.extract_placeholders("no markers", ""), [])

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
		self.assertEqual(frappe.db.get_value(service.DOCTYPE, created["id"], "title"), "STG405 Refund")

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
