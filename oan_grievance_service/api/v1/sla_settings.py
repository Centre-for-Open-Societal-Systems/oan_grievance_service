# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The installation-wide SLA policy for the Administration SLA tab.

`/api/v1/sla-policy` is the deferral ceiling, who approves a deferral, and the auto-escalate
threshold. SLA days are the service categories' rules (`/api/v1/sla-rules`), and the
auto-escalate and notify-on-breach flags are each category assignment's
(`/api/v1/category-assignments`).

Handlers stay thin. Field and range checks live in the doctype's `validate()`, so they hold
for the Desk as well as the API. What is here is request shape and the projection of the record.
The settings are read when a case's clock is armed, so a
change applies to cases that start, resume or re-arm after it, and leaves armed cases alone.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_permission,
	require_role,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1._schemas import Body
from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
	get_policy,
	max_deferral_days,
	requires_supervisor_approval,
)
from oan_grievance_service.services.constants import ADMIN_READ_ROLES

route = prefixed("/api/v1")

POLICY = "Grievance Deferral Policy"

# The only fields a PATCH may write. The request schema already forbids anything else; this
# keeps that true if a schema ever grows a field the document should not take from a client.
POLICY_EDITABLE = ("max_deferral_days", "requires_supervisor_approval")


class GlobalSlaPolicyRecord(BaseModel):
	max_deferral_days: int
	requires_supervisor_approval: bool
	modified: str | None = None


class GlobalSlaPolicyData(BaseModel):
	policy: GlobalSlaPolicyRecord


class UpdateGlobalSlaPolicy(Body):
	"""Partial update. Omitted fields stay as they are."""

	max_deferral_days: int = Field(
		default=None, ge=1, description="Most days any single deferral may add to the SLA clock"
	)
	requires_supervisor_approval: bool = Field(
		default=None,
		description="true: a senior officer decides a deferral. false: the assigned officer may approve it",
	)


def _text(modified) -> str | None:
	"""`modified` as the ISO-8601 UTC text the other endpoints return."""
	return to_tz_aware_iso(modified)


def _policy_data() -> dict:
	"""The installation-wide policy, with the defaults filled in while the Single is untouched."""
	return {
		"policy": GlobalSlaPolicyRecord(
			max_deferral_days=max_deferral_days(),
			requires_supervisor_approval=requires_supervisor_approval(),
			modified=_text(get_policy().modified),
		).model_dump()
	}


@route("/sla-policy", methods=("GET",), summary="Get the global SLA policy", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@api_doc(
	summary="Get the global SLA policy",
	description="The installation-wide SLA settings: the most days one deferral may add, and whether a "
	+ "deferral needs a senior officer's approval (requires_supervisor_approval; false lets the "
	+ "assigned officer approve it). A case escalates automatically once its full SLA window has "
	+ "elapsed; there is no escalation threshold.",
	tags=["Administration"],
	response_model=GlobalSlaPolicyData,
)
def get_global_policy(**kwargs):
	"""Return the installation-wide SLA policy."""
	return success_response(
		data=_policy_data(),
		message=_("Global SLA policy retrieved"),
	)


@route("/sla-policy", methods=("PATCH",), summary="Update the global SLA policy", tier="officer-core")
@handle_api_errors
@require_permission("Grievance Deferral Policy", "write")
@validate_request(UpdateGlobalSlaPolicy, exclude_unset=True)
@api_doc(
	summary="Update the global SLA policy",
	description="Change max_deferral_days, requires_supervisor_approval or both. Omitted fields stay "
	+ "as they are. A new setting applies to the next deferral request.",
	tags=["Administration"],
	response_model=GlobalSlaPolicyData,
)
def update_global_policy(**kwargs):
	"""Update the installation-wide policy. `kwargs` holds only the fields the client sent."""
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	policy = frappe.get_doc(POLICY)
	policy.update({key: kwargs[key] for key in POLICY_EDITABLE if key in kwargs})
	policy.save()
	return success_response(data=_policy_data(), message=_("Global SLA policy updated"))
