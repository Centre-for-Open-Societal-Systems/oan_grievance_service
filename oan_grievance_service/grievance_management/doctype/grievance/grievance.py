# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries

# FSD 3.2.3 gives the ticket scheme REGION-WOREDA-CATEGORY-SEQUENCE with the example
# OROM-BISH-AGRN-12345. The segment codes now come from the Region, Woreda and Service
# Category masters rather than being hardcoded, so an administrator can correct them
# without a release. `abbreviate` is only the fallback for a master with no code set.
#
# Note the specification's own example code "AGRN" matches none of the five service
# categories in FSD 3.2.2. The seeded codes are INPT, SCHM, PAYM, CRDT and MRKT, and
# should be confirmed against the OAN registry before go-live.
CODE_LENGTH = 4
MIN_DESCRIPTION_LENGTH = 20


def abbreviate(value: str) -> str:
	"""Uppercase alphanumeric prefix of a name, padded to a fixed width."""
	cleaned = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
	if not cleaned:
		return "XXXX"
	return cleaned[:CODE_LENGTH].ljust(CODE_LENGTH, "X")


def segment(doctype: str, name: str) -> str:
	"""The configured ticket code for a master record, else an abbreviation of it."""
	if not name:
		return "XXXX"
	code = frappe.db.get_value(doctype, name, "code")
	return (code or abbreviate(name)).upper()


class Grievance(Document):
	def autoname(self):
		"""Build the FSD 3.2.3 ticket number: REGION-WOREDA-CATEGORY-SEQUENCE."""
		prefix = "-".join(
			[
				segment("Region", self.region),
				segment("Woreda", self.woreda),
				segment("Service Category", self.service_category),
			]
		)
		self.name = f"{prefix}-{getseries(prefix + '-', 5)}"
		self.ticket_number = self.name

	def validate(self):
		self.validate_description_length()
		self.validate_grievance_type_category()

	def validate_description_length(self):
		"""FSD 3.2.2: the description is free text with a minimum of 20 characters."""
		description = (self.description or "").strip()
		if len(description) < MIN_DESCRIPTION_LENGTH:
			frappe.throw(
				_("Description must be at least {0} characters.").format(MIN_DESCRIPTION_LENGTH),
				title=_("Description Too Short"),
			)

	def validate_grievance_type_category(self):
		"""FSD 3.2.2: grievance type is loaded per category, so it must belong to one."""
		if not (self.grievance_type and self.service_category):
			return
		parent = frappe.db.get_value("Grievance Type", self.grievance_type, "service_category")
		if parent != self.service_category:
			frappe.throw(
				_("Grievance type {0} belongs to category {1}, not {2}.").format(
					frappe.bold(self.grievance_type),
					frappe.bold(parent),
					frappe.bold(self.service_category),
				),
				title=_("Type Does Not Match Category"),
			)

	# ------------------------------------------------------------------
	# Deliberately not implemented yet. Each of these depends on a spec
	# question that is still open; see SETUP.md before filling them in.
	# ------------------------------------------------------------------

	def apply_routing(self):
		"""FSD 3.3: evaluate Grievance Routing Rule and auto-assign, else queue for the
		nodal officer. Stub: the fallback behaviour interacts with the unresolved SLA
		clock start below."""
		raise NotImplementedError("FSD 3.3 routing engine is not implemented yet")

	def start_sla(self):
		"""FSD 3.7 SLA clock.

		UNRESOLVED: FSD 4.2 starts the timer at assignment, while UC-04 computes the
		breach from creation_date + sla_days. These differ for any grievance that waits
		in the manual routing queue. Do not implement until the user decides which
		applies.
		"""
		raise NotImplementedError("SLA clock start is unresolved between FSD 4.2 and UC-04")
