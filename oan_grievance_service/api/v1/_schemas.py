# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Request-schema building blocks shared by the v1 endpoints."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def blank_to_none(value):
	"""Before-validator: a blank string means "not set"."""
	if isinstance(value, str) and not value.strip():
		return None
	return value


class Body(BaseModel):
	"""Rejects unknown fields. `cmd` is added by the RPC request layer, not the client."""

	model_config = ConfigDict(extra="forbid")

	@model_validator(mode="before")
	@classmethod
	def _drop_cmd(cls, data):
		if isinstance(data, dict):
			return {key: value for key, value in data.items() if key != "cmd"}
		return data


class PartialBody(Body):
	"""Base for PATCH schemas: only the fields the client sent are dumped.

	`validate_request` calls `model_dump()` itself and hands the result to the handler, so
	the handler never sees the model. Without `exclude_unset`, every omitted field would
	arrive as None and be read as "set to null". Subclass this instead of overriding
	`model_dump` again; the override can go once `validate_request` takes an `exclude_unset`
	option (tracked for oan_auth_service).
	"""

	def model_dump(self, **kwargs):
		return super().model_dump(**{"exclude_unset": True, **kwargs})
