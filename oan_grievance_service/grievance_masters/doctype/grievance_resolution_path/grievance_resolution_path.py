# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GrievanceResolutionPath(Document):
	def validate(self):
		self.path_name = (self.path_name or "").strip()
