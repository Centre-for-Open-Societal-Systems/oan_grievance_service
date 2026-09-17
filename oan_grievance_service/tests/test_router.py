"""Tests for the Werkzeug REST Router in OAN Grievance Service."""

import json
import unittest

import frappe
from oan_auth_service.api.utils import _resolve_version_meta
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request, Response

from oan_grievance_service.api.router import ensure_routes_registered
from oan_grievance_service.api.v1 import administrative_area, grievance, profile, submitter
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

		if not frappe.db.exists("Grievance Type", "Fertilizer Shortage"):
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Fertilizer Shortage",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		self.area_name = a_leaf_area()
		self.area = frappe.get_doc("Grievance Administrative Area", self.area_name)

		# Setup test farmer user & profile
		farmer_email = "rest_test_farmer@example.com"
		if not frappe.db.exists("User", farmer_email):
			self.farmer_user = frappe.get_doc(
				{
					"doctype": "User",
					"email": farmer_email,
					"first_name": "REST Farmer",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer_user = frappe.get_doc("User", farmer_email)

		profile_name = frappe.db.get_value(
			"Grievance Submitter Profile", {"user": self.farmer_user.name}, "name"
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

	def test_version_meta_isolation(self):
		"""Verify that version_meta resolves from oan_grievance_service and not oan_auth_service."""
		meta_grv = _resolve_version_meta(grievance.submit)
		self.assertEqual(meta_grv["api_version"], "v1")
		self.assertEqual(meta_grv["status"], "current")

		meta_sub = _resolve_version_meta(submitter.me)
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
		self.assertEqual(grv_profile["full_name"], "REST Test Submitter")
		self.assertEqual(grv_profile["type"], "Individual Farmer")

		# 2. Test Staff / Admin profile retrieval
		frappe.set_user("Administrator")
		req_admin = make_test_request("/api/v1/auth/me", method="GET")
		res_admin = frappe.api.handle(req_admin)
		self.assertEqual(res_admin.status_code, 200)
		data_admin = json.loads(res_admin.get_data(as_text=True))
		self.assertEqual(data_admin["status"], "success")
		self.assertIn("profiles", data_admin["data"])
		self.assertIn("grievance", data_admin["data"]["profiles"])
		admin_grv = data_admin["data"]["profiles"]["grievance"]
		self.assertEqual(admin_grv["type"], "Admin")
		self.assertEqual(admin_grv["profile_id"], "Administrator")

	def test_deprecated_submitters_me_endpoint(self):
		"""Test GET /api/v1/submitters/me backward compatibility."""
		import frappe.api

		frappe.set_user(self.farmer_user.name)
		req = make_test_request("/api/v1/submitters/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertEqual(data["data"]["profile_id"], self.farmer_profile.name)
		self.assertEqual(data["data"]["full_name"], "REST Test Submitter")
		self.assertEqual(data["data"]["type"], "Individual Farmer")
		self.assertEqual(data["data"]["submitter_name"], "REST Test Submitter")
		self.assertEqual(data["data"]["submitter_type"], "Individual Farmer")

	def test_grievance_submission_tracking_and_timeline_rest_flow(self):
		"""Test complete REST workflow: submit, track, add note, and timeline."""
		import frappe.api

		# 1. Submit a grievance via POST /api/v1/grievances
		frappe.set_user(self.farmer_user.name)
		submit_payload = {
			"submission_channel": "Mobile App",
			"administrative_area": self.area.name,
			"service_category": "Inputs",
			"grievance_type": "Fertilizer Shortage",
			"description": "REST API submission test: severe shortage in sector 4.",
			"consent_given": 1,
		}
		req_submit = make_test_request("/api/v1/grievances", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		self.assertEqual(res_submit.status_code, 200)
		submit_data = json.loads(res_submit.get_data(as_text=True))
		self.assertEqual(submit_data["status"], "success")
		ticket_number = submit_data["data"]["ticket_number"]
		self.assertTrue(bool(ticket_number))

		# 2. Track status via GET /api/v1/grievances/<ticket_number>
		req_track = make_test_request(f"/api/v1/grievances/{ticket_number}", method="GET")
		res_track = frappe.api.handle(req_track)
		self.assertEqual(res_track.status_code, 200)
		track_data = json.loads(res_track.get_data(as_text=True))
		self.assertEqual(track_data["data"]["ticket_number"], ticket_number)

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
