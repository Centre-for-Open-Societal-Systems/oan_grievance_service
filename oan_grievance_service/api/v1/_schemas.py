# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Request-schema building blocks shared by the v1 endpoints."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def blank_to_none(value):
	"""Before-validator: a blank string means "not set"."""
	if isinstance(value, str) and not value.strip():
		return None
	return value


class Body(BaseModel):
	"""Rejects unknown fields. `validate_request` already drops the RPC layer's `cmd`.

	For PATCH schemas, pass `exclude_unset=True` to `validate_request` so omitted fields
	stay omitted instead of arriving as None.
	"""

	model_config = ConfigDict(extra="forbid")
