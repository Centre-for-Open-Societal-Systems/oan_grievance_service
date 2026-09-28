# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Submitter Profile: one person or organisation that files grievances.

What belongs here: profile validation, the dedupe key, and queries over profiles such
as which profiles a user owns.

What does not belong here: identity format rules shared with intake (services/identity.py)
and whether an owner may see a case (permissions.py).
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import identity


class GrievanceSubmitterProfile(Document):
	def validate(self):
		self.validate_required_identity()
		self.set_dedupe_key()

	def validate_required_identity(self):
		"""Enforce the registration requirements for this submitter type."""
		rule = identity.SUBMITTER_TYPE_RULES.get(self.submitter_type)
		if not rule:
			frappe.throw(
				_("{0} is not a submitter type this service knows how to register.").format(
					self.submitter_type
				),
				title=_("Unknown Submitter Type"),
			)

		missing = [field for field in rule.required if not (self.get(field) or "").strip()]
		if missing:
			labels = [frappe.unscrub(field) for field in missing]
			frappe.throw(
				_("A {0} must provide: {1}.").format(self.submitter_type, ", ".join(labels)),
				title=_("Incomplete Registration"),
			)

	def set_dedupe_key(self):
		"""Derive or normalize the canonical party key.

		If dedupe_key is provided (e.g. from Fayda / API intake / SSO), ensure it is preserved.
		Otherwise, fallback to phone:<contact_mobile>.
		"""
		key = (self.dedupe_key or "").strip()
		if key:
			if ":" not in key:
				key = build_dedupe_key(identity.SCHEME_PHONE, key)
		elif self.contact_mobile:
			key = build_dedupe_key(identity.SCHEME_PHONE, self.contact_mobile)

		if not self.is_new() and self.get_doc_before_save() and self.get_doc_before_save().dedupe_key:
			old_key = self.get_doc_before_save().dedupe_key
			old_scheme, _old_value = split_dedupe_key(old_key)

			# If the profile was previously anchored to phone, and contact_mobile changed,
			# sync dedupe_key to the new contact_mobile unless an explicit new key (e.g. Fayda) was provided.
			if old_scheme == identity.SCHEME_PHONE:
				if (
					key == old_key
					and self.contact_mobile
					and f"{identity.SCHEME_PHONE}:{self.contact_mobile}" != old_key
				):
					key = build_dedupe_key(identity.SCHEME_PHONE, self.contact_mobile)

			if old_key != key:
				# Phone-anchored identities are allowed to update phone number or upgrade to verified ID (Fayda / Org).
				# Verified identities (Fayda / Org) are immutable.
				if old_scheme != identity.SCHEME_PHONE:
					frappe.throw(
						_(
							"This profile is already registered as {0} and its identity cannot be changed."
						).format(old_key),
						title=_("Identity Is Immutable"),
					)

		if not key:
			frappe.throw(
				_("A Submitter Profile must have a valid dedupe_key or contact_mobile."),
				title=_("No Identity"),
			)

		# If dedupe_key changed, ensure it does not conflict with another existing profile
		if not self.is_new() and self.get_doc_before_save() and self.get_doc_before_save().dedupe_key != key:
			conflict = frappe.db.get_value(
				"Grievance Submitter Profile",
				{"dedupe_key": key, "name": ["!=", self.name]},
				"name",
			)
			if conflict:
				frappe.throw(
					_("A Submitter Profile with dedupe key '{0}' is already registered.").format(key),
					frappe.DuplicateEntryError,
					title=_("Already Registered"),
				)

		self.dedupe_key = key

	@property
	def identity_scheme(self):
		"""Which scheme the party was resolved by: 'fayda', 'org' or 'phone'."""
		return split_dedupe_key(self.dedupe_key)[0]

	@property
	def identity_value(self):
		"""The raw identifier, without its scheme prefix."""
		return split_dedupe_key(self.dedupe_key)[1]


def split_dedupe_key(key):
	"""('fayda', '3214...') from 'fayda:3214...'.

	Split on the first colon only: a phone number in E.164 has no colon, but nothing
	stops a future scheme's value from containing one.
	"""
	if not key or ":" not in key:
		return None, None
	scheme, value = key.split(":", 1)
	return scheme, value


def build_dedupe_key(scheme, value):
	"""The one place a key is composed, so intake and this controller cannot drift."""
	valid_schemes = (identity.SCHEME_FAYDA, identity.SCHEME_ORG, identity.SCHEME_PHONE)
	if scheme not in valid_schemes:
		frappe.throw(_("Unknown identity scheme {0}.").format(scheme))
	return f"{scheme}:{(value or '').strip()}"


def profiles_of(user):
	"""Profiles this user owns.

	Resolved through the explicit `user` link rather than by matching a contact address,
	so changing a contact email cannot transfer someone else's cases, and two profiles
	sharing an address do not both match.
	"""
	return frappe.get_all("Grievance Submitter Profile", filters={"user": user}, pluck="name")
