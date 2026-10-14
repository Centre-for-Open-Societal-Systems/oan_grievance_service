# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Endpoint guards that defer to DocType permissions instead of keeping role lists."""

from functools import wraps

import frappe


def require_permission(doctype: str, ptype: str):
	"""Reject the call unless the caller holds `ptype` on `doctype` through DocPerm.

	This is the doctype-level check only. Row-level access still comes from the
	permission hooks in permissions.py when the handler loads the document.
	"""

	def decorator(fn):
		@wraps(fn)
		def wrapper(*args, **kwargs):
			frappe.has_permission(doctype, ptype, throw=True)
			return fn(*args, **kwargs)

		return wrapper

	return decorator
