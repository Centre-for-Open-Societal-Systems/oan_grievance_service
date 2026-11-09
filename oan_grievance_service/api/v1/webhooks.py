"""Inbound webhooks from the platforms that feed the grievance service.

Frappe sees the gateway, not the platform: the IVR platform authenticates to APISIX with a
gateway key, and APISIX calls here with the Frappe API key and secret of a user holding
Grievance Submitter (`Authorization: token <api_key>:<api_secret>`). See apisix/README.md.
"""

from datetime import datetime

from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, require_role, success_response, validate_request
from pydantic import BaseModel, ConfigDict, Field

from oan_grievance_service.api.v1._options import public_status
from oan_grievance_service.api.v1._schemas import Text
from oan_grievance_service.api.v1.attachment import prepare_uploads
from oan_grievance_service.api.v1.grievance import _current_state, _timeline_event_with
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import intake
from oan_grievance_service.services import ticket_number as tn

route = prefixed("/api/v1")

IVR_ROLES = [C.ROLE_SUBMITTER, C.ROLE_ADMIN, "System Manager", "Administrator"]


class IvrGrievanceRequest(BaseModel):
	"""A call, as the IVR platform reports it: the caller's number and, optionally, more."""

	model_config = ConfigDict(extra="forbid")

	phone_number: str = Field(
		min_length=1,
		max_length=20,
		description="The caller's number: international (+251911223344) or, with a country_code, national",
	)
	country_code: str | None = Field(None, max_length=6)
	external_ref_id: str | None = Field(
		None,
		max_length=100,
		description="The platform's id for the call; a retried delivery with the same id files nothing new",
	)
	timestamp: datetime | None = Field(
		None,
		description="When the platform says the call happened (ISO 8601); a time in the future is read as now",
	)
	description: Text | None = Field(
		None,
		max_length=2000,
		description="What the caller said, when the platform has it; a fixed note is filed otherwise",
	)
	consent_given: bool = Field(
		True, description="Whether the caller consented; recorded as given when omitted"
	)


@route(
	"/webhooks/ivr/grievances",
	methods=("POST",),
	summary="File a grievance for an IVR caller from their phone number",
	tier="ivr-webhooks",
	auth="api_key",
	roles=IVR_ROLES,
)
@validate_request(IvrGrievanceRequest)
@handle_api_errors
@require_role(IVR_ROLES)
def ivr_grievance(**kwargs):
	"""Send JSON, or multipart/form-data to attach the call's audio (MP3, up to 10 MB).

	Files the call under the default category and type, on the IVR Helpline channel, for an
	Individual Farmer profile made from the number (an existing one is reused). Fayda ID, area
	and every other detail are left empty."""
	# Checked before anything is filed, so a bad file refuses the call instead of leaving
	# a case without its recording.
	uploads = prepare_uploads(None)
	doc, submission, attachments = intake.file_from_ivr(
		kwargs["phone_number"],
		country_code=kwargs.get("country_code"),
		external_ref_id=kwargs.get("external_ref_id"),
		timestamp=kwargs.get("timestamp"),
		description=kwargs.get("description"),
		uploads=uploads,
		consent_given=kwargs.get("consent_given", True),
	)

	created = submission is not None
	data = {
		"ticket_number": tn.display(doc.ticket_number),
		"status": public_status(doc.status),
		"submitter": doc.submitter,
		"created": created,
		"attachments": attachments,
		"current_state": _current_state(doc),
	}
	if created:
		data["timeline_event"] = _timeline_event_with(submission, doc, attachments)
	return success_response(
		data=data,
		message=_("Grievance submitted successfully") if created else _("Grievance already submitted"),
	)
