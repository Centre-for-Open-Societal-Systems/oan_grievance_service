# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import hooks_handlers, identity, ticket_number

MIN_DESCRIPTION_LENGTH = 20

# Fields a submitted grievance only changes through an approved Grievance Change
# Request. System paths (routing, escalation) write them with db_set and are not
# requests; every save that changes one of these must carry the request.
REQUESTABLE_FIELDS = (
	"assigned_dept",
	"assigned_to",
	"sla_due_date",
	"service_category",
	"grievance_type",
)

# What the submitter filed, and the filing's own bookkeeping. Fixed once the case
# exists; only db_set paths (anonymity, migrations) still write them.
FILED_FIELDS = (
	"ticket_number",
	"submission_channel",
	"client_submission_uuid",
	"submitter_type",
	"submitter",
	"submitter_name",
	"contact_mobile",
	"contact_email",
	"assisted_by_officer",
	"consent_given",
	"consent_recorded_at",
	"administrative_area",
	"administrative_unit",
	"area_lft",
	"area_path_code",
	"description",
	"desired_outcome",
)

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
	can_request_more_info: bool | int = True

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
			from oan_grievance_service.services.resolvers import resolve_grievance_type

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
		the number is mirrored onto its own field because it is
		an attribute of the grievance, and reports and notifications read it by
		name.
		"""
		if self.name:
			return
		self.name = ticket_number.generate(self.administrative_area, self.service_category)
		self.ticket_number = self.name

	def _validate_mandatory(self):
		if not self.contact_mobile:
			field = self.meta.get_field("contact_mobile")
			if field and field.reqd:
				field.reqd = 0
				try:
					super()._validate_mandatory()
				finally:
					field.reqd = 1
				return
		super()._validate_mandatory()

	def validate(self):
		self.keep_status_in_step_with_the_workflow()
		if self.is_new():
			self.validate_filing()
			self.set_administrative_area_metadata()
		else:
			self.guard_terminal_state()
			self.guard_filed_fields()
			self.guard_requestable_fields()
		self.validate_classification_is_active()
		self.record_the_workflow_move()

	def validate_filing(self):
		"""Domain rules a case must meet when it is filed. Frappe already enforces
		reqd / Link / Select."""
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

	def validate_classification_is_active(self):
		"""A case is filed under an active category and type, the ones the wizard offers.

		Checked when the case is filed and whenever the classification is changed. A case already filed keeps its category
		and type however they are retired afterwards, so its later moves are never blocked.
		"""
		filing = self.is_new()
		for fieldname, doctype, label in (
			("service_category", "Grievance Service Category", _("Service category")),
			("grievance_type", "Grievance Type", _("Grievance type")),
		):
			value = self.get(fieldname)
			if not value or not (filing or self.has_value_changed(fieldname)):
				continue
			if not frappe.db.get_value(doctype, value, "is_active"):
				frappe.throw(
					_("{0} '{1}' is no longer available. Choose an active one.").format(label, value),
					frappe.ValidationError,
				)

	# Workflow
	# --------
	# Frappe's engine moves a grievance by setting `workflow_state` and saving it;
	# `validate` records the move.

	def keep_status_in_step_with_the_workflow(self):
		"""`workflow_state` is what the engine drives; `status` mirrors it so every
		reader -- the API, the list filters, the reports -- keeps its field."""
		if not self.workflow_state:
			self.workflow_state = self.status or C.STATE_SUBMITTED
		self.status = self.workflow_state

	def workflow_move_from(self):
		"""The state this save leaves, or None when the save is not a move."""
		if self.is_new():
			return None
		before = self.get_doc_before_save()
		if not before or before.workflow_state == self.workflow_state:
			return None
		return before.workflow_state

	def guard_terminal_state(self):
		"""A Closed or Rejected case is final; only db_set paths still write to it."""
		before = self.get_doc_before_save()
		if not before:
			return
		from oan_grievance_service.services.sla import terminal_states

		if before.workflow_state in terminal_states():
			frappe.throw(
				_("A {0} grievance cannot be changed.").format(_(before.workflow_state)),
				frappe.ValidationError,
				title=_("Grievance Closed"),
			)

	def guard_filed_fields(self):
		changed = [f for f in FILED_FIELDS if self.has_value_changed(f)]
		if changed:
			frappe.throw(
				_("{0} cannot be changed once the grievance is filed.").format(
					", ".join(_(self.meta.get_label(f)) for f in changed)
				),
				title=_("Filed Fields Are Fixed"),
			)

	def record_the_workflow_move(self):
		from_state = self.workflow_move_from()
		if from_state:
			hooks_handlers.after_workflow_action(self, from_state)

	def validate_workflow(self):
		"""Frappe insists a new document enters the workflow at its first state.

		Every intake path inserts at Submitted, so that holds for live traffic. It is
		relaxed for a new document so history can be loaded at the state it was
		actually in: a migrated case that closed two years ago was never Submitted.
		"""
		if self.is_new():
			return
		super().validate_workflow()

	# Change requests
	# ---------------
	# Grievance Change Request holds who asked and who approved; what a change means
	# for the case - which values are allowed and what else moves with them - lives
	# here, next to the fields themselves.

	def guard_requestable_fields(self):
		if self.flags.change_request:
			return
		before = self.get_doc_before_save()
		if not before:
			return
		changed = []
		for f in REQUESTABLE_FIELDS:
			if self.has_value_changed(f):
				if (
					f in ("assigned_dept", "assigned_to")
					and not before.get(f)
					and self.status == C.STATE_SUBMITTED
				):
					continue
				changed.append(f)
		if changed:
			frappe.throw(
				_("{0} can only be changed through an approved change request.").format(
					", ".join(_(self.meta.get_label(f)) for f in changed)
				),
				title=_("Change Request Required"),
			)

	def validate_requested_change(self, fieldname, new_value):
		"""The rules a requested value must meet before anyone is asked to approve it."""
		if fieldname not in REQUESTABLE_FIELDS:
			frappe.throw(
				_("Field '{0}' cannot be changed through a change request.").format(fieldname),
				title=_("Field Not Requestable"),
			)

		if fieldname == "assigned_dept" and not frappe.db.exists("Grievance Department", new_value):
			frappe.throw(
				_("Department '{0}' does not exist.").format(new_value), title=_("Invalid Department")
			)

		if fieldname == "assigned_to" and new_value and not frappe.db.exists("User", new_value):
			frappe.throw(_("Officer '{0}' does not exist.").format(new_value), title=_("Invalid Officer"))

		if fieldname == "service_category" and not frappe.db.exists("Grievance Service Category", new_value):
			frappe.throw(
				_("Service Category '{0}' does not exist.").format(new_value),
				title=_("Invalid Service Category"),
			)

		if fieldname == "grievance_type" and new_value and not frappe.db.exists("Grievance Type", new_value):
			frappe.throw(
				_("Grievance Type '{0}' does not exist.").format(new_value), title=_("Invalid Grievance Type")
			)

		if fieldname == "sla_due_date":
			self._validate_deferral(new_value)

	def _validate_deferral(self, new_value):
		"""A deferral only moves the deadline out, and by no more than policy allows."""
		from frappe.utils import get_datetime

		from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
			max_deferral_days,
		)

		if not self.sla_due_date:
			frappe.throw(_("This grievance has no SLA deadline to defer."), title=_("No SLA Deadline"))
		days = (get_datetime(new_value) - get_datetime(self.sla_due_date)).total_seconds() / 86400
		if days <= 0:
			frappe.throw(_("A deferral must move the deadline later."), title=_("Invalid Deferral"))
		if days > max_deferral_days():
			frappe.throw(
				_("A deferral may not exceed {0} days.").format(max_deferral_days()),
				title=_("Deferral Too Long"),
			)

	def apply_change_request(self, request):
		"""Write an approved request's values through a normal save, so the Version log
		records the change and on_update runs."""
		for row in request.changes:
			self.set(row.fieldname, row.new_value or None)
		self.flags.change_request = request.name
		self.flags.change_request_doc = request
		from oan_grievance_service.services import reassignment

		self.flags.reassignment = reassignment.is_reassignment(request)
		self.save(ignore_permissions=True)

	def reject_change_request(self, request):
		"""A rejected change request records the decision note and timeline."""
		pass

	def on_update(self):
		if self.flags.change_request:
			if getattr(self.flags, "reassignment", False):
				from oan_grievance_service.services import reassignment

				reassignment.on_applied(self, getattr(self.flags, "change_request_doc", None))
			else:
				self.react_to_approved_change()
		elif self.has_value_changed("service_category") and self.sla_due_date:
			from oan_grievance_service.services import sla

			sla.recalculate_sla_on_category_change(self)

	def react_to_approved_change(self):
		"""Fields that move with a requested change."""
		from frappe.utils import get_datetime

		from oan_grievance_service.services import sla

		if self.has_value_changed("service_category"):
			sla.recalculate_sla_on_category_change(self)

		if self.has_value_changed("sla_due_date"):
			# Reminders reopen against the new deadline. A case already climbing keeps its
			# rung's remaining time, shifted by the same amount; one that has not escalated
			# is re-armed against the new deadline.
			before = self.get_doc_before_save()
			if before and before.sla_due_date:
				shift = get_datetime(self.sla_due_date) - get_datetime(before.sla_due_date)
				updates = {"reminder_50_sent": 0, "reminder_80_sent": 0}
				if self.escalated and self.next_escalation_at:
					updates["next_escalation_at"] = get_datetime(self.next_escalation_at) + shift
				self.db_set(updates, update_modified=False)
				if "next_escalation_at" not in updates:
					sla.arm_escalation(self, sla.resolve_policy(self.service_category))

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
	# The dashboard rollup counts events by the day they happened; each refresh
	# reads only the last few days of each, so each needs its own index.
	frappe.db.add_index("Grievance", ["creation"])
	frappe.db.add_index("Grievance", ["resolved_at"])
	frappe.db.add_index("Grievance", ["escalated_at"])
