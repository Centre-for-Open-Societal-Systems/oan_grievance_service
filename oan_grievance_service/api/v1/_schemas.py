# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Request-schema building blocks shared by the v1 endpoints."""

from typing import Annotated, Literal

from pydantic import (
	AfterValidator,
	BaseModel,
	BeforeValidator,
	ConfigDict,
	StringConstraints,
	ValidationInfo,
)
from pydantic_core import PydanticCustomError

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
# input at the same numbers; these make a direct API call hit them too.
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


def _too_long(label: str, limit: int) -> PydanticCustomError:
	return PydanticCustomError(
		"string_too_long",
		"{label} must not exceed {max_length} characters.",
		{"label": label, "max_length": limit},
	)


def check_max_chars(value: str, limit: int, label: str = "This field") -> str:
	"""Reject text longer than `limit` characters.

	Leading and trailing whitespace is not counted, and the value is passed on exactly as
	it was sent: a long value is refused, never trimmed to fit.
	"""
	if len(value.strip()) > limit:
		raise _too_long(label, limit)
	return value


def max_chars(limit: int, label: str = "This field"):
	"""`check_max_chars` as an `Annotated` validator."""
	return AfterValidator(lambda value: check_max_chars(value, limit, label))


ActionTaken = Annotated[str, max_chars(ACTION_TAKEN_MAX, "Action taken")]
ResolutionSummary = Annotated[str, max_chars(RESOLUTION_SUMMARY_MAX, "Resolution summary")]
InternalNote = Annotated[str, max_chars(INTERNAL_NOTE_MAX, "Internal note")]
Justification = Annotated[str, max_chars(JUSTIFICATION_MAX, "Justification")]
TemplateTitle = Annotated[NonBlank, max_chars(TEMPLATE_TITLE_MAX, "Title")]
DesiredOutcome = Annotated[str, max_chars(DESIRED_OUTCOME_MAX, "Desired outcome")]
DeferralReason = Annotated[str, max_chars(DEFERRAL_REASON_MAX, "Reason")]
PersonName = Annotated[NonBlank, max_chars(PERSON_NAME_MAX, "Full name")]


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
		raise _too_long("Action taken", ACTION_TAKEN_MAX)
	if len(parts["resolution_summary"]) > RESOLUTION_SUMMARY_MAX:
		raise _too_long("Resolution summary", RESOLUTION_SUMMARY_MAX)
	return value


def check_rejection_comment(value: str | None, info: ValidationInfo) -> str | None:
	"""Field validator for the `note` of a decision: a rejection needs a reasoned comment.

	Use with `Field(validate_default=True)` so an omitted comment is refused too. An approval
	note is free text, as before.
	"""
	if info.data.get("decision") != "Rejected":
		return value
	text = (value or "").strip()
	if len(text) < REJECTION_COMMENT_MIN:
		raise PydanticCustomError(
			"rejection_comment_too_short",
			"A rejection needs a comment of at least {min_length} characters.",
			{"min_length": REJECTION_COMMENT_MIN},
		)
	if len(text) > REJECTION_COMMENT_MAX:
		raise _too_long("The rejection comment", REJECTION_COMMENT_MAX)
	return value


class Body(BaseModel):
	"""Rejects unknown fields. `validate_request` already drops the RPC layer's `cmd`.

	For PATCH schemas, pass `exclude_unset=True` to `validate_request` so omitted fields
	stay omitted instead of arriving as None.
	"""

	model_config = ConfigDict(extra="forbid")
