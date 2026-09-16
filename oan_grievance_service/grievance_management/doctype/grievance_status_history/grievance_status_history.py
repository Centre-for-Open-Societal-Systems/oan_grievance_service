# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from oan_grievance_service.services.audit import ImmutableRecord


class GrievanceStatusHistory(ImmutableRecord, Document):
	"""Grievance Status History: append-only. See ImmutableRecord for why the guard is here and not
	only in the permission flags."""
