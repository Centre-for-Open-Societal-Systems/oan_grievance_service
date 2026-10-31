"""Demo data for a development or staging site: categories, types, departments and staff.

Not part of install or migrate. Import it on demand:

	bench --site <site> execute oan_grievance_service.setup.demo_data.seed

It goes through the same services the API uses, so desks, reporting lines, roles and the SLA
rows come out exactly as if an administrator had entered them. Running it again changes
nothing but passwords, which are reset to `PASSWORD`.

Every account here shares the password `PASSWORD` and is not asked to replace it. That is
for development only: never run this against a site that holds real people.

Per service category it makes a department with one desk, and four people on that desk:
a Category Admin (L3), an L2 and an L1 officer who report up that chain, and a Reviewer.
A single Grievance Admin runs the whole platform.
"""

import frappe
from frappe import _
from frappe.utils.password import update_password

from oan_grievance_service.grievance_sla.doctype.grievance_sla_configuration.grievance_sla_configuration import (
	save_sla_configuration,
)
from oan_grievance_service.services import account, officer
from oan_grievance_service.services import constants as C

PASSWORD = "admin@123"
DOMAIN = "grievance.local"

PLATFORM_ADMIN = ("admin", "Selamawit Haile", "+251911000001")

# Per category: name, ticket code, sort order, SLA days, department, email prefix, grievance
# types, then (full name, phone) for the Category Admin, L2, L1 and Reviewer.
# The first six categories are the ones install seeds; their codes and SLA days are kept.
CATEGORIES = [
	{
		"name": "Inputs",
		"code": "001",
		"order": 1,
		"sla_days": 14,
		"department": "Agricultural Inputs Directorate",
		"slug": "inputs",
		"types": [
			"Fertilizer Shortage",
			"Seed Quality Issue",
			"Pesticide Availability",
			"Fertilizer Price Overcharging",
			"Input Voucher Not Honoured",
		],
		"people": [
			("Abebe Bekele", "+251911100001"),
			("Tigist Alemayehu", "+251911100002"),
			("Dawit Tesfaye", "+251911100003"),
			("Marta Girma", "+251911100004"),
		],
	},
	{
		"name": "Schemes",
		"code": "002",
		"order": 2,
		"sla_days": 14,
		"department": "Agricultural Support Schemes Directorate",
		"slug": "schemes",
		"types": [
			"Direct Benefit Transfer Delay",
			"Subsidy Allocation Issue",
			"Beneficiary Left Out of Scheme",
			"Productive Safety Net Enrolment Error",
		],
		"people": [
			("Yohannes Mulugeta", "+251911200001"),
			("Hanna Desta", "+251911200002"),
			("Getachew Worku", "+251911200003"),
			("Rahel Tadesse", "+251911200004"),
		],
	},
	{
		"name": "Payments",
		"code": "003",
		"order": 3,
		"sla_days": 7,
		"department": "Farmer Payments Directorate",
		"slug": "payments",
		"types": [
			"Payment Failure",
			"Delayed Payment Settlement",
			"Wrong Amount Paid",
			"Mobile Money Wallet Not Credited",
		],
		"people": [
			("Berhanu Assefa", "+251911300001"),
			("Meseret Kebede", "+251911300002"),
			("Solomon Negash", "+251911300003"),
			("Bethlehem Yilma", "+251911300004"),
		],
	},
	{
		"name": "Credit",
		"code": "004",
		"order": 4,
		"sla_days": 14,
		"department": "Agricultural Credit and Finance Directorate",
		"slug": "credit",
		"types": [
			"Loan Disbursement Delay",
			"Interest Rate Discrepancy",
			"Loan Application Rejected Without Reason",
			"Collateral Release Delay",
		],
		"people": [
			("Fikru Haile", "+251911400001"),
			("Almaz Gebre", "+251911400002"),
			("Henok Abera", "+251911400003"),
			("Selam Tekle", "+251911400004"),
		],
	},
	{
		"name": "Markets",
		"code": "005",
		"order": 5,
		"sla_days": 10,
		"department": "Agricultural Marketing Directorate",
		"slug": "markets",
		"types": [
			"Price Reporting Dispute",
			"Market Access Obstruction",
			"Unfair Weighing at Collection Point",
			"Cooperative Payment Withheld",
		],
		"people": [
			("Tesfaye Demissie", "+251911500001"),
			("Aster Wolde", "+251911500002"),
			("Mulugeta Hailu", "+251911500003"),
			("Kidist Bogale", "+251911500004"),
		],
	},
	{
		"name": "Extension Services",
		"code": "007",
		"order": 7,
		"sla_days": 10,
		"department": "Agricultural Extension Directorate",
		"slug": "extension",
		"types": [
			"Extension Agent Not Visiting",
			"Training Not Delivered",
			"Wrong Agronomic Advice",
			"Demonstration Plot Neglected",
		],
		"people": [
			("Eyob Zewdie", "+251911600001"),
			("Tsion Mekonnen", "+251911600002"),
			("Nahom Belay", "+251911600003"),
			("Liya Ayele", "+251911600004"),
		],
	},
	{
		"name": "Irrigation and Water",
		"code": "008",
		"order": 8,
		"sla_days": 10,
		"department": "Irrigation Development Authority",
		"slug": "irrigation",
		"types": [
			"Irrigation Water Not Reaching Plot",
			"Canal Maintenance Neglected",
			"Water User Association Dispute",
			"Pump or Drip Equipment Fault",
		],
		"people": [
			("Wondimu Kassa", "+251911700001"),
			("Birtukan Seyoum", "+251911700002"),
			("Amanuel Teshome", "+251911700003"),
			("Saron Debebe", "+251911700004"),
		],
	},
	{
		"name": "Land and Tenure",
		"code": "009",
		"order": 9,
		"sla_days": 21,
		"department": "Rural Land Administration Directorate",
		"slug": "land",
		"types": [
			"Land Certificate Delay",
			"Boundary Dispute",
			"Land Holding Registration Error",
			"Land Use Right Transfer Delay",
		],
		"people": [
			("Girma Wondimagegn", "+251911800001"),
			("Zenebech Alemu", "+251911800002"),
			("Tadesse Gashaw", "+251911800003"),
			("Eden Mitiku", "+251911800004"),
		],
	},
	{
		"name": "Livestock and Veterinary",
		"code": "00A",
		"order": 10,
		"sla_days": 7,
		"department": "Livestock and Veterinary Services Directorate",
		"slug": "livestock",
		"types": [
			"Vaccination Not Available",
			"Veterinary Clinic Unstaffed",
			"Livestock Disease Outbreak Unreported",
			"Artificial Insemination Service Delay",
		],
		"people": [
			("Kebede Lemma", "+251911900001"),
			("Mekdes Shiferaw", "+251911900002"),
			("Alemayehu Gudeta", "+251911900003"),
			("Helen Berhe", "+251911900004"),
		],
	},
	{
		"name": "Crop Insurance",
		"code": "00B",
		"order": 11,
		"sla_days": 14,
		"department": "Agricultural Insurance Directorate",
		"slug": "insurance",
		"types": [
			"Claim Not Settled",
			"Premium Deduction Dispute",
			"Damage Assessment Delay",
			"Policy Not Issued After Payment",
		],
		"people": [
			("Samuel Gebremedhin", "+251912000001"),
			("Genet Tilahun", "+251912000002"),
			("Biniyam Feleke", "+251912000003"),
			("Yeshi Mengistu", "+251912000004"),
		],
	},
]

# Each person's position on the desk, in the order they are created: a later one reports to the
# one before it (L1 to the L2, the L2 to the Category Admin).
POSITIONS = [
	("admin", "Officer", "L3", "Category Admin"),
	("l2", "Officer", "L2", "Senior Nodal Officer"),
	("l1", "Officer", "L1", "Nodal Officer"),
	("reviewer", "Reviewer", None, "Review Officer"),
]


def seed():
	"""Create everything above that is missing, set every demo password, and commit."""
	frappe.flags.in_demo_seed = True
	made = {
		"categories": _seed_categories(),
		"types": _seed_types(),
		"platform_admin": _seed_platform_admin(),
		"staff": _seed_staff(),
	}
	# Explicit commit is required for standalone bench execute seed script
	frappe.db.commit()  # nosemgrep
	frappe.msgprint(_("Demo data ready. Every account's password is {0}.").format(PASSWORD))
	return made


def email_of(slug: str, position: str) -> str:
	return f"{slug}.{position}@{DOMAIN}"


def _seed_categories() -> list[str]:
	made = []
	for entry in CATEGORIES:
		name, code = entry["name"], entry["code"]
		if not (
			frappe.db.exists("Grievance Service Category", name)
			or frappe.db.exists("Grievance Service Category", {"code": code})
		):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": name,
					"code": code,
					"sort_order": entry["order"],
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
			made.append(name)
		if not frappe.db.exists("Grievance SLA Configuration", {"service_category": name}):
			save_sla_configuration(service_category=name, sla_days=entry["sla_days"])
	return made


def _seed_types() -> list[str]:
	made = []
	for entry in CATEGORIES:
		for type_name in entry["types"]:
			if frappe.db.exists(
				"Grievance Type", {"service_category": entry["name"], "type_name": type_name}
			):
				continue
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": type_name,
					"service_category": entry["name"],
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
			made.append(type_name)
	return made


def _seed_platform_admin() -> str:
	prefix, full_name, phone = PLATFORM_ADMIN
	email = f"{prefix}@{DOMAIN}"
	user = account.new_or_existing_user(email)
	account.write_user(user, {"full_name": full_name, "phone": phone}, C.ROLE_ADMIN)
	_set_password(email)
	return email


def _seed_staff() -> list[str]:
	made = []
	for entry in CATEGORIES:
		department, category = entry["department"], entry["name"]
		above = None
		for (position, role, level, designation), (full_name, phone) in zip(
			POSITIONS, entry["people"], strict=True
		):
			email = email_of(entry["slug"], position)
			if not officer.role_of(email):
				officer.create(
					full_name=full_name,
					email=email,
					role=role,
					level=level,
					designation=designation,
					department=department,
					service_categories=[category],
					phone=phone,
					reports_to=above if role == "Officer" and level != "L3" else None,
					# The Category Admin opens the department, and its desk, for everyone after them.
					create_department=level == "L3"
					and not frappe.db.exists("Grievance Department", department),
				)
				made.append(email)
			_set_password(email)
			if role == "Officer":
				above = email
	return made


def _set_password(email: str) -> None:
	"""A password the person keeps: unlike a temporary one, it does not have to be replaced."""
	update_password(user=email, pwd=PASSWORD)
	frappe.db.set_value("User", email, account.MUST_CHANGE_PASSWORD_FIELD, 0, update_modified=False)
