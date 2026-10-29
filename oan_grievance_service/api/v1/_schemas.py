# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Request-schema building blocks shared by the v1 endpoints."""

from typing import Annotated, Literal, NoReturn

from pydantic import (
	BaseModel,
	BeforeValidator,
	ConfigDict,
	Field,
	StringConstraints,
	ValidationError,
	model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from oan_grievance_service.services import response_body

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

Decision = Annotated[
	Literal["Approved", "Rejected"],
	BeforeValidator(lambda value: value.strip().title() if isinstance(value, str) else value),
]


def blank_to_none(value):
	"""Before-validator: a blank string means "not set"."""
	if isinstance(value, str) and not value.strip():
		return None
	return value


# Free-text limits from the Form Validation Summary ("Backend gaps"). The frontend stops
# input at the same numbers; these make a direct API call hit them too. A plain limit is
# `Annotated[str, StringConstraints(strip_whitespace=True, max_length=N)]` on the field:
# the surrounding whitespace is dropped before the length is checked, and a longer value
# is refused, never cut to fit.
ACTION_TAKEN_MAX = 500
RESOLUTION_SUMMARY_MAX = 2000
INTERNAL_NOTE_MAX = 2000
REJECTION_COMMENT_MIN = 20
REJECTION_COMMENT_MAX = 1000
JUSTIFICATION_MAX = 1000
DESIRED_OUTCOME_MAX = 1000
DEFERRAL_REASON_MAX = 1000
PERSON_NAME_MAX = 140
TEMPLATE_TITLE_MAX = 140


def refuse(field: str, kind: str, message: str, value, **ctx) -> NoReturn:
	"""Fail a model validator on `field`, so the 400 names it.

	A plain `ValueError` raised from a `model_validator` has no location and would be
	reported under a blank key. `message` may use the `ctx` names as placeholders.
	"""
	raise ValidationError.from_exception_data(
		"request",
		[InitErrorDetails(type=PydanticCustomError(kind, message, ctx), loc=(field,), input=value)],
	)


def check_response_text(value: str | None) -> str | None:
	"""Field validator for a department response sent as one text.

	A response may come as one text instead of `action_taken` and `resolution_summary`; when
	that text is written in the two-part form, each part keeps its own limit, so the single
	field cannot be used to get round them.
	"""
	parts = response_body.split(value)
	if not parts:
		return value
	if len(parts["action_taken"]) > ACTION_TAKEN_MAX:
		raise PydanticCustomError(
			"action_taken_too_long",
			"Action taken must not exceed {max_length} characters.",
			{"max_length": ACTION_TAKEN_MAX},
		)
	if len(parts["resolution_summary"]) > RESOLUTION_SUMMARY_MAX:
		raise PydanticCustomError(
			"resolution_summary_too_long",
			"Resolution summary must not exceed {max_length} characters.",
			{"max_length": RESOLUTION_SUMMARY_MAX},
		)
	return value


class DecisionRequest(BaseModel):
	"""A decide request: approve, or reject with a reasoned comment.

	A rejection needs a `note` of 20 to 1000 characters; an approval note is free text, as
	before. The rule reads both fields, so it does not depend on their order.
	"""

	decision: Decision = Field(..., description="Approved or Rejected")
	note: str | None = Field(None, description="Required when rejecting: 20 to 1000 characters")

	@model_validator(mode="after")
	def _rejection_needs_comment(self):
		if self.decision != "Rejected":
			return self
		length = len((self.note or "").strip())
		if length < REJECTION_COMMENT_MIN:
			refuse(
				"note",
				"rejection_comment_too_short",
				"A rejection needs a comment of at least {min_length} characters.",
				self.note,
				min_length=REJECTION_COMMENT_MIN,
			)
		if length > REJECTION_COMMENT_MAX:
			refuse(
				"note",
				"rejection_comment_too_long",
				"A rejection comment must not exceed {max_length} characters.",
				self.note,
				max_length=REJECTION_COMMENT_MAX,
			)
		return self


class Body(BaseModel):
	"""Rejects unknown fields. `validate_request` already drops the RPC layer's `cmd`.

	For PATCH schemas, pass `exclude_unset=True` to `validate_request` so omitted fields
	stay omitted instead of arriving as None.
	"""

	model_config = ConfigDict(extra="forbid")
