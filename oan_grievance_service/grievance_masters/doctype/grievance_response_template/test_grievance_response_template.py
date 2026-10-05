# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.grievance_masters.doctype.grievance_response_template.grievance_response_template import (
	render,
	templates_for,
)
from oan_grievance_service.tests.fixtures import a_grievance


class TestGrievanceResponseTemplate(FrappeTestCase):
	def setUp(self):
		frappe.db.delete(
			"Grievance Response Template", {"template_code": ["in", ["TEST-TMPL-1", "TEST-TMPL-2"]]}
		)

	def tearDown(self):
		frappe.db.delete(
			"Grievance Response Template", {"template_code": ["in", ["TEST-TMPL-1", "TEST-TMPL-2"]]}
		)

	def test_template_creation_and_rendering_with_reason_and_note(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Response Template",
				"template_code": "TEST-TMPL-1",
				"title": "Test Resolution Template",
				"workflow_action": "Resolve",
				"body": "Dear {{ submitter_name }}, grievance {{ ticket_number }} is resolved.",
				"note": "Field verification verified by officer.",
				"is_active": 1,
			}
		).insert()

		self.assertEqual(doc.workflow_action, "Resolve")
		self.assertEqual(doc.body, "Dear {{ submitter_name }}, grievance {{ ticket_number }} is resolved.")
		self.assertEqual(doc.note, "Field verification verified by officer.")

		case = a_grievance()
		rendered_reason = render(doc, case, "body")
		rendered_note = render(doc, case, "note")

		self.assertIn("resolved", rendered_reason)
		self.assertEqual(rendered_note, "Field verification verified by officer.")

	def test_templates_for_service_category_filtering(self):
		frappe.get_doc(
			{
				"doctype": "Grievance Response Template",
				"template_code": "TEST-TMPL-1",
				"title": "General Template",
				"workflow_action": "Resolve",
				"body": "General resolution.",
				"note": "General note.",
				"is_active": 1,
			}
		).insert()

		frappe.get_doc(
			{
				"doctype": "Grievance Response Template",
				"template_code": "TEST-TMPL-2",
				"title": "Inputs Specific Template",
				"workflow_action": "Resolve",
				"service_category": "Inputs",
				"body": "Inputs resolved.",
				"note": "Inputs note.",
				"is_active": 1,
			}
		).insert()

		case = a_grievance(service_category="Inputs")

		# With category Inputs, both are available, Inputs specific ranks first
		inputs_templates = templates_for(case, "Resolve", service_category="Inputs")
		template_codes = [t.name for t in inputs_templates]
		self.assertIn("TEST-TMPL-2", template_codes)
		self.assertIn("TEST-TMPL-1", template_codes)
		self.assertEqual(template_codes[0], "TEST-TMPL-2")

		# With different category, Inputs specific is excluded
		other_templates = templates_for(case, "Resolve", service_category="Finance")
		other_codes = [t.name for t in other_templates]
		self.assertNotIn("TEST-TMPL-2", other_codes)
		self.assertIn("TEST-TMPL-1", other_codes)

	def test_invalid_jinja_syntax_in_note_raises_validation_error(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Response Template",
					"template_code": "TEST-TMPL-1",
					"title": "Bad Jinja",
					"workflow_action": "Resolve",
					"body": "Valid body",
					"note": "Invalid {{ unclosed tag",
					"is_active": 1,
				}
			).insert()
