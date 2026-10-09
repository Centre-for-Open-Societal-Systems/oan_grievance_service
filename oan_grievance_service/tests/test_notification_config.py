# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import json
import re

import frappe
import frappe.api
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.router import ensure_routes_registered
from oan_grievance_service.api.v1.notification_config import (
	get_notification_config,
	list_notification_configs,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.notification_config import EVENTS, display_template
from oan_grievance_service.tests.test_category_assignment import _user
from oan_grievance_service.tests.test_router import make_test_request

FRONTEND_KEYS = {
	"id",
	"title",
	"eventType",
	"active",
	"channel",
	"subject",
	"trigger",
	"recipients",
	"template",
	"lastEdited",
}


class TestNotificationConfig(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _events(self):
		data = list_notification_configs()["data"]
		return {event["id"]: event for event in data["notification_configs"]}

	def _records(self, code):
		return frappe.get_all(
			"Notification",
			filters={"document_type": "Grievance", "event": "Method", "method": code},
			pluck="name",
		)

	def test_lists_the_nineteen_appendix_c_events_in_order(self):
		data = list_notification_configs()["data"]["notification_configs"]
		self.assertEqual(len(data), 19)
		self.assertEqual([event["id"] for event in data], [f"EC-{n:03d}" for n in range(1, 20)])
		self.assertEqual([event["id"] for event in data], [event.id for event in EVENTS])

	def test_every_event_fits_the_frontend_type(self):
		for event_id, event in self._events().items():
			self.assertTrue(set(event) <= FRONTEND_KEYS, event_id)
			self.assertTrue(
				{"id", "title", "active", "channel", "subject", "trigger", "recipients"} <= set(event)
			)
			self.assertIsInstance(event["active"], bool)
			self.assertTrue(set(event["channel"]) <= {"SMS", "Email"}, event_id)
			self.assertTrue(event["recipients"], event_id)

	def test_a_seeded_event_reports_its_record(self):
		event = self._events()["EC-001"]
		self.assertEqual(event["title"], "Submission Received")
		self.assertEqual(event["eventType"], C.EVENT_SUBMISSION_RECEIVED)
		self.assertEqual(event["trigger"], "Immediately on save")
		self.assertEqual(event["channel"], ["SMS", "Email"])
		self.assertEqual(event["recipients"], ["Submitter"])
		self.assertTrue(event["active"])
		self.assertEqual(event["subject"], "Submission Received")
		self.assertRegex(event["lastEdited"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

	def test_template_is_english_with_tokens_not_jinja(self):
		template = self._events()["EC-001"]["template"]
		self.assertIn("{{id}}", template)
		self.assertIn("{{category}}", template)
		self.assertNotIn("_(", template)
		self.assertNotIn("{%", template)
		self.assertNotIn("{0}", template)

	def test_every_stored_template_converts_cleanly(self):
		for event_id, event in self._events().items():
			self.assertFalse(re.search(r"_\(|\{%|\{\d+\}|\.format", event["template"]), event_id)
			self.assertFalse(re.search(r"_\(|\{%|\.format", event["subject"]), event_id)

	def test_email_only_event(self):
		event = self._events()["EC-003"]
		self.assertEqual(event["channel"], ["Email"])
		self.assertEqual(event["recipients"], ["L1 Officer"])

	def test_event_without_a_backend_record_reports_inactive(self):
		event = self._events()["EC-017"]
		self.assertFalse(event["active"])
		self.assertEqual(event["recipients"], ["L2 Officer"])
		self.assertIsNone(event["eventType"])
		self.assertEqual((event["subject"], event["template"]), ("", ""))
		self.assertIsNone(event["lastEdited"])

	def test_switching_off_one_channel_keeps_the_event_active(self):
		sms = frappe.db.get_value(
			"Notification",
			{"document_type": "Grievance", "method": C.EVENT_SUBMISSION_RECEIVED, "channel": "SMS"},
		)
		frappe.db.set_value("Notification", sms, "enabled", 0)
		event = self._events()["EC-001"]
		self.assertTrue(event["active"])
		self.assertEqual(event["channel"], ["Email"])

	def test_switching_off_every_channel_deactivates_the_event(self):
		for name in self._records(C.EVENT_SUBMISSION_RECEIVED):
			frappe.db.set_value("Notification", name, "enabled", 0)
		event = self._events()["EC-001"]
		self.assertFalse(event["active"])
		self.assertEqual(event["channel"], ["SMS", "Email"])

	def test_an_edit_shows_up_with_a_later_timestamp(self):
		before = self._events()["EC-005"]["lastEdited"]
		name = self._records(C.EVENT_STATUS_IN_PROGRESS)[0]
		frappe.db.set_value("Notification", name, "modified", "2099-01-02 03:04:05", update_modified=False)
		after = self._events()["EC-005"]["lastEdited"]
		self.assertNotEqual(before, after)
		self.assertRegex(after, r"^2099-01-0[12]T")

	def test_get_returns_one_event_matching_the_list(self):
		listed = self._events()["EC-008"]
		result = get_notification_config(event_id="EC-008")
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["notification_config"], listed)

	def test_get_accepts_a_lowercase_id(self):
		result = get_notification_config(event_id="ec-019")
		self.assertEqual(result["data"]["notification_config"]["id"], "EC-019")

	def test_get_unknown_id_is_not_found(self):
		for event_id in ("EC-020", "EC-000", "nope"):
			result = get_notification_config(event_id=event_id)
			self.assertNotEqual(result.get("status"), "success", event_id)

	def test_list_rejects_unknown_parameters(self):
		self.assertNotEqual(list_notification_configs(bogus="1").get("status"), "success")

	def test_requires_admin_role(self):
		outsider = _user("stg427-officer@example.com", "Plain Officer")
		frappe.set_user(outsider)
		self.assertNotEqual(list_notification_configs().get("status"), "success")
		self.assertNotEqual(get_notification_config(event_id="EC-001").get("status"), "success")


class TestNotificationConfigHttp(FrappeTestCase):
	"""The routes, status codes and JSON, through Frappe's request handler."""

	def setUp(self):
		ensure_routes_registered()
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _get(self, path):
		response = frappe.api.handle(make_test_request(path, method="GET"))
		return response.status_code, json.loads(response.get_data(as_text=True))

	def test_list(self):
		status, body = self._get("/api/v1/notification-configs")
		self.assertEqual(status, 200)
		self.assertEqual(body["status"], "success")
		self.assertEqual(body["meta"]["api_version"], "v1")
		events = body["data"]["notification_configs"]
		self.assertEqual(len(events), 19)
		self.assertEqual(set(events[0]), FRONTEND_KEYS)

	def test_get_one(self):
		status, body = self._get("/api/v1/notification-configs/EC-016")
		self.assertEqual(status, 200)
		event = body["data"]["notification_config"]
		self.assertEqual(event["id"], "EC-016")
		self.assertEqual(event["eventType"], C.EVENT_SLA_BREACH)

	def test_unknown_id_is_404(self):
		status, body = self._get("/api/v1/notification-configs/EC-999")
		self.assertEqual(status, 404)
		self.assertNotEqual(body["status"], "success")

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		status, _body = self._get("/api/v1/notification-configs")
		self.assertIn(status, (401, 403))

	def test_non_admin_is_forbidden(self):
		frappe.set_user(_user("stg427-http@example.com", "Plain Officer"))
		status, _body = self._get("/api/v1/notification-configs/EC-001")
		self.assertEqual(status, 403)


class TestDisplayTemplate(FrappeTestCase):
	def test_empty(self):
		self.assertEqual(display_template(None), "")
		self.assertEqual(display_template(""), "")

	def test_translated_sentence_with_fields(self):
		stored = (
			"{{ _('Grievance {0} ({1} / {2}) is assigned.', context='grievance.x')"
			".format(doc.ticket_number, doc.service_category, doc.grievance_type) }}"
		)
		self.assertEqual(
			display_template(stored),
			"Grievance {{id}} ({{category}} / {{grievanceType}}) is assigned.",
		)

	def test_unmapped_field_becomes_a_camel_case_token(self):
		stored = "{{ _('Due {0}', context='k').format(doc.next_escalation_at) }}"
		self.assertEqual(display_template(stored), "Due {{nextEscalationAt}}")

	def test_double_quoted_literal_and_escapes(self):
		stored = "{{ _(\"It's {0}\", context='k').format(doc.ticket_number) }}"
		self.assertEqual(display_template(stored), "It's {{id}}")

	def test_conditional_keeps_the_branch_for_a_set_field(self):
		stored = (
			"{{ _('Received {0}.', context='k').format(doc.ticket_number) }} "
			"{% if doc.sla_due_date %}{{ _('Due {0}.', context='k.then').format(doc.sla_due_date) }}"
			"{% else %}{{ _('Not yet assigned.', context='k.else') }}{% endif %}"
		)
		self.assertEqual(display_template(stored), "Received {{id}}. Due {{slaDeadline}}.")

	def test_hand_written_wording_is_left_alone(self):
		self.assertEqual(display_template("Dear {{ doc.owner }}, hello."), "Dear {{ doc.owner }}, hello.")
