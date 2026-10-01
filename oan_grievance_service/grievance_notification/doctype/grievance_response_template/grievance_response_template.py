# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

# An edit to any of these makes a new version. Switching `is_active` does not.
VERSIONED_FIELDS = (
	"title",
	"response_type",
	"service_category",
	"grievance_type",
	"action_taken",
	"resolution_summary",
)

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def extract_placeholders(*texts: str | None) -> list[str]:
	"""Distinct `{{ name }}` placeholder names across the texts, in order of first appearance."""
	seen: dict[str, None] = {}
	for text in texts:
		for name in _PLACEHOLDER.findall(text or ""):
			seen.setdefault(name)
	return list(seen)


class GrievanceResponseTemplate(Document):
	"""Pre-written action-taken and resolution-summary wording for officer responses.

	Editing is versioned: `version` goes up by one when the wording or scope changes, and
	Frappe's own change log (`track_changes`) keeps what each edit replaced.
	"""

	def before_naming(self):
		if not self.template_code:
			self.template_code = make_autoname("RT-.#####")

	def validate(self):
		self.validate_grievance_type()
		self.validate_placeholders()
		self.set_version()

	def validate_grievance_type(self):
		if not self.grievance_type:
			return
		category = frappe.db.get_value("Grievance Type", self.grievance_type, "service_category")
		if category != self.service_category:
			frappe.throw(
				_("Grievance type {0} does not belong to service category {1}.").format(
					self.grievance_type, self.service_category
				)
			)

	def validate_placeholders(self):
		"""Reject a stray or malformed brace pair, which would reach the officer as raw text."""
		for label, text in (
			(_("Action taken"), self.action_taken),
			(_("Resolution summary"), self.resolution_summary),
		):
			leftover = _PLACEHOLDER.sub("", text or "")
			if "{{" in leftover or "}}" in leftover:
				frappe.throw(
					_(
						"{0} has an unclosed or malformed placeholder. Write placeholders as {1} "
						"using letters, digits, and underscores."
					).format(label, "{{ name }}")
				)

	def set_version(self):
		"""Version 1 on creation, then one more for each edit that changes the wording or scope."""
		before = self.get_doc_before_save()
		if not before:
			self.version = 1
		elif any((before.get(f) or None) != (self.get(f) or None) for f in VERSIONED_FIELDS):
			self.version = (before.version or 1) + 1
		else:
			self.version = before.version or 1
