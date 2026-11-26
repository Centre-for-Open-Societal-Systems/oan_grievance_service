# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GrievanceGuestAccess(Document):
	"""The link and PIN of one sensitive anonymous case (STG-518).

	Written only by services/guest_access.py, with ignore_permissions. The DocPerm rows grant
	System Manager read and nothing else, so no one edits a credential by hand.
	"""
