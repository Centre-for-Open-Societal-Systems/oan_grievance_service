"""Machine users the gateway calls Frappe as.

The IVR webhook is not called with a Frappe credential. The IVR vendor authenticates to
the gateway with its own key, and the gateway replaces it with `token <api_key>:<api_secret>`
of the user below (apisix/README.md, "IVR webhook credentials"). This module creates the
user, so a new site does not depend on someone adding it by hand.

It does not generate the API key. Generate it once, after the site exists:

	bench --site <site> execute oan_grievance_service.setup.service_users.generate_ivr_keys

and store the printed `token <key>:<secret>` in the gateway's Secret
`oan-grievance-ivr-frappe-credential` (key `authorization`). The key is generated only once,
because Frappe invalidates the old secret when a new one is made.
"""

import frappe

from oan_grievance_service.services import constants as C

# The synthetic domain oan_auth_service gives local accounts: the notification code skips
# mail to it (services/notifications.py, SYNTHETIC_ADDRESS_DOMAIN).
IVR_USER_EMAIL = "grievance-ivr-integration@id.openagrinet.internal"
IVR_USER_FIRST_NAME = "Grievance IVR Integration"
IVR_USER_ROLES = (C.ROLE_SUBMITTER,)


def ensure_ivr_service_user():
	"""Create the IVR integration user, or bring an existing one back to the intended state.

	Idempotent: runs on install and on every migrate. The password is random and is never
	shown or stored, so the user can only authenticate with its API key.
	"""
	if frappe.db.exists("User", IVR_USER_EMAIL):
		user = frappe.get_doc("User", IVR_USER_EMAIL)
	else:
		user = frappe.new_doc("User")
		user.email = IVR_USER_EMAIL
		user.first_name = IVR_USER_FIRST_NAME
		user.user_type = "Website User"
		user.send_welcome_email = 0
		user.new_password = frappe.generate_hash(length=40)

	user.enabled = 1
	present = {row.role for row in user.roles}
	for role in IVR_USER_ROLES:
		if role not in present:
			user.append("roles", {"role": role})
	user.flags.ignore_permissions = True
	user.save()
	return user.name


# The user a verified link-and-PIN session runs as (STG-518, services/guest_access.py). It holds
# only the guest role, which reaches the one case the session is bound to. Nobody signs in as it:
# the password is random and never kept, no API key is ever made for it, and `restrict_ip` names
# an address no client has, so Frappe's login refuses it even with a password.
GUEST_USER_EMAIL = "grievance-guest-access@id.openagrinet.internal"
GUEST_USER_FIRST_NAME = "Grievance Guest Access"
GUEST_USER_ROLES = (C.ROLE_GUEST,)
GUEST_USER_RESTRICT_IP = "0.0.0.0"


def ensure_guest_service_user():
	"""Create the guest service user, or bring an existing one back to the intended state.

	Idempotent: runs on install and on every migrate. Roles other than the guest role are
	removed, so the user can never reach more than one case.

	The guest role is kept without desk access first. Frappe creates a role named in a DocPerm
	row when it syncs the DocType, with desk access on, before `seed_roles` runs, and a user with
	such a role is saved as a System User.
	"""
	for role in GUEST_USER_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0}).insert(
				ignore_permissions=True
			)
		elif frappe.db.get_value("Role", role, "desk_access"):
			frappe.db.set_value("Role", role, "desk_access", 0)

	if frappe.db.exists("User", GUEST_USER_EMAIL):
		user = frappe.get_doc("User", GUEST_USER_EMAIL)
	else:
		user = frappe.new_doc("User")
		user.email = GUEST_USER_EMAIL
		user.first_name = GUEST_USER_FIRST_NAME
		user.user_type = "Website User"
		user.send_welcome_email = 0
		user.new_password = frappe.generate_hash(length=40)

	user.enabled = 1
	user.user_type = "Website User"
	user.restrict_ip = GUEST_USER_RESTRICT_IP
	# An API key would be a way in that skips the link and PIN.
	user.api_key = None
	user.set("roles", [row for row in user.roles if row.role in GUEST_USER_ROLES])
	present = {row.role for row in user.roles}
	for role in GUEST_USER_ROLES:
		if role not in present:
			user.append("roles", {"role": role})
	user.flags.ignore_permissions = True
	user.save()
	return user.name


def generate_ivr_keys():
	"""Print the gateway token for the IVR user. Only creates a key pair if there is none."""
	user = frappe.get_doc("User", IVR_USER_EMAIL)
	if user.api_key:
		print(f"{IVR_USER_EMAIL} already has an API key; not generating another.")
		print("To rotate it, call frappe.core.doctype.user.user.generate_keys deliberately.")
		return
	from frappe.core.doctype.user.user import generate_keys

	generate_keys(IVR_USER_EMAIL)
	# The secret is printed once and cannot be read back, so the key must be stored first.
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	user.reload()
	secret = user.get_password("api_secret")
	print(f"token {user.api_key}:{secret}")
