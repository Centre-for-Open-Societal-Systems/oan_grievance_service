"""JWT auth_hook configuration for this deployment.

The validation logic itself lives in oan_auth_service. This module exists to bind
it to oan_grievance_service's own API namespace, exempt paths and revocation rule,
so the shared library never needs to know this app exists.
"""

# Only requests under this prefix are subject to JWT validation; everything else
# (desk, standard Frappe APIs) is left to Frappe's own auth.
API_NAMESPACE = "/api/method/oan_grievance_service."

# Endpoints reachable without a bearer token. Kept explicit rather than pattern
# matched so adding one is a visible diff.
EXEMPT_PATHS: list[str] = [
	"/api/method/oan_grievance_service.api.v1.submitter.options",
	"/api/method/oan_grievance_service.api.v1.administrative_area.get_areas",
]

# Frappe's socket server learns who a connecting socket is by calling this endpoint
# with the client's headers. Registered so a bearer JWT resolves to its user rather
# than Guest. Exempt, so a desk cookie session or an anonymous website socket still
# connects as before; a token that is present must be valid.
SOCKET_USER_INFO_PATH = "/api/method/frappe.realtime.get_user_info"


def register():
	"""Register the oan_grievance_service namespace with oan_auth_service."""
	try:
		from oan_auth_service.api.middleware import register_namespace

		register_namespace(API_NAMESPACE, EXEMPT_PATHS)
		register_namespace(SOCKET_USER_INFO_PATH, [SOCKET_USER_INFO_PATH])
	except ImportError:
		pass


register()
