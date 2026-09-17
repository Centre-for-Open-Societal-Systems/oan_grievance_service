"""REST routing initialization for OAN Grievance Service.

Integrates with oan_auth_service.api.router by declaring routes using @prefixed(...)
or @rest(...), and ensuring all rules are registered into Frappe's API_URL_MAP.
"""

import frappe
from oan_auth_service.api.middleware import register_namespace
from oan_auth_service.api.router import (
	NAMESPACE,
	_exempt_paths,
	_rules,
	prefixed,
	registered_routes,
	rest,
)
from oan_auth_service.api.router import (
	ensure_routes_registered as ensure_auth_routes,
)
from oan_auth_service.api.utils import handle_api_errors, success_response

_REGISTERED = False

root_route = prefixed("/api/v1")


@root_route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"/grievances/health", methods=("GET",), allow_guest=True, summary="Grievance service health check"
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def get_health():
	"""Health check endpoint for the grievance service."""
	return success_response(
		data={
			"status": "healthy",
			"service": "oan_grievance_service",
			"api_version": "v1",
		}
	)


@root_route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"/grievances/ping", methods=("GET",), allow_guest=True, summary="Grievance service ping"
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def get_ping():
	"""Ping endpoint for uptime monitors."""
	return success_response(
		data={
			"ping": "pong",
			"service": "oan_grievance_service",
			"api_version": "v1",
		}
	)


def ensure_routes_registered() -> None:
	"""Add every declared grievance route to Frappe's URL map and claim the namespace."""
	global _REGISTERED
	if _REGISTERED:
		return

	import frappe.api

	# 1. Ensure auth routes are imported & registered
	ensure_auth_routes()

	# 2. Importing grievance endpoint modules executes the @route(...) decorator registrations
	from oan_grievance_service.api.v1 import administrative_area, grievance, profile, submitter

	for rule in _rules:
		if rule not in frappe.api.API_URL_MAP._rules:
			frappe.api.API_URL_MAP.add(rule)

	register_namespace(prefix=NAMESPACE, exempt_paths=sorted(_exempt_paths))
	_REGISTERED = True
