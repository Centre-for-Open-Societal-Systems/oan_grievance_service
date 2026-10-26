# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Service provider directory: search for the grievance form's provider dropdown.

Search reads the local Grievance Service Provider directory only; it never calls an
external source. A grievance stores the provider ID, so a renamed provider changes
nothing on old grievances. Access follows the DocType's own permission: whoever may read a
Grievance Service Provider may search and look one up.
"""

from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_permission,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.grievance_masters.doctype.grievance_service_provider.grievance_service_provider import (
	MAX_PAGE_SIZE,
	MIN_SEARCH_LENGTH,
	get_provider,
	provider_items,
	search_providers,
)

route = prefixed("/api/v1/service-providers")


class ServiceProviderParent(BaseModel):
	id: str
	name: str


class ServiceProviderArea(BaseModel):
	id: str
	name: str
	path_label: str = Field(description="Names from the region down to the area, in order")


class ServiceProviderItem(BaseModel):
	id: str = Field(description="Provider ID, such as SP-00042. Store this on the grievance.")
	name: str
	type: str
	parent: ServiceProviderParent | None = None
	area: ServiceProviderArea | None = Field(default=None, description="Empty for a nationwide provider")
	registration_number: str | None = None
	external_ref: str | None = Field(
		default=None,
		description="The source's own ID, such as the A2C bank ID PB-0452; empty for a hand-made provider",
	)
	registered_region: str | None = Field(
		default=None, description="The region as the source wrote it; set even when it matches no area"
	)
	logo: str | None = Field(
		default=None, description="As the source gave it, often a path relative to the source's own host"
	)


class ServiceProviderDetail(ServiceProviderItem):
	is_active: bool


class ServiceProviderListData(BaseModel):
	items: list[ServiceProviderItem]
	pagination: dict


class ListServiceProviders(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	page_size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE, description="Items per page")
	search: str | None = Field(
		default=None,
		max_length=100,
		description="Case-insensitive match on name, registration number or parent name. "
		f"At least {MIN_SEARCH_LENGTH} characters when set.",
	)
	provider_type: str | None = Field(
		default=None,
		max_length=140,
		description="The name of a Grievance Service Provider Type, such as Bank",
	)
	area: str | None = Field(
		default=None,
		max_length=140,
		description="Area ID or path code. Keeps providers inside that area or above it, plus nationwide ones.",
	)

	_blank = field_validator("search", "provider_type", "area", mode="before")(blank_to_none)

	@field_validator("search")
	@classmethod
	def _search_long_enough(cls, value: str | None) -> str | None:
		if value is None:
			return None
		value = value.strip()
		if len(value) < MIN_SEARCH_LENGTH:
			raise ValueError(f"must be at least {MIN_SEARCH_LENGTH} characters")
		return value


class ServiceProviderRef(Body):
	provider_id: NonBlank = Field(max_length=140)


@route("", methods=("GET",), summary="Search service providers", tier="officer-core")
@handle_api_errors
@require_permission("Grievance Service Provider", "read")
@validate_request(ListServiceProviders)
@api_doc(
	summary="Search service providers",
	description="Active providers for the grievance form's dropdown. search matches the provider's name, "
	+ "registration number and parent name. area keeps providers inside that area or above it, plus "
	+ "nationwide providers. Names that start with the search text come first, then the most local area.",
	tags=["Service Providers"],
	response_model=ServiceProviderListData,
)
def list_service_providers(
	search: str | None = None,
	provider_type: str | None = None,
	area: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""Search the directory. `page` and `page_size` also accept str: see list_assignments."""
	params = PageParams(page=page, page_size=page_size)
	rows, total = search_providers(
		search=search,
		provider_type=provider_type,
		area=area,
		page=params.page,
		page_size=params.page_size,
	)
	return success_response(
		data={
			"items": provider_items(rows),
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Service providers retrieved"),
	)


@route("/<provider_id>", methods=("GET",), summary="Get a service provider", tier="officer-core")
@handle_api_errors
@require_permission("Grievance Service Provider", "read")
@validate_request(ServiceProviderRef)
@api_doc(
	summary="Get a service provider",
	description="One provider by ID, active or not, so a saved grievance or draft can still show its name.",
	tags=["Service Providers"],
	response_model=ServiceProviderDetail,
)
def get_service_provider(provider_id: str, **kwargs):
	"""Return one provider, including an inactive one."""
	return success_response(
		data=provider_items([get_provider(provider_id)], with_status=True)[0],
		message=_("Service provider retrieved"),
	)
