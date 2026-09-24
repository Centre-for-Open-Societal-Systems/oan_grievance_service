"""FR-08 Notifications and Communication, per the FSD Appendix C matrix.

Configuration is a core Notification record per (event, channel). An administrator can
change the channel, recipient, wording or enable state from the desk without a release,
which is what FSD 3.11.7 asks for, and core gives us real Jinja rendering and a
condition expression for free.

Delivery, however, is ours. Core renders a notification body exactly once, before it
knows who it is addressed to (notification.py: the render at send_an_email precedes
nothing that varies per recipient), and it never sets the language around that render.
FSD 3.8 requires each citizen to be written to in their own language, so the send loop
below does one render per recipient inside print_language(), which is the only point at
which the recipient's language is known. Everything else - the template, the enable
flag, the condition, the recipient role - stays in the admin-editable record.

Amharic wording lives in core Translation records rather than in a second Notification
per event, so both languages are editable from the desk. The consequence to remember:
the English source string is the translation key, so a stable context key is passed to
_() and admins must edit the Amharic alongside any English rewording.

The Appendix C closing note requires controls against redundant messaging, so a queued
row is deduplicated on (grievance, event, recipient) and a disabled Notification, or one
whose condition is false, sends nothing.
"""

import re

import frappe
from frappe import _
from frappe.core.doctype.sms_settings.sms_settings import _send_sms
from frappe.email.doctype.notification.notification import get_context
from frappe.translate import get_user_lang, print_language
from frappe.utils import now_datetime, strip_html_tags, validate_email_address

RECIPIENT_SUBMITTER = "Submitter"
RECIPIENT_ASSIGNED_OFFICER = "Assigned Officer"
RECIPIENT_DEPARTMENT_OFFICER = "Department Officer"
RECIPIENT_NODAL_OFFICER = "Nodal Officer"
RECIPIENT_DEPARTMENT_HEAD = "Department Head"
RECIPIENT_TOP_LEVEL = "Top Level Authority"

RECIPIENT_ROLES = (
	RECIPIENT_SUBMITTER,
	RECIPIENT_ASSIGNED_OFFICER,
	RECIPIENT_DEPARTMENT_OFFICER,
	RECIPIENT_NODAL_OFFICER,
	RECIPIENT_DEPARTMENT_HEAD,
	RECIPIENT_TOP_LEVEL,
)

ROLE_LEVEL_RECIPIENTS = {
	RECIPIENT_NODAL_OFFICER: "nodal_officer",
	RECIPIENT_TOP_LEVEL: "senior_nodal_officer",
	RECIPIENT_DEPARTMENT_HEAD: "department_head",
}

CHANNEL_SMS = "SMS"
CHANNEL_EMAIL = "Email"
# Core's create_system_notification writes per-user Notification Log rows carrying
# document_type, document_name, title and description, which a frontend reads over the
# API. That makes it a real in-app channel, not just the desk bell.
CHANNEL_SYSTEM = "System Notification"

SUPPORTED_CHANNELS = (CHANNEL_SMS, CHANNEL_EMAIL, CHANNEL_SYSTEM)

# Jinja expression, statement and comment blocks.
JINJA_BLOCK = re.compile(r"{{.*?}}|{%.*?%}|{#.*?#}", re.DOTALL)


def validate_notification(doc, method=None):
	"""Keep Grievance notification wording translatable. Registered on Notification.

	FSD 3.8 requires citizen messages in Amharic and English. The send path renders once
	per recipient inside print_language(), which only moves strings marked with _();
	literal text typed into a template renders identically in every language. So a
	Grievance notification carrying bare literal text would quietly send English to
	Amharic speakers, with nothing in the log to show it had happened.

	This is a heuristic, not a proof. It strips Jinja blocks and rejects readable text in
	what remains, which catches the realistic mistake - someone pastes a sentence in -
	but does not verify that the strings inside {{ }} are themselves wrapped in _().
	"""
	if doc.get("document_type") != "Grievance":
		return

	if doc.get("channel") and doc.channel not in SUPPORTED_CHANNELS:
		frappe.throw(
			_("Grievance notifications support {0}. {1} has no delivery path here.").format(
				", ".join(SUPPORTED_CHANNELS), doc.channel
			),
			title=_("Unsupported Channel"),
		)

	# Subject is checked alongside the body: it is rendered for email and reused as the
	# in-app title, so literal text there reaches the recipient just as untranslated.
	for fieldname in ("subject", "message"):
		value = doc.get(fieldname)
		if not value:
			continue
		remainder = JINJA_BLOCK.sub("", value)
		if any(char.isalnum() for char in remainder):
			frappe.throw(
				_(
					"Grievance notification {0} must be wrapped in _() so it can be sent in the "
					"recipient's language. Found untranslatable text: {1}"
				).format(_(fieldname), frappe.utils.cstr(remainder).strip()[:120]),
				title=_("Untranslatable Message"),
			)


def resolve_recipient(grievance, recipient_role, override=None):
	"""Turn a recipient role into a User, or into a bare address where there is no User.

	This is the Link traversal core cannot do: receiver_by_document_field iterates a
	child table and has no way to follow a Link to another doctype, so the department
	officers on Grievance Department are unreachable from a Notification. Doing it here
	is also why no denormalised recipient columns are needed on Grievance.

	Addresses are resolved at send time and never stored on the Notification, so a staff
	change does not misdirect notifications on historic cases.
	"""
	if override:
		return override

	if recipient_role == RECIPIENT_SUBMITTER:
		# submitter is a Link to Submitter Profile, so the User is one hop away. Fall
		# back to the contact snapshot for submitters who never registered a User.
		if grievance.submitter:
			user = frappe.db.get_value("Grievance Submitter Profile", grievance.submitter, "user")
			if user:
				return user
		return grievance.contact_mobile or grievance.contact_email

	if recipient_role == RECIPIENT_ASSIGNED_OFFICER:
		return grievance.assigned_to

	if not grievance.assigned_dept:
		return None

	from oan_grievance_service.permissions import find_officer_by_role_level

	role_level = ROLE_LEVEL_RECIPIENTS.get(recipient_role)
	if role_level:
		officer = find_officer_by_role_level(
			role_level,
			department=grievance.assigned_dept,
			administrative_area=grievance.administrative_area,
		)
		if officer:
			return officer

	dept = frappe.db.get_value(
		"Grievance Department",
		grievance.assigned_dept,
		["email_account", "head_of_dept", "nodal_officer", "senior_officer"],
		as_dict=True,
	)
	if not dept:
		return None

	dept_fallback = {
		# email_account is a Data field holding a mailbox, not a User link. Every
		# Department Officer event in Appendix C is email-only, which is what makes
		# that safe: there is no mobile number to look up for a bare address.
		RECIPIENT_DEPARTMENT_OFFICER: dept.email_account,
		RECIPIENT_DEPARTMENT_HEAD: dept.head_of_dept,
		RECIPIENT_NODAL_OFFICER: dept.nodal_officer,
		RECIPIENT_TOP_LEVEL: dept.senior_officer or dept.head_of_dept,
	}
	return dept_fallback.get(recipient_role)


def notifications_for(event_code):
	"""Every enabled Notification wired to an Appendix C event.

	Events are carried on core's "Method" trigger rather than Save/Value Change: the
	lifecycle fires them explicitly from the service layer, so there is one Notification
	per (event, channel) and the channel Select decides which transport runs.
	"""
	return frappe.get_all(
		"Notification",
		filters={
			"document_type": "Grievance",
			"event": "Method",
			"method": event_code,
			"enabled": 1,
		},
		pluck="name",
	)


def _recipient_language(recipient):
	"""The language to render in, per FSD 3.8.

	get_user_lang carries core's own fallback chain (User.language, then System
	Settings, then en), but it assumes the argument is a User. Bare addresses - the
	department mailbox, or an unregistered submitter's mobile - have no User record and
	fall back to the site default.
	"""
	if recipient and frappe.db.exists("User", recipient):
		return get_user_lang(recipient)
	return frappe.db.get_single_value("System Settings", "language") or "en"


def _already_queued(grievance_name, event_code, recipient, channel):
	"""Appendix C note: guard against redundant messaging.

	The key includes the channel. Appendix C's "SMS + Email" rows are two Notification
	records here, because core's channel is a single Select, and both are meant to go
	out - so deduplicating on (grievance, event, recipient) alone would silently drop
	whichever of the pair was queued second.
	"""
	return bool(
		frappe.db.exists(
			"Grievance Notification Log",
			{
				"grievance": grievance_name,
				"event": event_code,
				"recipient": recipient,
				"channel": channel,
			},
		)
	)


def queue(grievance, event_code, recipient_override=None):
	"""Write a Grievance Notification Log row for an Appendix C event.

	Returns the log rows created, which is an empty list when every matching
	Notification is disabled, fails its condition, or has already been sent for this
	grievance.
	"""
	rows = []

	for notification_name in notifications_for(event_code):
		notification = frappe.get_doc("Notification", notification_name)
		context = get_context(grievance)

		# Core evaluates this inside evaluate_alert; we are not going through that path,
		# so the condition is honoured here or not at all.
		if notification.condition and not frappe.safe_eval(notification.condition, None, context):
			continue

		recipient_role = notification.get("grievance_recipient") or RECIPIENT_SUBMITTER
		recipient = resolve_recipient(grievance, recipient_role, recipient_override)
		if not recipient:
			continue

		if _already_queued(grievance.name, event_code, recipient, notification.channel):
			continue

		language = _recipient_language(recipient)

		# The only reason delivery is ours rather than core's: one render per recipient,
		# with the recipient's language in scope. print_language also clears the cached
		# jenv, so _() inside the template resolves against the right dictionary.
		with print_language(language):
			# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
			message = frappe.render_template(notification.message, context)
			# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
			subject = frappe.render_template(notification.subject, context) if notification.subject else ""

		rows.append(
			frappe.get_doc(
				{
					"doctype": "Grievance Notification Log",
					"grievance": grievance.name,
					"event": event_code,
					"recipient": recipient,
					"channel": notification.channel,
					"language": language,
					"status": "Queued",
					"message": message,
					"subject": subject,
					"notification": notification_name,
				}
			).insert(ignore_permissions=True)
		)

	return rows


# The auth service names every User it registers with a synthetic address in this
# domain and keeps the real one in the custom `oan_login_email` field, because core's
# User.validate copies `name` back into `email` on every save (user/user.py:222).
# So `User.email` is never a mailbox for a registered submitter or officer.
SYNTHETIC_ADDRESS_DOMAIN = "@id.openagrinet.internal"


def _submitter_profile_for(recipient, grievance):
	"""The submitter's profile name when the queued row is addressed to a registered
	submitter, else None.

	`resolve_recipient` hands back the submitter's User when they registered one.
	When they did not, it hands back the bare contact snapshot from the case, and
	this returns None: there is no registered contact to prefer.
	"""
	if not grievance.submitter:
		return None
	user = frappe.db.get_value("Grievance Submitter Profile", grievance.submitter, "user")
	return grievance.submitter if user and user == recipient else None


def _address_candidates(recipient, grievance, profile_field, user_fields, case_field):
	"""Where to look for a recipient's contact, in order.

	Precedence, agreed on PR #26 review: for a registered submitter the registered
	contact always wins, first the profile (which the farmer can update) and then
	the User record (from registration). The contact snapshot on the case is used
	only for a submitter with no account, a walk-in or IVR caller that staff filed,
	where the snapshot is the only contact there is. For staff recipients only the
	User record applies. The snapshot is taken at filing time, so for a registered
	farmer it can only ever equal or lag the profile, never lead it.
	"""
	candidates = []
	profile = _submitter_profile_for(recipient, grievance)
	if profile:
		candidates.append(frappe.db.get_value("Grievance Submitter Profile", profile, profile_field))

	if frappe.db.exists("User", recipient):
		for fieldname in user_fields:
			if frappe.db.has_column("User", fieldname):
				candidates.append(frappe.db.get_value("User", recipient, fieldname))
	else:
		# No account: the row carries the contact snapshot itself.
		candidates += [getattr(grievance, case_field, None), recipient]
	return candidates


def _deliverable_email(recipient, grievance):
	"""The address a queued email row actually goes to.

	Registered contact first (profile, then the User's login email), the case's
	snapshot only for a submitter without an account. `User.email` is the last
	resort and is skipped when it is the synthetic registration address. Found on
	the dev bench: every acknowledgement email went to xxxx@id.openagrinet.internal,
	which no mail server delivers.
	"""
	candidates = _address_candidates(
		recipient, grievance, "contact_email", ("oan_login_email", "email"), "contact_email"
	)
	for address in candidates:
		if not address:
			continue
		address = address.strip()
		if address.lower().endswith(SYNTHETIC_ADDRESS_DOMAIN):
			continue
		if validate_email_address(address):
			return address
	raise ValueError(f"No deliverable email address for {recipient}")


def _deliverable_mobile(recipient, grievance):
	"""The number a queued SMS row actually goes to, same precedence as email."""
	candidates = _address_candidates(recipient, grievance, "contact_mobile", ("mobile_no",), "contact_mobile")
	for mobile in candidates:
		if mobile and mobile.strip():
			return mobile.strip()
	raise ValueError(f"No mobile number for {recipient}")


def _send_email_row(row, grievance):
	"""Deliver one queued email row and return the Communication it threaded onto."""
	from frappe.core.doctype.communication.email import _make as make_communication

	address = _deliverable_email(row.recipient, grievance)

	communication = make_communication(
		doctype="Grievance",
		name=grievance.name,
		content=row.message,
		subject=row.subject or f"Grievance {grievance.ticket_number or grievance.name}",
		recipients=address,
		communication_medium="Email",
		send_email=False,
		communication_type="Automated Message",
	).get("name")

	frappe.sendmail(
		recipients=[address],
		subject=row.subject or f"Grievance {grievance.ticket_number or grievance.name}",
		message=row.message,
		reference_doctype="Grievance",
		reference_name=grievance.name,
		communication=communication,
		delayed=True,
	)
	return {"communication": communication}


def _send_sms_row(row, grievance):
	"""Deliver one queued SMS row.

	Core writes an SMS Log only on gateway success, and not at all once a send_sms hook
	is registered, so the Grievance Notification Log row is the durable evidence and the
	sms_log link is opportunistic.

	With no gateway configured, core's `_send_sms` only msgprints and returns, so a
	row would be marked Sent for a message that never left. Found on the dev bench:
	every SMS row read Sent with SMS Settings empty. Fail closed instead, so a
	gateway that is missing or not answering shows up as Failed in the log.
	"""
	mobile = _deliverable_mobile(row.recipient, grievance)
	hooked = bool(frappe.get_hooks("send_sms"))

	if not hooked and not frappe.db.get_single_value("SMS Settings", "sms_gateway_url"):
		raise ValueError("No SMS gateway configured in SMS Settings; message not sent.")

	before = frappe.db.get_value("SMS Log", {}, "name", order_by="creation desc")
	_send_sms([mobile], strip_html_tags(row.message), success_msg=False)
	after = frappe.db.get_value("SMS Log", {}, "name", order_by="creation desc")

	delivered = bool(after and after != before)
	if not hooked and not delivered:
		raise ValueError("SMS gateway did not confirm delivery: no SMS Log was written.")
	return {"sms_log": after} if delivered else {}


def _send_system_row(row, grievance):
	"""Deliver one queued in-app row.

	Core's own path resolves recipients through get_list_of_recipients, which we bypass,
	so the Notification Log row is created directly for the recipient we already
	resolved. Only a User can receive one - for_user is a User link - so a bare address
	such as the department mailbox has nowhere to go.
	"""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	if not frappe.db.exists("User", row.recipient):
		raise ValueError(f"{row.recipient} is not a User and cannot receive an in-app notification")

	enqueue_create_notification(
		[row.recipient],
		{
			"type": "Alert",
			"document_type": "Grievance",
			"document_name": grievance.name,
			"subject": row.subject or grievance.ticket_number or grievance.name,
			"title": row.subject or grievance.ticket_number or grievance.name,
			"description": row.message,
			"from_user": grievance.modified_by or grievance.owner,
		},
		# Our own log dedupes on (grievance, event, recipient, channel); this stops a
		# retry of a partially-failed dispatch from stacking rows in the bell feed too.
		dedupe_on=["document_type", "document_name", "subject"],
	)
	return {}


def dispatch_queued(limit=100):
	"""Send whatever is queued. Called by the scheduler."""
	pending = frappe.get_all(
		"Grievance Notification Log",
		filters={"status": "Queued"},
		fields=["name", "grievance", "channel", "recipient", "message", "subject"],
		limit=limit,
		order_by="creation asc",
	)

	grievance_cache = {}

	def _get_cached_grievance(name):
		if name not in grievance_cache:
			grievance_cache[name] = frappe.get_doc("Grievance", name)
		return grievance_cache[name]

	for row in pending:
		update = {"status": "Sent", "sent_at": now_datetime()}
		try:
			if row.channel == CHANNEL_EMAIL:
				update.update(_send_email_row(row, _get_cached_grievance(row.grievance)))
			elif row.channel == CHANNEL_SMS:
				update.update(_send_sms_row(row, _get_cached_grievance(row.grievance)))
			elif row.channel == CHANNEL_SYSTEM:
				update.update(_send_system_row(row, _get_cached_grievance(row.grievance)))
			else:
				# Never silently mark an undelivered row as Sent.
				raise ValueError(f"No delivery path for channel {row.channel}")
		except Exception:
			frappe.log_error(
				title="Grievance notification dispatch failed",
				message=frappe.get_traceback(),
			)
			update = {"status": "Failed", "error": frappe.get_traceback(with_context=False)}

		frappe.db.set_value("Grievance Notification Log", row.name, update, update_modified=False)

	return len(pending)
