# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Request-schema building blocks shared by the v1 endpoints."""

from typing import Annotated, Literal, NoReturn

from pydantic import (
	AfterValidator,
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

# Surrounding whitespace is dropped before a length is checked. Add the limit with `Field`:
# `Text | None = Field(None, max_length=500)`.
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
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
# input at the same numbers; these make a direct API call hit them too. A longer value is
# refused, never cut to fit. A limit used by one field sits on that field; a limit used by
# several is a type below. The numbers kept here are the ones a validator checks as well.
ACTION_TAKEN_MAX = 500  # also the first part of a response sent as one text
RESOLUTION_SUMMARY_MAX = 2000  # also the second part of a response sent as one text
INTERNAL_NOTE_MAX = 2000  # an internal note on /action and on /message
REJECTION_COMMENT_MIN = 20
REJECTION_COMMENT_MAX = 1000

ActionTaken = Annotated[Text, Field(max_length=ACTION_TAKEN_MAX)]
ResolutionSummary = Annotated[Text, Field(max_length=RESOLUTION_SUMMARY_MAX)]
PersonName = Annotated[Text, Field(max_length=140)]


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


# A department response as one text: when it is written in the two-part form, each part
# keeps its limit. Reused by every field that takes a response.
ResponseText = Annotated[NonBlank, AfterValidator(check_response_text)]


class DecisionRequest(BaseModel):
	"""A decide request: approve, or reject with a reasoned comment.

	A rejection needs a `note` of 20 to 1000 characters; an approval note is free text, as
	before. The rule reads both fields, so it does not depend on their order.
	"""

	decision: Decision = Field(..., description="Approved or Rejected")
	note: str | None = Field(None, description="Required when rejecting")

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
