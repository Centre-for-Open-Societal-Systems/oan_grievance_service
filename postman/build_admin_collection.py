#!/usr/bin/env python3
# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Generate the OAN Grievance Administration & Officer Management Postman Collection.

Covers all administrative endpoints that Grievance Admin and System Manager can call:
1. Authentication (Admin login & token refresh)
2. User Account Onboarding (/v1/admin/users - auth service)
3. Officer Onboarding & Management (/v1/officers - grievance service)
4. Desk & Category Assignments (/v1/category-assignments)
5. SLA Policy & Configurations (/v1/sla-*)
6. Response Templates (/v1/response-templates)
7. Notification Templates (/v1/notification-templates)
8. Taxonomy & Master Data (/v1/service-categories & /v1/grievance-types)
"""

import json
from pathlib import Path


def make_test_script(checks: list[str], post_extract: list[str] | None = None) -> list[dict]:
	lines = []
	for check in checks:
		lines.append(check)
	if post_extract:
		lines.append("")
		lines.append("if (pm.response.code >= 200 && pm.response.code < 300) {")
		lines.append("    var res = pm.response.json();")
		lines.append("    var envelope = res.message || res;")
		lines.append("    var data = envelope.data || envelope;")
		for ext in post_extract:
			lines.append(f"    {ext}")
		lines.append("}")
	return [
		{
			"listen": "test",
			"script": {
				"exec": lines,
				"type": "text/javascript",
			},
		}
	]


def make_request(
	name: str,
	method: str,
	path_segments: list[str],
	description: str,
	query_params: list[dict] | None = None,
	body: dict | None = None,
	test_checks: list[str] | None = None,
	post_extract: list[str] | None = None,
	auth_override: dict | None = None,
) -> dict:
	url_raw = "{{base_url}}/" + "/".join(path_segments)
	if query_params:
		qp_str = "&".join(f"{qp['key']}={qp['value']}" for qp in query_params if not qp.get("disabled"))
		if qp_str:
			url_raw += f"?{qp_str}"

	headers = []
	req_body = None
	if body is not None:
		headers.append({"key": "Content-Type", "value": "application/json"})
		req_body = {
			"mode": "raw",
			"raw": json.dumps(body, indent=2),
		}

	req_obj = {
		"method": method,
		"header": headers,
		"url": {
			"raw": url_raw,
			"host": ["{{base_url}}"],
			"path": path_segments,
		},
		"description": description,
	}
	if query_params:
		req_obj["url"]["query"] = query_params
	if req_body:
		req_obj["body"] = req_body
	if auth_override:
		req_obj["auth"] = auth_override

	default_checks = [
		'pm.test("Status code is 2xx", function () {',
		"    pm.response.to.be.success;",
		"});",
		'pm.test("Response status is success", function () {',
		"    var res = pm.response.json();",
		"    pm.expect(res.status).to.eql('success');",
		"});",
	]
	events = make_test_script(test_checks or default_checks, post_extract)

	return {
		"name": name,
		"event": events,
		"request": req_obj,
		"response": [],
	}


def build_collection() -> dict:
	collection = {
		"info": {
			"_postman_id": "a9c7b12d-e4f8-4b9a-8a12-c5d9e7f12345",
			"name": "OAN Grievance - Administration & Officer Management",
			"description": (
				"End-to-end administration workflows for **Grievance Admin** and **System Manager**.\n\n"
				"### Core Capabilities Covered:\n"
				"1. **Admin Authentication**: Sign in and refresh tokens.\n"
				"2. **User Identity Onboarding (`/v1/admin/users`)**: Create user accounts, assign roles (`Grievance Officer`, `Grievance Review Officer`), retrieve user details, and issue temporary passwords.\n"
				"3. **Officer Onboarding & Management (`/v1/officers`)**: Place officers on category desks as Nodal Officers (L1), Senior Officers (L2), Department Heads (L3), or Reviewers. Filter officers by level, department, status, category, and region; fetch status counts, manage leave/status, re-issue passwords, and inspect performance statistics.\n"
				"4. **Category Desk Routing (`/v1/category-assignments`)**: Configure primary and secondary routing desks, SLA days, and auto-escalation.\n"
				"5. **SLA Policies & Configuration (`/v1/sla-*`)**: Manage the global deferral ceiling and per-category SLA timers.\n"
				"6. **Response Templates (`/v1/response-templates`)**: Manage pre-written investigation templates for officers.\n"
				"7. **Notification Templates (`/v1/notification-templates`)**: Manage automated event notification templates and review placeholder dictionaries.\n"
				"8. **Taxonomy & Master Data (`/v1/service-categories`, `/v1/grievance-types`)**: Manage categories and grievance types.\n\n"
				"### Running in Newman / CI:\n"
				"```bash\n"
				"newman run postman/oan_grievance_admin_collection.json -e postman/local.postman_environment.json\n"
				"```"
			),
			"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
		},
		"auth": {
			"type": "bearer",
			"bearer": [
				{
					"key": "token",
					"value": "{{access_token}}",
					"type": "string",
				}
			],
		},
		"variable": [
			{"key": "base_url", "value": "http://localhost:8000", "type": "default"},
			{"key": "admin_email", "value": "administrator", "type": "default"},
			{"key": "admin_password", "value": "admin", "type": "secret"},
			{"key": "access_token", "value": "", "type": "secret"},
			{"key": "refresh_token", "value": "", "type": "secret"},
			{"key": "officer_email", "value": "newman.l1.officer@example.com", "type": "default"},
			{"key": "officer_name", "value": "Tigist Alemu", "type": "default"},
			{"key": "officer_phone", "value": "+251911123456", "type": "default"},
			{"key": "officer_designation", "value": "Nodal Officer", "type": "default"},
			{"key": "supervisor_email", "value": "newman.l2.officer@example.com", "type": "default"},
			{"key": "supervisor_name", "value": "Yonas Mekonnen", "type": "default"},
			{"key": "dept_head_email", "value": "head.moa@example.com", "type": "default"},
			{"key": "dept_head_name", "value": "Dr. Fikru Tadesse", "type": "default"},
			{"key": "reviewer_email", "value": "reviewer.moa@example.com", "type": "default"},
			{"key": "reviewer_name", "value": "Almaz Bekele", "type": "default"},
			{"key": "department", "value": "MoA", "type": "default"},
			{"key": "service_category", "value": "Inputs", "type": "default"},
			{"key": "grievance_type", "value": "Fertilizer Shortage", "type": "default"},
			{"key": "region", "value": "Oromia", "type": "default"},
			{"key": "officer_temp_password", "value": "", "type": "secret"},
			{"key": "category_assignment_id", "value": "", "type": "default"},
			{"key": "response_template_id", "value": "", "type": "default"},
			{"key": "notif_template_id", "value": "", "type": "default"},
		],
		"item": [],
	}

	# -------------------------------------------------------------
	# Folder 0: Admin Authentication
	# -------------------------------------------------------------
	auth_items = [
		make_request(
			name="1. Admin Login",
			method="POST",
			path_segments=["v1", "auth", "login"],
			description=(
				"Authenticates admin credentials (`usr` and `pwd`) and stores the returned Bearer "
				+ "`access_token` and `refresh_token` in collection and environment variables."
			),
			body={
				"usr": "{{admin_email}}",
				"pwd": "{{admin_password}}",
				"remember_me": False,
			},
			auth_override={"type": "noauth"},
			test_checks=[
				'pm.test("Login successful with 200 OK", function () {',
				"    pm.response.to.have.status(200);",
				"});",
				'pm.test("Access token received", function () {',
				"    var res = pm.response.json();",
				"    var d = res.data || res.message || res;",
				"    pm.expect(d.access_token).to.be.a('string').and.not.empty;",
				"});",
			],
			post_extract=[
				'if (data && data.access_token) pm.collectionVariables.set("access_token", data.access_token);',
				'if (data && data.refresh_token) pm.collectionVariables.set("refresh_token", data.refresh_token);',
				"if (pm.environment && pm.environment.name && data) {",
				'    if (data.access_token) pm.environment.set("access_token", data.access_token);',
				'    if (data.refresh_token) pm.environment.set("refresh_token", data.refresh_token);',
				"}",
			],
		),
		make_request(
			name="2. Refresh Token",
			method="POST",
			path_segments=["v1", "auth", "refresh"],
			description="Exchanges the single-use refresh token for a fresh access and refresh token pair.",
			body={"refresh_token": "{{refresh_token}}"},
			auth_override={"type": "noauth"},
			post_extract=[
				'if (data && data.access_token) pm.collectionVariables.set("access_token", data.access_token);',
				'if (data && data.refresh_token) pm.collectionVariables.set("refresh_token", data.refresh_token);',
			],
		),
	]
	collection["item"].append({"name": "0. Admin Authentication", "item": auth_items})

	# -------------------------------------------------------------
	# Folder 1: User Account Onboarding (/v1/admin/users)
	# -------------------------------------------------------------
	user_admin_items = [
		make_request(
			name="1. List System Users",
			method="GET",
			path_segments=["v1", "admin", "users"],
			query_params=[
				{"key": "role", "value": "Grievance Officer", "description": "Filter by assigned role"},
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List user accounts holding a service role. Supports search, email, role, and enabled filters.",
		),
		make_request(
			name="2. Onboard User Account (L2 Senior Officer)",
			method="POST",
			path_segments=["v1", "admin", "users"],
			description=(
				"Create a user account in the auth service for a Senior Nodal Officer (L2). "
				+ "A temporary password is generated and returned once in the response."
			),
			body={
				"full_name": "{{supervisor_name}}",
				"email": "{{supervisor_email}}",
				"phone_number": "+251911223344",
				"roles": ["Grievance Officer"],
			},
			test_checks=[
				'pm.test("Status is 201 or 200", function () {',
				"    pm.expect(pm.response.code).to.be.oneOf([200, 201]);",
				"});",
			],
			post_extract=[
				'if (data && data.temporary_password) pm.collectionVariables.set("officer_temp_password", data.temporary_password);',
			],
		),
		make_request(
			name="3. Onboard User Account (L1 Nodal Officer)",
			method="POST",
			path_segments=["v1", "admin", "users"],
			description="Create a user account in the auth service for a Nodal Officer (L1) with role Grievance Officer.",
			body={
				"full_name": "{{officer_name}}",
				"email": "{{officer_email}}",
				"phone_number": "{{officer_phone}}",
				"roles": ["Grievance Officer"],
			},
			test_checks=[
				'pm.test("Status is 201 or 200", function () {',
				"    pm.expect(pm.response.code).to.be.oneOf([200, 201]);",
				"});",
			],
			post_extract=[
				'if (data && data.temporary_password) pm.collectionVariables.set("officer_temp_password", data.temporary_password);',
			],
		),
		make_request(
			name="4. Onboard User Account (L3 Department Head)",
			method="POST",
			path_segments=["v1", "admin", "users"],
			description="Create a user account for a Department Head (L3) with roles Grievance Officer and Grievance Desk Head.",
			body={
				"full_name": "{{dept_head_name}}",
				"email": "{{dept_head_email}}",
				"phone_number": "+251911556677",
				"roles": ["Grievance Officer", "Grievance Desk Head"],
			},
			test_checks=[
				'pm.test("Status is 201 or 200", function () {',
				"    pm.expect(pm.response.code).to.be.oneOf([200, 201]);",
				"});",
			],
		),
		make_request(
			name="5. Onboard User Account (Reviewer)",
			method="POST",
			path_segments=["v1", "admin", "users"],
			description="Create a user account for an internal Reviewer holding Grievance Review Officer (read-only oversight).",
			body={
				"full_name": "{{reviewer_name}}",
				"email": "{{reviewer_email}}",
				"phone_number": "+251911889900",
				"roles": ["Grievance Review Officer"],
			},
			test_checks=[
				'pm.test("Status is 201 or 200", function () {',
				"    pm.expect(pm.response.code).to.be.oneOf([200, 201]);",
				"});",
			],
		),
		make_request(
			name="6. Get User Details",
			method="GET",
			path_segments=["v1", "admin", "users", "{{officer_email}}"],
			description="Account details, contact info, assigned roles, and app profiles of an onboarded user.",
		),
		make_request(
			name="7. Update User Details",
			method="PATCH",
			path_segments=["v1", "admin", "users", "{{officer_email}}"],
			description="Update user account profile information (e.g. first_name, last_name, mobile).",
			body={
				"first_name": "Tigist",
				"last_name": "Alemu (Updated)",
			},
		),
		make_request(
			name="8. Get User Roles",
			method="GET",
			path_segments=["v1", "admin", "users", "{{officer_email}}", "roles"],
			description="List the service roles granted to a specific user account.",
		),
		make_request(
			name="9. Issue User Temporary Password",
			method="POST",
			path_segments=["v1", "admin", "users", "{{officer_email}}", "temporary-password"],
			description="Generate a new temporary password for the user and revoke existing sessions.",
			body={"temporary_password": "TempPass#2026"},
			post_extract=[
				'if (data && data.temporary_password) pm.collectionVariables.set("officer_temp_password", data.temporary_password);',
			],
		),
		make_request(
			name="10. List Assignable Roles",
			method="GET",
			path_segments=["v1", "admin", "roles"],
			description="List all roles within the service scope that can be granted by an administrator.",
		),
	]
	collection["item"].append(
		{"name": "1. User Account Onboarding (/v1/admin/users)", "item": user_admin_items}
	)

	# -------------------------------------------------------------
	# Folder 2: Officer Management & Desks (/v1/officers)
	# -------------------------------------------------------------
	officer_items = [
		make_request(
			name="1. Onboard Senior Nodal Officer (L2) on Desks",
			method="POST",
			path_segments=["v1", "officers"],
			description=(
				"Places an existing user account onto category desks as a Senior Nodal Officer (L2). "
				+ "L2 officers act as supervisors and the first escalation rung for Nodal Officers."
			),
			body={
				"full_name": "{{supervisor_name}}",
				"email": "{{supervisor_email}}",
				"designation": "Senior Nodal Officer",
				"level": "L2",
				"department": "{{department}}",
				"service_categories": ["{{service_category}}"],
			},
		),
		make_request(
			name="2. Onboard Nodal Officer (L1) on Desks",
			method="POST",
			path_segments=["v1", "officers"],
			description=(
				"Places an existing user account onto category desks as a Nodal Officer (L1). "
				+ "Requires level L1, department, service_categories, and an L2 supervisor (reports_to)."
			),
			body={
				"full_name": "{{officer_name}}",
				"email": "{{officer_email}}",
				"designation": "{{officer_designation}}",
				"level": "L1",
				"department": "{{department}}",
				"service_categories": ["{{service_category}}"],
				"phone": "{{officer_phone}}",
				"reports_to": "{{supervisor_email}}",
			},
			post_extract=[
				'if (data && data.officer) pm.collectionVariables.set("officer_email", data.officer.name);',
			],
		),
		make_request(
			name="3. Onboard Department Head (L3 Category Admin)",
			method="POST",
			path_segments=["v1", "officers"],
			description=(
				"Creates a Department Head (L3) officer. An L3 is the department's final escalation rung "
				+ "who approves reassignments. Only one head handles a category in a department."
			),
			body={
				"full_name": "{{dept_head_name}}",
				"email": "{{dept_head_email}}",
				"designation": "Department Head",
				"level": "L3",
				"department": "{{department}}",
				"service_categories": ["{{service_category}}"],
				"create_department": False,
			},
		),
		make_request(
			name="4. Onboard Reviewer on Category Desks",
			method="POST",
			path_segments=["v1", "officers"],
			description=(
				"Places a Reviewer on a department's desks with read-only access limited to those desks. "
				+ "A Reviewer takes role='Reviewer', has no level or supervisor, and cases are never assigned to them."
			),
			body={
				"full_name": "{{reviewer_name}}",
				"email": "{{reviewer_email}}",
				"role": "Reviewer",
				"designation": "Regional Reviewer",
				"department": "{{department}}",
				"service_categories": ["{{service_category}}"],
			},
		),
		make_request(
			name="5. List All Officers",
			method="GET",
			path_segments=["v1", "officers"],
			query_params=[
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List all officers. Paginated with page and page_size, returned newest first.",
		),
		make_request(
			name="6. List Officers (Filtered by Level & Department)",
			method="GET",
			path_segments=["v1", "officers"],
			query_params=[
				{"key": "role", "value": "Officer"},
				{"key": "level", "value": "L1"},
				{"key": "department", "value": "{{department}}"},
				{"key": "service_category", "value": "{{service_category}}"},
				{"key": "status", "value": "Active"},
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List Nodal Officers (L1) in a department and service category with Active status.",
		),
		make_request(
			name="7. List Reviewers",
			method="GET",
			path_segments=["v1", "officers"],
			query_params=[
				{"key": "role", "value": "Reviewer"},
				{"key": "department", "value": "{{department}}"},
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List Reviewer accounts. Note that Reviewers have no level and cannot be On Leave.",
		),
		make_request(
			name="8. Officer Status Counts",
			method="GET",
			path_segments=["v1", "officers", "status-counts"],
			query_params=[
				{"key": "role", "value": "Officer"},
				{"key": "department", "value": "{{department}}"},
			],
			description="Returns Active, On Leave, and Inactive totals for dashboard and administration tabs.",
			test_checks=[
				'pm.test("Status code is 200", function () { pm.response.to.have.status(200); });',
				'pm.test("Contains status count fields", function () {',
				"    var res = pm.response.json();",
				"    var d = res.data || res.message || res;",
				"    pm.expect(d).to.have.property('active');",
				"    pm.expect(d).to.have.property('on_leave');",
				"    pm.expect(d).to.have.property('inactive');",
				"    pm.expect(d).to.have.property('total');",
				"});",
			],
		),
		make_request(
			name="9. Get Officer Profile & Desk Assignments",
			method="GET",
			path_segments=["v1", "officers", "{{officer_email}}"],
			description="Inspect an officer record: contact details, designation, level, supervisor, and category desk seats.",
		),
		make_request(
			name="10. Update Officer (Designation & Phone)",
			method="PATCH",
			path_segments=["v1", "officers", "{{officer_email}}"],
			description="Update officer details: designation, phone number, and service category assignments.",
			body={
				"full_name": "{{officer_name}}",
				"designation": "Lead Nodal Officer",
				"phone": "+251911998877",
			},
		),
		make_request(
			name="11. Set Officer On Leave",
			method="PATCH",
			path_segments=["v1", "officers", "{{officer_email}}"],
			description="Marks the officer as 'On Leave'. The auto-routing engine will temporarily bypass this officer.",
			body={"status": "On Leave"},
		),
		make_request(
			name="12. Mark Officer Active (Return from Leave)",
			method="PATCH",
			path_segments=["v1", "officers", "{{officer_email}}"],
			description="Reactivates an officer returning from leave.",
			body={"status": "Active"},
		),
		make_request(
			name="13. Reissue Officer Temporary Password",
			method="POST",
			path_segments=["v1", "officers", "{{officer_email}}", "password-resets"],
			description=(
				"Reissues a temporary password to an officer. All existing sessions are terminated "
				+ "and the officer must change password on their next sign-in."
			),
			body={"temporary_password": "TempPass#2026"},
		),
		make_request(
			name="14. Officer Performance Statistics",
			method="GET",
			path_segments=["v1", "officers", "statistics"],
			query_params=[
				{"key": "level", "value": "L1"},
				{"key": "department", "value": "{{department}}"},
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="Officer KPI metrics: assigned cases, resolved count, avg resolution days, and resolution rate.",
		),
		make_request(
			name="15. Deactivate Officer (Inactive)",
			method="PATCH",
			path_segments=["v1", "officers", "{{officer_email}}"],
			description="Deactivates an officer account and retires their desk assignment rows.",
			body={"status": "Inactive"},
		),
	]
	collection["item"].append({"name": "2. Officer Management & Desks (/v1/officers)", "item": officer_items})

	# -------------------------------------------------------------
	# Folder 3: Desk & Category Assignments (/v1/category-assignments)
	# -------------------------------------------------------------
	routing_items = [
		make_request(
			name="1. List Category Desk Assignments",
			method="GET",
			path_segments=["v1", "category-assignments"],
			query_params=[
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List active and configured category-to-department routing rules.",
			post_extract=[
				'if (data && data.assignments && data.assignments.length > 0) pm.collectionVariables.set("category_assignment_id", data.assignments[0].name);',
			],
		),
		make_request(
			name="2. Create Category Assignment",
			method="POST",
			path_segments=["v1", "category-assignments"],
			description="Assigns a category and department to primary (L1) and escalation (L2) officers with SLA days.",
			body={
				"service_category": "{{service_category}}",
				"department": "{{department}}",
				"l1_officer": "{{officer_email}}",
				"l2_officer": "{{supervisor_email}}",
				"sla_days": 7,
				"auto_escalate": True,
				"active": True,
			},
			post_extract=[
				'if (data && data.assignment) pm.collectionVariables.set("category_assignment_id", data.assignment.name);',
			],
		),
		make_request(
			name="3. Get Category Assignment",
			method="GET",
			path_segments=["v1", "category-assignments", "{{category_assignment_id}}"],
			description="Retrieve detailed configuration of a single category desk assignment.",
		),
		make_request(
			name="4. Update Category Assignment",
			method="PATCH",
			path_segments=["v1", "category-assignments", "{{category_assignment_id}}"],
			description="Update SLA days or toggle auto-escalation on a category desk assignment.",
			body={
				"sla_days": 10,
				"auto_escalate": True,
			},
		),
		make_request(
			name="5. Deactivate Category Assignment",
			method="DELETE",
			path_segments=["v1", "category-assignments", "{{category_assignment_id}}"],
			description="Soft-deactivates a category desk assignment (`active = 0`). Hard deletion is forbidden.",
		),
	]
	collection["item"].append(
		{"name": "3. Category Desk Assignments (/v1/category-assignments)", "item": routing_items}
	)

	# -------------------------------------------------------------
	# Folder 4: SLA Policy & Configurations (/v1/sla-*)
	# -------------------------------------------------------------
	sla_items = [
		make_request(
			name="1. Get Global SLA Policy",
			method="GET",
			path_segments=["v1", "sla-policy"],
			description="Read the global SLA policy (maximum deferral days, supervisor approval requirement).",
		),
		make_request(
			name="2. Update Global SLA Policy",
			method="PATCH",
			path_segments=["v1", "sla-policy"],
			description="Update global SLA limits across all categories.",
			body={
				"max_deferral_days": 30,
				"requires_supervisor_approval": True,
			},
		),
		make_request(
			name="3. List Category SLA Settings",
			method="GET",
			path_segments=["v1", "sla-configurations"],
			query_params=[
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List per-category SLA configurations and reminder thresholds.",
		),
		make_request(
			name="4. Update Category SLA Setting",
			method="PATCH",
			path_segments=["v1", "sla-configurations", "{{service_category}}"],
			description="Update SLA window and breach notification settings for a specific category.",
			body={
				"sla_days": 7,
				"notify_on_breach": True,
				"auto_escalate": True,
			},
		),
	]
	collection["item"].append({"name": "4. SLA Policies & Configurations (/v1/sla-*)", "item": sla_items})

	# -------------------------------------------------------------
	# Folder 5: Response Templates (/v1/response-templates)
	# -------------------------------------------------------------
	response_items = [
		make_request(
			name="1. List Response Templates",
			method="GET",
			path_segments=["v1", "response-templates"],
			query_params=[
				{"key": "service_category", "value": "{{service_category}}"},
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List pre-written response templates available to officers.",
			post_extract=[
				'if (data && data.templates && data.templates.length > 0) pm.collectionVariables.set("response_template_id", data.templates[0].name);',
			],
		),
		make_request(
			name="2. Create Response Template",
			method="POST",
			path_segments=["v1", "response-templates"],
			description="Create a canned response template for grievance investigations.",
			body={
				"title": "Standard Intake Acknowledgment",
				"body": "Thank you for reaching out. We have received your inquiry regarding fertilizer distribution and an officer has been assigned.",
				"service_category": "{{service_category}}",
				"status": "In Progress",
			},
			post_extract=[
				'if (data && data.template) pm.collectionVariables.set("response_template_id", data.template.name);',
			],
		),
		make_request(
			name="3. Get Response Template",
			method="GET",
			path_segments=["v1", "response-templates", "{{response_template_id}}"],
			description="Get the content and metadata of a response template.",
		),
		make_request(
			name="4. Update Response Template",
			method="PATCH",
			path_segments=["v1", "response-templates", "{{response_template_id}}"],
			description="Edit template title, body text, or category mapping.",
			body={
				"title": "Updated Intake Acknowledgment",
				"body": "Thank you for contacting us. Your grievance is actively under review by the agricultural extension team.",
			},
		),
		make_request(
			name="5. Deactivate Response Template",
			method="DELETE",
			path_segments=["v1", "response-templates", "{{response_template_id}}"],
			description="Soft-deactivates the response template (`active = 0`).",
		),
	]
	collection["item"].append(
		{"name": "5. Response Templates (/v1/response-templates)", "item": response_items}
	)

	# -------------------------------------------------------------
	# Folder 6: Notification Templates (/v1/notification-templates)
	# -------------------------------------------------------------
	notif_items = [
		make_request(
			name="1. List Notification Templates",
			method="GET",
			path_segments=["v1", "notification-templates"],
			query_params=[
				{"key": "page", "value": "1"},
				{"key": "page_size", "value": "20"},
			],
			description="List all configurable SMS, Email, and In-App notification event templates.",
			post_extract=[
				'if (data && data.templates && data.templates.length > 0) pm.collectionVariables.set("notif_template_id", data.templates[0].name);',
			],
		),
		make_request(
			name="2. Get Notification Options & Channels",
			method="GET",
			path_segments=["v1", "notification-templates", "options"],
			description="Returns available notification channels, events, and languages.",
		),
		make_request(
			name="3. Get Notification Placeholders",
			method="GET",
			path_segments=["v1", "notification-templates", "placeholders"],
			description="Dictionary of dynamic placeholders available for message bodies (e.g. {{ticket_number}}, {{submitter_name}}).",
		),
		make_request(
			name="4. Get Notification Template",
			method="GET",
			path_segments=["v1", "notification-templates", "{{notif_template_id}}"],
			description="Fetch one notification template by its identifier.",
		),
		make_request(
			name="5. Update Notification Template",
			method="PATCH",
			path_segments=["v1", "notification-templates", "{{notif_template_id}}"],
			description="Update subject or message body content for an event notification.",
			body={
				"subject": "Grievance status update: {{ticket_number}}",
				"template": "Hello {{submitter_name}}, your grievance {{ticket_number}} is now {{status}}.",
			},
		),
	]
	collection["item"].append(
		{"name": "6. Notification Templates (/v1/notification-templates)", "item": notif_items}
	)

	# -------------------------------------------------------------
	# Folder 7: Taxonomy & Master Data
	# -------------------------------------------------------------
	taxonomy_items = [
		make_request(
			name="1. List Service Categories",
			method="GET",
			path_segments=["v1", "service-categories"],
			description="List service categories configured in the grievance catalog.",
		),
		make_request(
			name="2. Create Service Category",
			method="POST",
			path_segments=["v1", "service-categories"],
			description="Add a new service category to the grievance registry.",
			body={
				"category_name": "Agri Extension Services",
				"code": "EXT-01",
				"description": "Agricultural extension, farm training and advisory services",
			},
		),
		make_request(
			name="3. List Grievance Types",
			method="GET",
			path_segments=["v1", "grievance-types"],
			query_params=[
				{"key": "category", "value": "{{service_category}}"},
			],
			description="List sub-types under a service category.",
		),
		make_request(
			name="4. Create Grievance Type",
			method="POST",
			path_segments=["v1", "grievance-types"],
			description="Add a specific grievance type under an active category.",
			body={
				"type_name": "Delay in Extension Visit",
				"service_category": "{{service_category}}",
				"description": "Agronomist or DA failed to visit plot within scheduled period",
			},
		),
	]
	collection["item"].append({"name": "7. Taxonomy & Master Data", "item": taxonomy_items})

	return collection


def main():
	col = build_collection()
	out_file = Path("postman/oan_grievance_admin_collection.json")
	with open(out_file, "w", encoding="utf-8") as f:  # nosemgrep: frappe-security-file-traversal
		json.dump(col, f, indent=2)
	print(f"Generated {out_file} successfully with {len(col['item'])} top-level modules!")


if __name__ == "__main__":
	main()
