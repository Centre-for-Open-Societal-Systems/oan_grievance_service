# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Regression tests verifying fixes for PR #19 review comments."""

import random
from pathlib import Path

import frappe
import yaml
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from oan_grievance_service.api.v1 import draft, grievance
from oan_grievance_service.permissions import grievance_query_conditions, has_grievance_permission
from oan_grievance_service.tests.fixtures import (
	a_department,
	a_grievance_type,
	a_leaf_area,
	a_service_category,
)


def _make_submitter_profile(name="Test Submitter"):
	digits = "".join(random.choices("0123456789", k=7))
	p = frappe.get_doc(
		{
			"doctype": "Grievance Submitter Profile",
			"submitter_name": name,
			"contact_mobile": f"+25191{digits}",
			"submitter_type": "Individual Farmer",
			"active": 1,
		}
	).insert(ignore_permissions=True)
	return p


class TestPR19ReviewFixes(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def test_issue2_purge_expired_drafts_scheduled_daily(self):
		"""Issue 2: purge_expired_drafts must be registered under hooks.py scheduler_events.daily."""
		import oan_grievance_service.hooks as hooks

		daily_tasks = hooks.scheduler_events.get("daily", [])
		self.assertIn("oan_grievance_service.tasks.purge_expired_drafts", daily_tasks)

	def test_issue3_client_uuid_alias_accepted(self):
		"""Issue 3: client_uuid accepted as alias in SaveDraftRequest and save()."""
		key = frappe.generate_hash(length=16)
		res = draft.save(client_uuid=key, description="Initial draft description")
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["client_submission_uuid"], key)

	def test_issue4_kong_cors_methods_and_jwt_secret(self):
		"""Issue 4: kong.yml CORS must not pair wildcard origin with credentials, must allow DELETE, and drop dummy JWT secret."""
		kong_path = Path(__file__).resolve().parent.parent.parent / "kong" / "kong.yml"
		self.assertTrue(kong_path.exists())
		with open(kong_path) as f:
			conf = yaml.safe_load(f)

		# Check CORS on service plugins
		cors_plugin = next(p for p in conf["services"][0]["plugins"] if p["name"] == "cors")
		self.assertIn("DELETE", cors_plugin["config"]["methods"])
		if "*" in cors_plugin["config"]["origins"]:
			self.assertFalse(cors_plugin["config"].get("credentials", False))

		# Check consumers do not carry dummy secret
		for c in conf.get("consumers", []):
			for sec in c.get("jwt_secrets", []):
				self.assertNotEqual(sec.get("secret"), "REPLACE_WITH_OAN_AUTH_JWT_SECRET")

	def test_issue5_attachment_routes_present_in_spec_and_kong(self):
		"""Issue 5: Attachment endpoints must be present in openapi and kong."""
		spec_path = Path(__file__).resolve().parent.parent.parent / "openapi" / "openapi_v1.public.yaml"
		self.assertTrue(spec_path.exists())
		with open(spec_path) as f:
			spec = yaml.safe_load(f)

		paths = spec["paths"]
		self.assertIn("/api/v1/grievances/{ticket_number}/attachments", paths)
		self.assertIn("/api/v1/attachments/{attachment_id}/download", paths)
		self.assertIn("/api/v1/attachments/{attachment_id}", paths)

	def test_issue6_draft_field_clearing_and_anonymity_preservation(self):
		"""Issue 6: Empty strings can clear fields and omitting is_anonymous preserves current value."""
		key = frappe.generate_hash(length=16)
		# Step 1: Save with anonymous=1 and description
		res1 = draft.save(client_submission_uuid=key, is_anonymous=1, description="Something to clear")
		self.assertEqual(res1["data"]["is_anonymous"], 1)
		self.assertEqual(res1["data"]["description"], "Something to clear")

		# Step 2: Save with empty string description and omitted is_anonymous
		res2 = draft.save(client_submission_uuid=key, description="")
		self.assertEqual(res2["data"]["description"], "")
		self.assertEqual(res2["data"]["is_anonymous"], 1, "Omitted is_anonymous must not reset to 0")

	def test_issue7_draft_does_not_consume_real_ticket_number(self):
		"""Issue 7: Drafts with area and category must not consume real ticket numbers."""
		key = frappe.generate_hash(length=16)
		area = a_leaf_area()
		cat = a_service_category()

		res = draft.save(
			client_submission_uuid=key,
			administrative_area=area,
			service_category=cat,
			description="Draft test",
		)
		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["name"].startswith("DRAFT-"))
		self.assertIsNone(res["data"]["ticket_number"])

	def test_issue8_detect_duplicates_excludes_drafts(self):
		"""Issue 8: detect_duplicates must not count existing drafts as duplicates."""
		sub = _make_submitter_profile()
		g_type = a_grievance_type()

		# Create a draft with this submitter and grievance type
		draft_key = frappe.generate_hash(length=16)
		draft_doc = frappe.get_doc(
			{
				"doctype": "Grievance",
				"name": f"DRAFT-{draft_key}",
				"client_submission_uuid": draft_key,
				"submitter": sub.name,
				"grievance_type": g_type,
				"workflow_state": "Draft",
				"status": "Draft",
				"docstatus": 0,
			}
		)
		draft_doc.flags.is_draft_wizard = True
		draft_doc.flags.ignore_mandatory = True
		draft_doc.insert(ignore_permissions=True)

		# Create target submitted grievance
		target_doc = frappe.get_doc(
			{
				"doctype": "Grievance",
				"administrative_area": a_leaf_area(),
				"service_category": a_service_category(),
				"submitter": sub.name,
				"grievance_type": g_type,
				"workflow_state": "Submitted",
				"status": "Submitted",
				"docstatus": 1,
			}
		)
		target_doc.flags.ignore_mandatory = True
		target_doc.insert(ignore_permissions=True)

		duplicates = grievance.detect_duplicates(target_doc)
		# The draft must NOT be flagged as duplicate
		self.assertEqual(len(duplicates), 0)

	def test_issue10_confirm_resolution_transitions_only_to_resolved(self):
		"""Issue 10: Confirm Resolution must transition to Resolved and not auto-close immediately."""
		from oan_grievance_service.tests.fixtures import a_grievance

		# Setup grievance in Pending Submitter
		g = a_grievance(workflow_state="Pending Submitter", status="Pending Submitter")

		# Action Confirm Resolution
		res = grievance.action(g.ticket_number or g.name, action="Confirm Resolution", rating=5)
		self.assertEqual(res["status"], "success")
		g.reload()
		self.assertEqual(g.status, "Resolved", "Case must remain in Resolved state, not Closed")

	def test_issue11_empty_officer_scope_matches_no_cases(self):
		"""Issue 11: An officer with empty scope assignment must not match all grievances."""
		# Create an officer user with an empty assignment
		officer_email = f"empty.scope.{frappe.generate_hash(length=8)}@example.com"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": officer_email,
				"first_name": "EmptyScope",
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)

		parent_assignment = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"assignment_name": f"Empty Scope {frappe.generate_hash(length=6)}",
				"active": 1,
				"effective_from": frappe.utils.today(),
				"administrative_area_scope": None,
				"department_scope": None,
				"category_scope": None,
				"officers": [
					{
						"user": user.name,
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		)
		parent_assignment.flags.ignore_mandatory = True
		parent_assignment.insert(ignore_permissions=True)

		cond = grievance_query_conditions(user.name)
		# Must only match cases explicitly assigned to the officer, NEVER an open un-scoped wildcard
		self.assertNotIn(
			"workflow_state != 'Draft')",
			cond.replace(
				f"`tabGrievance`.assigned_to in ('{user.name}') and `tabGrievance`.workflow_state != 'Draft'",
				"",
			),
		)

		# A non-assigned case in another area must return False for this officer
		from oan_grievance_service.tests.fixtures import a_grievance

		other_case = a_grievance(
			assigned_to="Administrator", workflow_state="In Progress", status="In Progress"
		)
		self.assertFalse(has_grievance_permission(other_case, ptype="read", user=user.name))

	def test_issue1_actions_and_notes_require_write_permission(self):
		"""Issue 1: Officers cannot act on or add notes to cases assigned to other officers."""
		from oan_grievance_service.tests.fixtures import a_grievance

		# Create Officer A and Officer B
		officer_a_email = f"officer.a.{frappe.generate_hash(length=6)}@example.com"
		officer_b_email = f"officer.b.{frappe.generate_hash(length=6)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": officer_a_email,
				"first_name": "OfficerA",
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "User",
				"email": officer_b_email,
				"first_name": "OfficerB",
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)

		# Case assigned to Officer A
		case = a_grievance(assigned_to=officer_a_email, workflow_state="In Progress", status="In Progress")
		frappe.db.commit()

		# Officer B attempts to add note
		frappe.set_user(officer_b_email)
		res_note = grievance.add_note(case.ticket_number or case.name, body="Unauthorized officer note")
		self.assertEqual(res_note["status"], "error")
		self.assertEqual(res_note["code"], "PERMISSION_DENIED")

		# Officer B attempts to post message
		res_msg = grievance.message(case.ticket_number or case.name, body="Unauthorized officer message")
		self.assertEqual(res_msg["status"], "error")
		self.assertEqual(res_msg["code"], "PERMISSION_DENIED")

		# Officer B attempts to execute workflow action
		res_act = grievance.action(
			case.ticket_number or case.name, action="Request More Info", reason="Need info"
		)
		self.assertEqual(res_act["status"], "error")
		self.assertEqual(res_act["code"], "PERMISSION_DENIED")

		# Assigned Officer A can add note
		frappe.set_user(officer_a_email)
		note_res = grievance.add_note(case.ticket_number or case.name, body="Assigned officer note")
		self.assertEqual(note_res["status"], "success")

	def test_issue9_anonymity_masked_in_list_and_timeline(self):
		"""Issue 9: List and timeline mask name, phone, and email for anonymous grievances when viewed by officers."""
		from oan_grievance_service.tests.fixtures import a_grievance

		officer_email = f"officer.view.{frappe.generate_hash(length=6)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": officer_email,
				"first_name": "OfficerView",
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)

		anon_case = a_grievance(
			is_anonymous=1,
			submitter_name="Secret Citizen",
			contact_mobile="+251911998877",
			contact_email="secret@example.com",
			assigned_to=officer_email,
			workflow_state="In Progress",
			status="In Progress",
		)

		frappe.set_user(officer_email)
		# Timeline test
		tl_res = grievance.timeline(anon_case.ticket_number or anon_case.name)
		self.assertEqual(tl_res["status"], "success")
		self.assertEqual(tl_res["data"]["submitter_name"], "Anonymous Submitter")
		self.assertIsNone(tl_res["data"]["submitter"]["mobile"])
		self.assertIsNone(tl_res["data"]["submitter"]["email"])

		# List test
		list_res = grievance.list_grievances()
		self.assertEqual(list_res["status"], "success")
		found = next((item for item in list_res["data"]["items"] if item["name"] == anon_case.name), None)
		self.assertIsNotNone(found)
		self.assertEqual(found["submitter_name"], "Anonymous Submitter")
		self.assertIsNone(found["contact_mobile"])
		self.assertIsNone(found["contact_email"])
