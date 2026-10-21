# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Service provider sources: see how the directory is fed and start a sync.

A source is a place the directory copies providers from (the A2C bank catalog). A sync reads
the whole source in the background and brings the directory in line with it. These endpoints
are for administrators; credentials are stored on the source and never returned.
"""

from datetime import datetime

from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1._schemas import Body, NonBlank
from oan_grievance_service.grievance_masters.doctype.grievance_service_provider_source.grievance_service_provider_source import (
	list_sources,
)
from oan_grievance_service.services.constants import ADMIN_ROLES
from oan_grievance_service.services.service_provider_sync import enqueue_sync

route = prefixed("/api/v1/service-provider-sources")


class ServiceProviderSourceItem(BaseModel):
	name: str
	source_type: str
	is_enabled: bool
	api_base_url: str | None = None
	credentials_configured: bool = Field(description="Whether the secret this source needs is stored")
	last_synced_at: datetime | None = None
	last_sync_status: str | None = Field(default=None, description="Success, Partial or Failed")
	last_sync_message: str | None = Field(default=None, description="Counts, warnings and errors")
	provider_count: int
	active_provider_count: int


class ServiceProviderSourceListData(BaseModel):
	items: list[ServiceProviderSourceItem]


class ServiceProviderSyncQueued(BaseModel):
	source: str
	job_id: str
	queued: bool


class ServiceProviderSourceRef(Body):
	source_name: NonBlank = Field(max_length=140)


@route("", methods=("GET",), summary="List service provider sources", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="List service provider sources",
	description="Each source with its last sync result and how many providers it owns. "
	+ "Credentials are never returned.",
	tags=["Service Providers"],
	response_model=ServiceProviderSourceListData,
)
def list_service_provider_sources(**kwargs):
	"""List the sources, for the admin screen that starts a sync."""
	return success_response(data={"items": list_sources()}, message=_("Service provider sources retrieved"))


# TODO(STG-454): this manual trigger is temporary. The path and the contract of this endpoint
# will change; the daily job (tasks.enqueue_service_provider_syncs) is what normally feeds the
# directory. When it changes, update this route and its tier, regenerate openapi/, apisix/ and
# postman/, and revise docs/service-provider-directory.md section 4.4.
@route(
	"/<source_name>/sync",
	methods=("POST",),
	status=202,
	summary="Sync a service provider source",
	tier="officer-core",
)
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ServiceProviderSourceRef)
@api_doc(
	summary="Sync a service provider source",
	description="TEMPORARY: the path and contract of this endpoint will change. "
	+ "Queue a one-off sync of the whole source. The directory gets new providers, "
	+ "updated names and areas, and providers the source no longer lists are removed (or "
	+ "deactivated when something still links to them). A provider keeps its ID across syncs. "
	+ "The result appears on the source (last_sync_status, last_sync_message). "
	+ "409 when a sync of this source is already queued or running; 400 when it is disabled.",
	tags=["Service Providers"],
	response_model=ServiceProviderSyncQueued,
)
def sync_service_provider_source(source_name: str, **kwargs):
	"""Queue the sync; the work runs in the background."""
	job_id = enqueue_sync(source_name)
	return success_response(
		data={"source": source_name, "job_id": job_id, "queued": True},
		message=_("Sync queued"),
	)
