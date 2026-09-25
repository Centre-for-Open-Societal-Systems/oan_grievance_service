# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.services import hooks_handlers, identity, ticket_number

MIN_DESCRIPTION_LENGTH = 20

# Operational levels that may own a grievance. Macro containers (Country/Region/Zone)
# are rejected even when is_group=0; Woreda may be is_group=1 when it has child kebeles.
ALLOWED_FILING_LEVELS = frozenset(
	{
		"Woreda",
		"Kebele",
		"Village",
		"Ward",
		"Taluka",
		"Sub-County",
		"County",
		"District",
	}
)


def validate_filing_area(administrative_area: str):
	"""Domain rules for where a grievance may be filed.

	Link existence is Frappe's job; this enforces filing level and dissolved dates.
	"""
	area = frappe.get_doc("Grievance Administrative Area", administrative_area)
	if area.level_name not in ALLOWED_FILING_LEVELS or (
		not area.parent_administrative_area and area.is_group
	):
		frappe.throw(
			_(
				"Grievances cannot be attached to administrative level '{0}'. "
				"Please select an operational area such as a Woreda or Kebele."
			).format(area.level_name or _("Unknown")),
			title=_("Invalid Administrative Area"),
		)
	if area.valid_to and str(area.valid_to) <= frappe.utils.today():
		frappe.throw(
			_("The selected Administrative Area '{0}' has been dissolved or reorganized.").format(
				administrative_area
			),
			title=_("Dissolved Administrative Area"),
		)
	return area


class GrievanceSubmissionPayload(BaseModel):
	model_config = {"extra": "allow"}

	contact_mobile: str | None = None
	description: str | None = Field(default=None, min_length=MIN_DESCRIPTION_LENGTH)
	service_category: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None

	@field_validator("contact_mobile")
	@classmethod
	def _validate_mobile(cls, v):
		if not v or not str(v).strip():
			return v
		try:
			return identity.validate_mobile(str(v).strip())
		except (frappe.ValidationError, Exception) as exc:
			message = str(exc.args[0]) if isinstance(exc.args, tuple) and exc.args else str(exc)
			raise ValueError(message) from exc

	@field_validator("administrative_area")
	@classmethod
	def _validate_area(cls, v):
		if not v or not str(v).strip():
			return v
		area = str(v).strip()
		if frappe.db.exists("Grievance Administrative Area", area):
			try:
				validate_filing_area(area)
			except frappe.ValidationError as exc:
				message = str(exc.args[0]) if isinstance(exc.args, tuple) and exc.args else str(exc)
				raise ValueError(message) from exc
		return area

	@model_validator(mode="after")
	def _validate_category_and_type(self):
		cat = (self.service_category or "").strip()
		g_type = (self.grievance_type or "").strip()
		if cat and g_type:
			from oan_grievance_service.api.v1.grievance import resolve_grievance_type

			resolved_type = resolve_grievance_type(g_type, cat)
			if resolved_type and frappe.db.exists("Grievance Type", resolved_type):
				parent = frappe.db.get_value("Grievance Type", resolved_type, "service_category")
				if parent and parent != cat:
					raise ValueError(
						_("Grievance type {0} belongs to category {1}, not {2}.").format(
							frappe.bold(g_type),
							frappe.bold(parent),
							frappe.bold(cat),
						)
					)
		return self


def validate_submission_payload(payload: dict):
	"""Domain rules covered via GrievanceSubmissionPayload Pydantic schema."""
	if not isinstance(payload, dict):
		frappe.throw(_("Submission payload must be an object."), title=_("Invalid Payload"))
	GrievanceSubmissionPayload.model_validate(payload)
	return True


class Grievance(Document):
	def autoname(self):
		"""Nine-character ticket number: region, category, sequence, year.

		Named at insert so the sequence is allocated in the same transaction as
		the row it belongs to. See `services.ticket_number` for the encoding;
		the number is mirrored onto its own field because the FSD treats it as
		an attribute of the grievance, and reports and notifications read it by
		name.
		"""
		if self.name:
			return
		if getattr(self.flags, "is_draft_wizard", False) and not getattr(self.flags, "in_submit", False):
			key = self.client_submission_uuid or frappe.generate_hash(length=12)
			self.name = f"DRAFT-{key}"
			self.ticket_number = None
			return

		self.name = ticket_number.generate(self.administrative_area, self.service_category)
		self.ticket_number = self.name

	def _validate_mandatory(self):
		if getattr(self.flags, "is_draft_wizard", False) and not getattr(self.flags, "in_submit", False):
			return
		super()._validate_mandatory()

	def validate(self):
		self.keep_status_in_step_with_the_workflow()
		if getattr(self.flags, "is_draft_wizard", False) and not getattr(self.flags, "in_submit", False):
			return

		# Frappe already enforces reqd / Link / Select. Domain-only rules below.
		try:
			validate_submission_payload(
				{
					"contact_mobile": self.contact_mobile,
					"administrative_area": self.administrative_area,
					"service_category": self.service_category,
					"grievance_type": self.grievance_type,
					"description": self.description,
				}
			)
		except PydanticValidationError as e:
			# Desk / DocType path expects frappe.ValidationError; API gets pydantic details.
			parts = []
			for err in e.errors():
				loc = ".".join(str(item) for item in err["loc"])
				parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
			frappe.throw("; ".join(parts), title=_("Incomplete Submission"))
		self.set_administrative_area_metadata()
		self.record_the_workflow_move()

	# Workflow
	# --------
	# Frappe's engine moves a grievance by setting `workflow_state` and saving,
	# submitting or cancelling it, and the record of the move is handled from
	# the post-save methods -- one of which fires per kind of save.

	def keep_status_in_step_with_the_workflow(self):
		"""`workflow_state` is what the engine drives; `status` mirrors it so every
		reader -- the API, the list filters, the reports -- keeps its field."""
		if not self.workflow_state:
			self.workflow_state = self.status or "Draft"
		self.status = self.workflow_state

	def workflow_move_from(self):
		"""The state this save leaves, or None when the save is not a move."""
		if self.is_new():
			return None
		before = self.get_doc_before_save()
		if not before or before.workflow_state == self.workflow_state:
			return None
		return before.workflow_state

	# Frappe runs `validate` for a save and a submit only. A move between two
	# submitted states arrives as update_after_submit, a rejection as cancel, and
	# each has its own before-method; the sync must run from those too, or a move
	# on either path would leave `status` behind.

	def before_update_after_submit(self):
		self.keep_status_in_step_with_the_workflow()
		self.record_the_workflow_move()

	def before_cancel(self):
		self.keep_status_in_step_with_the_workflow()
		self.record_the_workflow_move()

	def record_the_workflow_move(self):
		from_state = self.workflow_move_from()
		if from_state:
			hooks_handlers.after_workflow_action(self, from_state)

	def validate_workflow(self):
		"""Frappe insists a new document enters the workflow at its first state.

		Every intake path inserts a Draft, so that holds for live traffic. It is
		relaxed for a new document so history can be loaded at the state it was
		actually in: a migrated case that closed two years ago was never Draft.
		"""
		if self.is_new():
			return
		super().validate_workflow()

	def set_administrative_area_metadata(self):
		"""Denormalise area_lft and capture immutable area_path_code snapshot."""
		if not self.administrative_area:
			return

		area = validate_filing_area(self.administrative_area)
		self.area_lft = area.lft
		if not self.area_path_code:
			self.area_path_code = area.path_code or area.name


def on_doctype_update():
	frappe.db.add_index("Grievance", ["area_lft"])
	# The escalation batch selects on this alone, so it is the whole schedule.
	frappe.db.add_index("Grievance", ["next_escalation_at"])
	frappe.db.add_index("Grievance", ["status", "sla_due_date"])
	frappe.db.add_index("Grievance", ["assigned_to", "status"])
	frappe.db.add_index("Grievance", ["submitter", "status"])
