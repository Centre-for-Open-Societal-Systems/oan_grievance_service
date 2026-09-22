# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Migrate legacy Grievance Draft records onto Grievance DocType.

Prior to unification, draft submissions were stored in a separate DocType ('Grievance Draft').
This patch migrates any legacy draft records into the Grievance table with status='Draft'
and workflow_state='Draft' so existing in-flight submissions are preserved.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Grievance Draft"):
		return

	# Reload Grievance DocType to ensure all columns are up to date
	frappe.reload_doc("grievance_management", "doctype", "grievance")

	draft_rows = frappe.db.sql(
		"""
		SELECT * FROM `tabGrievance Draft`
		""",
		as_dict=True,
	)

	for row in draft_rows:
		client_uuid = row.get("client_submission_uuid") or row.get("client_uuid") or row.get("name")
		if not client_uuid:
			continue

		# Check if already migrated or existing on Grievance
		if frappe.db.exists("Grievance", {"client_submission_uuid": client_uuid}):
			continue

		draft_name = f"DRAFT-{client_uuid}"
		if frappe.db.exists("Grievance", draft_name):
			continue

		doc = frappe.new_doc("Grievance")
		doc.name = draft_name
		doc.client_submission_uuid = client_uuid
		doc.owner = row.get("owner") or "Administrator"
		doc.workflow_state = "Draft"
		doc.status = "Draft"
		doc.docstatus = 0

		# Copy all compatible draft fields
		fields_to_copy = [
			"submitter",
			"submitter_type",
			"submitter_name",
			"contact_mobile",
			"contact_email",
			"submission_channel",
			"administrative_area",
			"administrative_unit",
			"service_category",
			"grievance_type",
			"associated_service_provider",
			"description",
			"desired_outcome",
			"is_anonymous",
		]
		for field in fields_to_copy:
			if field in row and row[field] is not None:
				doc.set(field, row[field])

		doc.flags.ignore_mandatory = True
		doc.flags.is_draft_wizard = True
		doc.insert(ignore_permissions=True)
