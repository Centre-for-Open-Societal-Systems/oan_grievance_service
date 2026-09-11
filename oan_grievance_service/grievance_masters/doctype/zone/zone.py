# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Zone(Document):
	def validate(self):
		self.set_path_code()

	def set_path_code(self):
		"""Keep the dotted ancestry in step with the region and code it derives from.

		Recomputed on every save rather than written once, because an administrator
		correcting a code would otherwise leave the path pointing at the old one.
		"""
		region_code = frappe.db.get_value("Region", self.region, "code") if self.region else None
		if region_code and self.code:
			self.path_code = f"ET.{region_code}.{self.code}"
