"""Tests for the Werkzeug REST Router in OAN Grievance Service."""

import json
import unittest

import frappe
from oan_auth_service.api.utils import _resolve_version_meta
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request, Response

from oan_grievance_service.api.router import ensure_routes_registered
from oan_grievance_service.api.v1 import administrative_area, draft, grievance, profile, submitter
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.tests.fixtures import a_leaf_area


def make_test_request(
	path: str,
	method: str = "GET",
	data: dict | None = None,
	headers: dict | None = None,
	scheme: str = "http",
	environ_base: dict | None = None,
) -> Request:
	"""Helper to construct a Werkzeug Request and set up frappe.local state."""
	builder_kwargs = {
		"path": path,
		"method": method.upper(),
		"base_url": f"{scheme}://testsite.localhost",
		"headers": headers or {},
	}
	if data is not None:
		builder_kwargs["json"] = data

	builder = EnvironBuilder(**builder_kwargs)
	env = builder.get_environ()
	if environ_base:
		env.update(environ_base)
	req = Request(env)

	frappe.local.request = req
	frappe.local.request_ip = "127.0.0.1"
	frappe.local.form_dict = frappe._dict(data or {})
	frappe.local.response = frappe._dict({})

	return req


class TestGrievanceRESTRouter(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		ensure_routes_registered()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

		# Ensure required roles exist
		for role in ("Grievance Submitter", "Grievance Officer", "Grievance Admin"):
			if not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
			frappe.get_doc(
				{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "code": "IND"}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Service Category", "Inputs"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Inputs",
					"code": "001",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Grievance Service Category", "Inputs", "code", "001")

		if not frappe.db.exists("Grievance Type", {"type_name": "Fertilizer Shortage"}):
			self.gtype = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Fertilizer Shortage",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			gtype_name = frappe.db.get_value("Grievance Type", {"type_name": "Fertilizer Shortage"}, "name")
			self.gtype = frappe.get_doc("Grievance Type", gtype_name)

		self.area = a_leaf_area()

		# Create a test farmer user and profile
		self.farmer_user = frappe.db.get_value("User", {"email": "rest_farmer@test.org"}, "*")
		if not self.farmer_user:
			self.farmer_user = frappe.get_doc(
				{
					"doctype": "User",
					"email": "rest_farmer@test.org",
					"first_name": "REST Farmer",
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer_user = frappe.get_doc("User", self.farmer_user.name)

		profile_name = frappe.db.get_value(
			"Grievance Submitter Profile",
			{"user": self.farmer_user.name},
			"name",
		) or frappe.db.get_value(
			"Grievance Submitter Profile",
			{"dedupe_key": "phone:+251911998877"},
			"name",
		)
		if not profile_name:
			self.farmer_profile = frappe.get_doc(
				{
					"doctype": "Grievance Submitter Profile",
					"submitter_type": "Individual Farmer",
					"submitter_name": "REST Test Submitter",
					"contact_mobile": "+251911998877",
					"user": self.farmer_user.name,
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer_profile = frappe.get_doc("Grievance Submitter Profile", profile_name)
			if self.farmer_profile.user != self.farmer_user.name:
				self.farmer_profile.user = self.farmer_user.name
				self.farmer_profile.save(ignore_permissions=True)

	def test_version_meta_isolation(self):
		"""Verify that version_meta resolves from oan_grievance_service and not oan_auth_service."""
		meta_grv = _resolve_version_meta(draft.submit_draft)
		self.assertEqual(meta_grv["api_version"], "v1")
		self.assertEqual(meta_grv["status"], "current")

		meta_sub = _resolve_version_meta(submitter.options)
		self.assertEqual(meta_sub["api_version"], "v1")

		meta_area = _resolve_version_meta(administrative_area.get_areas)
		self.assertEqual(meta_area["api_version"], "v1")

	def test_public_health_and_ping_endpoints(self):
		"""Test GET /api/v1/grievances/health and /ping."""
		import frappe.api

		req = make_test_request("/api/v1/grievances/health", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertEqual(data["data"]["service"], "oan_grievance_service")
		self.assertEqual(data["data"]["status"], "healthy")

		req_ping = make_test_request("/api/v1/grievances/ping", method="GET")
		res_ping = frappe.api.handle(req_ping)
		self.assertEqual(res_ping.status_code, 200)
		data_ping = json.loads(res_ping.get_data(as_text=True))
		self.assertEqual(data_ping["data"]["ping"], "pong")

	def test_public_submitter_options_endpoint(self):
		"""Test GET /api/v1/submitters/options is accessible as guest."""
		import frappe.api

		req = make_test_request("/api/v1/submitters/options", method="GET")
		frappe.set_user("Guest")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("submitter_types", data["data"])
		self.assertIn("identity_schemes", data["data"])
		self.assertIn("submission_types", data["data"])

	def test_public_administrative_area_endpoints(self):
		"""Test GET /api/v1/administrative-areas."""
		import frappe.api

		req = make_test_request("/api/v1/administrative-areas", method="GET")
		frappe.set_user("Guest")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("areas", data["data"])

	def test_grievance_options_endpoint(self):
		"""Test GET /api/v1/grievances/options."""
		import frappe.api

		frappe.set_user("Administrator")
		req = make_test_request("/api/v1/grievances/options", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("departments", data["data"])
		self.assertIn("statuses", data["data"])
		self.assertIn("service_categories", data["data"])

	def test_auth_me_returns_namespaced_grievance_profile(self):
		"""Test GET /api/v1/auth/me enriches data.profiles.grievance via on_user_profile hook."""
		import frappe.api

		# 1. Test Submitter profile retrieval
		frappe.set_user(self.farmer_user.name)
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("profiles", data["data"])
		self.assertIn("grievance", data["data"]["profiles"])
		grv_profile = data["data"]["profiles"]["grievance"]
		self.assertEqual(grv_profile["profile_id"], self.farmer_profile.name)
		self.assertEqual(grv_profile["type"], "Individual Farmer")
		self.assertIsInstance(grv_profile["identities"], list)

		# 2. Test Admin without officer scope or submitter profile returns no grievance profile
		frappe.set_user("Administrator")
		req_admin = make_test_request("/api/v1/auth/me", method="GET")
		res_admin = frappe.api.handle(req_admin)
		self.assertEqual(res_admin.status_code, 200)
		data_admin = json.loads(res_admin.get_data(as_text=True))
		self.assertEqual(data_admin["status"], "success")
		self.assertNotIn("grievance", data_admin["data"].get("profiles", {}))

	def test_grievance_submission_tracking_and_timeline_rest_flow(self):
		"""Test complete REST workflow: submit, track, add note, and timeline."""
		import uuid

		import frappe.api

		# 1. Save and submit a grievance draft via REST
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "REST API submission test: severe shortage in sector 4.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		res_save = frappe.api.handle(req_save)
		self.assertEqual(res_save.status_code, 200)

		submit_payload = {
			"client_submission_uuid": draft_uuid,
			"consent_given": 1,
		}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		self.assertEqual(res_submit.status_code, 200)
		submit_data = json.loads(res_submit.get_data(as_text=True))
		self.assertEqual(submit_data["status"], "success")
		ticket_number = submit_data["data"]["ticket_number"]
		self.assertTrue(bool(ticket_number))

		# 2. Track status and attachments via GET /api/v1/grievances/<ticket_number>/timeline
		req_tl = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl = frappe.api.handle(req_tl)
		self.assertEqual(res_tl.status_code, 200)
		tl_data = json.loads(res_tl.get_data(as_text=True))
		self.assertEqual(tl_data["data"]["ticket_number"], tn.display(ticket_number))
		self.assertIn("attachments", tl_data["data"])

		# 3. Add note as Officer via POST /api/v1/grievances/<ticket_number>/note
		frappe.set_user("Administrator")
		req_note = make_test_request(
			f"/api/v1/grievances/{ticket_number}/note",
			method="POST",
			data={"body": "Officer reviewing case via REST", "is_internal": True},
		)
		res_note = frappe.api.handle(req_note)
		self.assertEqual(res_note.status_code, 200)
		note_data = json.loads(res_note.get_data(as_text=True))
		self.assertEqual(note_data["status"], "success")

		# 4. View Timeline via GET /api/v1/grievances/<ticket_number>/timeline
		req_tl = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl = frappe.api.handle(req_tl)
		self.assertEqual(res_tl.status_code, 200)
		tl_data = json.loads(res_tl.get_data(as_text=True))
		self.assertEqual(tl_data["status"], "success")
		self.assertTrue(len(tl_data["data"]["timeline"]) > 0)

	def test_unified_action_rest_endpoint(self):
		"""Test POST /api/v1/grievances/<ticket_number>/action for workflow moves."""
		import uuid

		import frappe.api

		from oan_grievance_service.services import lifecycle

		# 1. Save and submit a grievance draft via REST
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "Unified action endpoint test with minimum length.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		res_save = frappe.api.handle(req_save)
		self.assertEqual(res_save.status_code, 200)

		submit_payload = {
			"client_submission_uuid": draft_uuid,
			"consent_given": 1,
		}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		ticket_number = json.loads(res_submit.get_data(as_text=True))["data"]["ticket_number"]
		frappe.db.commit()

		# 2. As submitter, check timeline returns available_actions
		req_tl = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl = frappe.api.handle(req_tl)
		tl_data = json.loads(res_tl.get_data(as_text=True))["data"]
		self.assertIn("available_actions", tl_data)

		# 3. Assign & Start Work
		# 3. Assign & Start Work
		frappe.set_user("Administrator")
		from oan_grievance_service.tests.fixtures import a_department

		doc = frappe.get_doc("Grievance", {"ticket_number": ticket_number})
		doc.assigned_dept = a_department()
		doc.save(ignore_permissions=True)
		lifecycle.transition(doc, "Assign")
		frappe.db.commit()

		# Execute Start Work via unified action endpoint
		req_action = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={"action": "Start Work"},
		)
		res_action = frappe.api.handle(req_action)
		self.assertEqual(res_action.status_code, 200)
		action_data = json.loads(res_action.get_data(as_text=True))
		self.assertEqual(action_data["status"], "success")
		self.assertEqual(action_data["data"]["status"], "In Progress")
		frappe.db.commit()

		# 4. Reject without reason is refused (400)
		req_rej_bad = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={"action": "Reject", "reason": ""},
		)
		res_rej_bad = frappe.api.handle(req_rej_bad)
		body_rej_bad = json.loads(res_rej_bad.get_data(as_text=True))
		self.assertEqual(body_rej_bad["status"], "error")
		self.assertIn("reason is required", body_rej_bad["message"].lower())

		# 5. Reject with reason succeeds
		req_rej_ok = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={"action": "Reject", "reason": "Not an agricultural grievance."},
		)
		res_rej_ok = frappe.api.handle(req_rej_ok)
		self.assertEqual(res_rej_ok.status_code, 200)
		action_rej = json.loads(res_rej_ok.get_data(as_text=True))
		self.assertEqual(action_rej["data"]["status"], "Rejected")

	def test_reassign_defer_and_anonymity_endpoints(self):
		"""Test direct REST APIs for reassignment, deferral, and anonymity decisions."""
		import uuid

		import frappe.api

		from oan_grievance_service.tests.fixtures import a_department

		# 1. Submit a grievance
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "Testing reassign and deferral direct endpoints.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		frappe.api.handle(req_save)

		submit_payload = {"client_submission_uuid": draft_uuid, "consent_given": 1}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		ticket_number = json.loads(res_submit.get_data(as_text=True))["data"]["ticket_number"]
		frappe.db.commit()

		# 2. Reassign endpoint
		frappe.set_user("Administrator")
		dept = a_department()
		req_reassign = make_test_request(
			f"/api/v1/grievances/{ticket_number}/reassign",
			method="POST",
			data={
				"target_department": dept,
				"sla_treatment": "Continue",
				"reason": "Routing to regional dept",
			},
		)
		res_reassign = frappe.api.handle(req_reassign)
		self.assertEqual(res_reassign.status_code, 200, res_reassign.get_data(as_text=True))
		reassign_data = json.loads(res_reassign.get_data(as_text=True))
		self.assertEqual(reassign_data["status"], "success")
		self.assertEqual(reassign_data["data"]["assigned_dept"], dept)

		# 3. Defer SLA endpoint
		req_defer = make_test_request(
			f"/api/v1/grievances/{ticket_number}/defer-sla",
			method="POST",
			data={"additional_days": 5, "reason": "Awaiting soil lab sample results"},
		)
		res_defer = frappe.api.handle(req_defer)
		self.assertEqual(res_defer.status_code, 200)
		defer_data = json.loads(res_defer.get_data(as_text=True))
		self.assertEqual(defer_data["status"], "success")
		self.assertIsNotNone(defer_data["data"]["sla_due_date"])

		# 4. Anonymity decision endpoint
		req_anon = make_test_request(
			f"/api/v1/grievances/{ticket_number}/anonymity-decision",
			method="POST",
			data={"decision": "Approved", "reason": "Sensitive whistleblowing context"},
		)
		res_anon = frappe.api.handle(req_anon)
		self.assertEqual(res_anon.status_code, 200)
		anon_data = json.loads(res_anon.get_data(as_text=True))
		self.assertEqual(anon_data["status"], "success")
		self.assertTrue(anon_data["data"]["is_anonymous"])
