# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Woreda(Document):
	def validate(self):
		self.validate_code_unique_in_region()
		self.validate_zone_region_agree()

	def validate_code_unique_in_region(self):
		"""FSD 3.2.3 puts this code in the ticket number.

		Two woredas sharing a code inside one region make the ticket ambiguous about
		where the case was filed -- OROM-SULU-INPT-00001 would name two places. The
		seeded dataset is generated collision-free, but an administrator adding a
		woreda by hand has no way to know that, so the invariant is enforced here
		rather than assumed.
		"""
		if not (self.code and self.region):
			return

		clash = frappe.db.get_value(
			"Woreda",
			{"code": self.code, "region": self.region, "name": ["!=", self.name]},
			["name", "woreda_name"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("Code {0} is already used by {1} in {2}. Ticket numbers would be ambiguous.").format(
					frappe.bold(self.code), frappe.bold(clash.woreda_name), frappe.bold(self.region)
				),
				title=_("Duplicate Woreda Code"),
			)

	def validate_zone_region_agree(self):
		"""A woreda cannot sit in a zone belonging to a different region."""
		if not (self.zone and self.region):
			return

		zone_region = frappe.db.get_value("Zone", self.zone, "region")
		if zone_region and zone_region != self.region:
			frappe.throw(
				_("Zone {0} belongs to {1}, not {2}.").format(
					frappe.bold(self.zone), frappe.bold(zone_region), frappe.bold(self.region)
				),
				title=_("Zone Does Not Match Region"),
			)
