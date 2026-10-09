# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.notification_template import (
	get_notification_placeholders,
	get_notification_template,
	get_notification_template_options,
	list_notification_templates,
	update_notification_template,
)
from oan_grievance_service.services import notifications


class TestNotificationTemplatesAPI(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_list_returns_both_active_and_inactive_by_default(self):
		# Create one active and one inactive notification
		active_name = "Grievance: Test Active Template (Email)"
		inactive_name = "Grievance: Test Inactive Template (SMS)"

		for name in (active_name, inactive_name):
			if frappe.db.exists("Notification", name):
				frappe.delete_doc("Notification", name, force=1, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Notification",
				"name": active_name,
				"subject": "{{ _('Active Subject', context='test.active') }}",
				"document_type": "Grievance",
				"event": "Method",
				"method": "test_active_event",
				"channel": "Email",
				"grievance_recipient_type": "Submitter",
				"message": "{{ _('Active Message', context='test.active') }}",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Notification",
				"name": inactive_name,
				"subject": "{{ _('Inactive Subject', context='test.inactive') }}",
				"document_type": "Grievance",
				"event": "Method",
				"method": "test_inactive_event",
				"channel": "SMS",
				"grievance_recipient_type": "Role Level",
				"grievance_role_level": "nodal_officer",
				"message": "{{ _('Inactive Message', context='test.inactive') }}",
				"enabled": 0,
			}
		).insert(ignore_permissions=True)

		# Default listing must return both active and inactive
		resp = list_notification_templates(page_size=100)
		self.assertEqual(resp["status"], "success")
		names = {t["name"] for t in resp["data"]["templates"]}
		self.assertIn(active_name, names)
		self.assertIn(inactive_name, names)

		# Filter enabled=true
		resp_active = list_notification_templates(enabled="true", page_size=100)
		active_names = {t["name"] for t in resp_active["data"]["templates"]}
		self.assertIn(active_name, active_names)
		self.assertNotIn(inactive_name, active_names)

		# Filter enabled=false
		resp_inactive = list_notification_templates(enabled="false", page_size=100)
		inactive_names = {t["name"] for t in resp_inactive["data"]["templates"]}
		self.assertNotIn(active_name, inactive_names)
		self.assertIn(inactive_name, inactive_names)

		# Filter by channel
		resp_email = list_notification_templates(channel="Email", page_size=100)
		email_names = {t["name"] for t in resp_email["data"]["templates"]}
		self.assertIn(active_name, email_names)
		self.assertNotIn(inactive_name, email_names)

		# Filter by recipient_type
		resp_role = list_notification_templates(recipient_type="Role Level", page_size=100)
		role_names = {t["name"] for t in resp_role["data"]["templates"]}
		self.assertIn(inactive_name, role_names)
		self.assertNotIn(active_name, role_names)

	def test_get_notification_template(self):
		test_name = "Grievance: Test Get Template (Email)"
		if frappe.db.exists("Notification", test_name):
			frappe.delete_doc("Notification", test_name, force=1, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Notification",
				"name": test_name,
				"subject": "{{ _('Get Subject', context='test.get') }}",
				"document_type": "Grievance",
				"event": "Method",
				"method": "test_get_event",
				"channel": "Email",
				"grievance_recipient_type": "Role Level",
				"grievance_role_level": "department_head",
				"message": "{{ _('Get Message', context='test.get') }}",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

		res = get_notification_template(test_name)
		self.assertEqual(res["status"], "success")
		template = res["data"]["template"]
		self.assertEqual(template["name"], test_name)
		self.assertEqual(template["event"], "test_get_event")
		self.assertEqual(template["channel"], "Email")
		self.assertEqual(template["recipient_type"], "Role Level")
		self.assertEqual(template["role_level"], "department_head")
		self.assertEqual(template["role_level_name"], "Department Head")
		self.assertTrue(template["enabled"])

	def test_dynamic_resolution_for_new_role_level(self):
		"""Adding a new role level dynamically resolves without code changes."""
		new_level_code = "zonal_officer_test"
		if not frappe.db.exists("Grievance Role Level", new_level_code):
			frappe.get_doc(
				{
					"doctype": "Grievance Role Level",
					"level_code": new_level_code,
					"level_name": "Zonal Officer Test",
					"level_order": 15,
					"escalation_hours": 36,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		user_email = "zonal-test@example.com"
		if not frappe.db.exists("User", user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "Zonal",
					"last_name": "Officer",
					"enabled": 1,
					"roles": [{"role": "Grievance Officer"}],
				}
			).insert(ignore_permissions=True)

		dept_name = "Zonal Test Dept"
		if not frappe.db.exists("Grievance Department", dept_name):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": dept_name,
					"short_name": "ZTD",
				}
			).insert(ignore_permissions=True)

		# Setup desk assignment with the new role level
		desk = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": dept_name,
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": user_email,
						"role_level": new_level_code,
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "Grievance RBAC Assignment", desk.name, force=True, ignore_permissions=True
		)

		grievance = frappe._dict(
			{
				"name": "GRV-ZONAL-001",
				"assigned_dept": dept_name,
				"administrative_area": None,
			}
		)

		# Resolve via the two-tier parameters
		resolved = notifications.resolve_recipient(
			grievance,
			notifications.RECIPIENT_ROLE_LEVEL,
			role_level=new_level_code,
		)
		self.assertEqual(resolved, user_email)

		# Also resolve passing new_level_code directly as recipient_role
		resolved_direct = notifications.resolve_recipient(
			grievance,
			new_level_code,
		)
		self.assertEqual(resolved_direct, user_email)

	def test_update_notification_template(self):
		test_name = "Grievance: Test Patch Template (Email)"
		if frappe.db.exists("Notification", test_name):
			frappe.delete_doc("Notification", test_name, force=1, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Notification",
				"name": test_name,
				"subject": "{{ _('Original Subject') }}",
				"document_type": "Grievance",
				"event": "Method",
				"method": "test_patch_event",
				"channel": "Email",
				"grievance_recipient_type": "Submitter",
				"message": "{{ _('Original Message') }}",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# 1. Update using simplified human-readable strings
		res = update_notification_template(
			template=test_name,
			subject="Updated Subject: Case {ticket_number}",
			body="Updated Body: Hello {ticket_number} regarding {service_category}.",
			enabled=False,
			recipient_type="Role Level",
			role_level="nodal_officer",
			translations={
				"am": {
					"subject": "የተሻሻለ ርዕስ: ጉዳይ {0}",
					"body": "ሰላም {0} ስለ {1}።",
				}
			},
		)
		self.assertEqual(res["status"], "success")
		template = res["data"]["template"]
		self.assertEqual(template["subject"], "Updated Subject: Case {ticket_number}")
		self.assertEqual(
			template["body"], "Updated Body: Hello {ticket_number} regarding {service_category}."
		)
		self.assertEqual(template["placeholders"], ["ticket_number", "service_category"])
		self.assertFalse(template["enabled"])
		self.assertEqual(template["recipient_type"], "Role Level")
		self.assertEqual(template["role_level"], "nodal_officer")
		self.assertIn("am", template["translations"])
		self.assertEqual(template["translations"]["am"]["subject"], "የተሻሻለ ርዕስ: ጉዳይ {0}")

		# Verify translatable Jinja compilation in DB
		doc = frappe.get_doc("Notification", test_name)
		self.assertEqual(
			doc.subject,
			"{{ _('Updated Subject: Case {0}', context='grievance.test_patch_event.email.subject').format(doc.ticket_number) }}",
		)
		self.assertEqual(
			doc.message,
			"{{ _('Updated Body: Hello {0} regarding {1}.', context='grievance.test_patch_event.email').format(doc.ticket_number, doc.service_category) }}",
		)
		self.assertEqual(doc.enabled, 0)
		self.assertEqual(doc.grievance_recipient_type, "Role Level")
		self.assertEqual(doc.grievance_role_level, "nodal_officer")

		# Verify validate_notification passes without error
		notifications.validate_notification(doc)
		frappe.db.commit()

		# 2. Verify invalid placeholder rejection
		err_res = update_notification_template(
			template=test_name,
			body="Invalid field: {non_existent_field_xyz}",
		)
		self.assertEqual(err_res["status"], "error")
		self.assertEqual(err_res["code"], "VALIDATION_ERROR")
		self.assertIn("Invalid placeholder", err_res["message"])

		# 3. Verify backward-compatible raw Jinja acceptance
		res_raw = update_notification_template(
			template=test_name,
			subject="{{ _('Raw Subject: Case {0}').format(doc.ticket_number) }}",
			body="{{ _('Raw Body: Hello {0}').format(doc.ticket_number) }}",
		)
		self.assertEqual(res_raw["status"], "success")
		t_raw = res_raw["data"]["template"]
		self.assertEqual(t_raw["subject"], "Raw Subject: Case {ticket_number}")
		self.assertEqual(t_raw["body"], "Raw Body: Hello {ticket_number}")
		self.assertEqual(t_raw["raw_subject"], "{{ _('Raw Subject: Case {0}').format(doc.ticket_number) }}")
		self.assertEqual(t_raw["raw_body"], "{{ _('Raw Body: Hello {0}').format(doc.ticket_number) }}")

		# 4. Verify department alias compiles to doc.assigned_dept
		res_alias = update_notification_template(
			template=test_name,
			body="Case {ticket_number} assigned to {department}.",
		)
		self.assertEqual(res_alias["status"], "success")
		doc = frappe.get_doc("Notification", test_name)
		self.assertEqual(
			doc.message,
			"{{ _('Case {0} assigned to {1}.', context='grievance.test_patch_event.email').format(doc.ticket_number, doc.assigned_dept) }}",
		)

	def test_get_notification_placeholders(self):
		res = get_notification_placeholders()
		self.assertEqual(res["status"], "success")
		placeholders = res["data"]["placeholders"]
		self.assertTrue(len(placeholders) > 0)
		names = {p["name"] for p in placeholders}
		self.assertIn("ticket_number", names)
		self.assertIn("service_category", names)
		self.assertIn("assigned_dept", names)
		self.assertIn("department", names)
		self.assertIn("sla_due_date", names)
		self.assertIn("status", names)

		# Verify structure
		item = next(p for p in placeholders if p["name"] == "ticket_number")
		self.assertEqual(item["label"], "Ticket Number")
		self.assertTrue(len(item["description"]) > 0)
		self.assertTrue(len(item["example"]) > 0)

	def test_get_notification_template_options(self):
		res = get_notification_template_options()
		self.assertEqual(res["status"], "success")
		data = res["data"]
		self.assertIn("channels", data)
		self.assertIn("recipients", data)
		self.assertIn("placeholders", data)
		self.assertIn("role_levels", data)

		channels = data["channels"]
		self.assertIn("Email", channels)
		self.assertIn("SMS", channels)

		recipient_ids = {r["id"] for r in data["recipients"]}
		self.assertIn("submitter", recipient_ids)
		self.assertIn("assigned_officer", recipient_ids)
		self.assertIn("nodal_officer", recipient_ids)

		# Role levels
		level_codes = {rl["level_code"] for rl in data["role_levels"]}
		self.assertIn("nodal_officer", level_codes)

	def test_update_notification_template_with_recipient_id(self):
		test_name = "Grievance: Test Recipient ID Update (Email)"
		if frappe.db.exists("Notification", test_name):
			frappe.delete_doc("Notification", test_name, force=1, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Notification",
				"name": test_name,
				"subject": "{{ _('Test Subject') }}",
				"document_type": "Grievance",
				"event": "Method",
				"method": "test_recip_id_event",
				"channel": "Email",
				"grievance_recipient_type": "Submitter",
				"message": "{{ _('Test Message') }}",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

		# 1. Update using flat recipient_id = "department_head"
		res = update_notification_template(
			template=test_name,
			recipient_id="department_head",
		)
		self.assertEqual(res["status"], "success")
		template = res["data"]["template"]
		self.assertEqual(template["recipient_type"], "Role Level")
		self.assertEqual(template["role_level"], "department_head")
		self.assertEqual(template["recipient_id"], "department_head")

		# 2. Update back using flat recipient_id = "submitter"
		res_sub = update_notification_template(
			template=test_name,
			recipient_id="submitter",
		)
		self.assertEqual(res_sub["status"], "success")
		template_sub = res_sub["data"]["template"]
		self.assertEqual(template_sub["recipient_type"], "Submitter")
		self.assertIsNone(template_sub["role_level"])
		self.assertEqual(template_sub["recipient_id"], "submitter")
