# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Convert service category codes from the old four-letter form to Base32.

The category code is the CATEGORY segment of the ticket number. Sites installed
before the nine-character scheme carry INPT, SCHM, PAYM, CRDT and MRKT, none of
which are valid: they are the wrong width, and INPT and CRDT contain characters
the alphabet excludes. Left alone, ticket generation refuses every submission on
those sites.

Safe to run because no tickets have been issued under the old codes. Once they
have, a code is frozen — a ticket already sent to a submitter cannot be made to
mean something else — and a future change would have to leave existing codes in
place and allocate new ones instead.
"""

import frappe

from oan_grievance_service.setup.install import SERVICE_CATEGORIES


def execute():
	for name, code, _order in SERVICE_CATEGORIES:
		existing = frappe.db.get_value("Grievance Service Category", name, "code")
		if existing is None or existing == code:
			continue
		frappe.db.set_value("Grievance Service Category", name, "code", code)
