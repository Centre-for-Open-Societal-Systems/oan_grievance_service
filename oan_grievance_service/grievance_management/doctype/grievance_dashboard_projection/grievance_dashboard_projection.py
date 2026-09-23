# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GrievanceDashboardProjection(Document):
	"""FR-09 / STG-330 pre-aggregated dashboard fact row.

	Rows are written only by ``services.dashboard_stats.refresh_projection``.
	Desk users and the Statistics API are read-only consumers.
	"""

	pass
