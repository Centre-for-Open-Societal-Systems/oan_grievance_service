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
	# The submission wizard fills its dropdowns on page load, before the submitter
	# has registered and therefore before any token exists. Without these four the
	# form 401s at the point a farmer opens it, having typed nothing. All four are
	# read-only reference data -- submitter types, categories, the types under a
	# category, and a preview of the ticket number -- and carry no personal data.
	# Draft save/load and grievance submit require an authenticated user.
	# Discard may still run for abandoned wizard cleanup without a token.
	"/api/method/oan_grievance_service.api.v1.draft.discard",
	# Attaching to a draft is part of the same unauthenticated wizard. The endpoint
	# gates the grievance path on a role itself; a guest reaches only the draft
	# path, by holding its client_uuid.
	"/api/method/oan_grievance_service.api.v1.attachment.submit_document",
]


def register():
	"""Register the oan_grievance_service namespace with oan_auth_service."""
	try:
		from oan_auth_service.api.middleware import register_namespace

		register_namespace(API_NAMESPACE, EXEMPT_PATHS)
	except ImportError:
		pass


register()
