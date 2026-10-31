# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from oan_grievance_service.services.audit import ImmutableRecord


class GrievanceAccessAuditEvent(ImmutableRecord, Document):
	"""Grievance Access Audit Event: append-only. See ImmutableRecord for why the guard is here and not
	only in the permission flags."""
