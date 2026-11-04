"""Demo farmers and grievances for a development or staging site.

Not part of install or migrate. Run it after `demo_data.seed`, which creates the categories,
desks and officers the cases are routed to:

	bench --site <site> execute oan_grievance_service.setup.demo_data.seed
	bench --site <site> execute oan_grievance_service.setup.demo_cases.seed

Farmers are Ethiopian smallholders in real woredas and kebeles, each with a Fayda ID, a mobile
number and a login (password `PASSWORD`, shared with the demo staff). Their grievances run the
real workflow through the same services the API uses, one case per walk: routed to a desk,
taken up, asked for more information, answered, resolved, confirmed, reopened, escalated,
auto-closed or rejected. Every step is stamped with a moment in the past, so the SLA clock, the
holds, the escalation schedule and the dashboards read as a service that has been running for
a few months.

Notifications are not queued: nothing here may reach a real phone or inbox.
Running it twice adds a second set of farmers and cases; start from a clean site.

Never run this against a site that holds real people.
"""

import contextlib
import random
import sys
from datetime import datetime, timedelta

import frappe
from frappe.utils import data as frappe_data
from frappe.utils.password import update_password

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import identity, lifecycle, response_body, routing, sla
from oan_grievance_service.setup.demo_data import CATEGORIES, PASSWORD, email_of

SEED = 20261031
CASES = 72
FARMERS = 54
WINDOW_DAYS = 120
FARMER_DOMAIN = "farmers.grievance.local"

# A woreda each, with the people who live there. Every one has kebeles in the area tree.
WOREDAS = [
	("woreda-ET040703", "oromo"),  # Adama
	("woreda-ET040506", "oromo"),  # Bako Tibe
	("woreda-ET040103", "oromo"),  # Gimbi
	("woreda-ET041712", "oromo"),  # Shashemene
	("woreda-ET040406", "oromo"),  # Mena (Jimma)
	("woreda-ET040405", "oromo"),  # Kersa (Jimma)
	("woreda-ET042011", "oromo"),  # Sebeta
	("woreda-ET040712", "oromo"),  # Akaki
	("woreda-ET041002", "harari"),  # Jarso (East Hararghe)
	("woreda-ET041008", "harari"),  # Kersa (East Hararghe)
	("woreda-ET030203", "amhara"),  # Fogera
	("woreda-ET030613", "amhara"),  # Dejen
	("woreda-ET030209", "amhara"),  # Dera
	("woreda-ET030710", "amhara"),  # Bure
	("woreda-ET010306", "tigray"),  # Kilte Awulaelo
	("woreda-ET080202", "south"),  # Arba Minch Zuria
]

NAMES = {
	"oromo": {
		"m": [
			"Gemechu",
			"Lemma",
			"Dinku",
			"Tadesse",
			"Jaleta",
			"Bekele",
			"Dereje",
			"Abdisa",
			"Gadisa",
			"Tolasa",
		],
		"f": ["Chaltu", "Bontu", "Hawi", "Lensa", "Meseret", "Ayantu", "Sifan", "Tsehay"],
		"father": [
			"Tolera",
			"Gudeta",
			"Feyisa",
			"Merga",
			"Dida",
			"Bulti",
			"Negasa",
			"Wakjira",
			"Etana",
			"Kebede",
		],
	},
	"harari": {
		"m": ["Mohammed", "Ahmed", "Abdi", "Yusuf", "Kedir", "Jemal", "Hussein", "Ibrahim"],
		"f": ["Hawa", "Fatuma", "Zeynab", "Aisha", "Kedija"],
		"father": ["Ahmed", "Yusuf", "Mohammed", "Ali", "Hussein", "Omar", "Jemal", "Abdella"],
	},
	"amhara": {
		"m": ["Mulugeta", "Tsegaye", "Asmare", "Alemu", "Wubalem", "Belay", "Mesfin", "Abebe", "Yohannes"],
		"f": ["Yeshi", "Ayelech", "Birtukan", "Zenebech", "Tigist", "Almaz", "Wubit", "Meskerem"],
		"father": [
			"Haile",
			"Wolde",
			"Alemu",
			"Negash",
			"Bogale",
			"Mekonnen",
			"Zewdu",
			"Tilahun",
			"Assefa",
			"Desta",
		],
	},
	"tigray": {
		"m": ["Berhane", "Kidane", "Amanuel", "Haftu", "Teklay", "Abraha", "Hagos", "Tesfay"],
		"f": ["Selam", "Letay", "Rahel", "Hiwot", "Freweini", "Mebrat"],
		"father": [
			"Gebremedhin",
			"Mariam",
			"Berhe",
			"Hagos",
			"Abraha",
			"Gebreselassie",
			"Teklehaimanot",
			"Woldu",
		],
	},
	"south": {
		"m": ["Markos", "Tamirat", "Dawit", "Yonas", "Mathewos", "Bereket", "Eyob"],
		"f": ["Tsion", "Mulunesh", "Selamawit", "Aster", "Hirut", "Mahlet"],
		"father": ["Tadesse", "Mathewos", "Lagebo", "Dubale", "Mengesha", "Shiferaw", "Mamo", "Gelaye"],
	},
}

CHANNELS = ["Web Portal", "Mobile App", "Mobile Call", "IVR Helpline"]
CHANNEL_WEIGHTS = [28, 22, 22, 28]

# (category, type, what the farmer wrote, what they want, what the officer asks, what was
# done, how it ended). {woreda} and {kebele} are filled with where the farmer lives.
TEMPLATES = [
	(
		"Inputs",
		"Fertilizer Shortage",
		"Our cooperative in {kebele} kebele, {woreda}, received only 12 quintals of DAP against the 40 quintals "
		"we requested for the meher season. Households that registered in May were told to wait, and planting "
		"time is almost over.",
		"Release the rest of the DAP and urea to the cooperative before the planting window closes.",
		"Please tell us the name of your cooperative and how many quintals you registered for.",
		"Contacted the woreda cooperative promotion office and the regional input distribution enterprise. A second "
		"allocation of 28 quintals of DAP was confirmed for the cooperative.",
		"28 quintals of DAP reached the cooperative store and were shared among registered members in the order "
		"they registered.",
	),
	(
		"Inputs",
		"Seed Quality Issue",
		"The improved wheat seed I bought from the union store near {woreda} germinated very badly. About half of "
		"my 1.5 hectare plot did not come up even after I replanted. Other farmers in {kebele} kebele say the same "
		"about seed from the same bags.",
		"Replace the seed or refund the cost, and check the whole batch before it is sold again.",
		"Please send the lot number printed on the seed bag.",
		"Took samples from the union store and sent them to the regional seed quality control laboratory. The lot "
		"failed the germination standard.",
		"The union replaced the seed for 23 affected households and the rest of the lot was taken off sale.",
	),
	(
		"Inputs",
		"Fertilizer Price Overcharging",
		"The cooperative shop in {kebele} kebele sells urea at 4,300 birr a quintal. The price posted at the "
		"{woreda} agriculture office is 3,650 birr. They say the difference is a transport fee that nobody "
		"agreed to.",
		"Return the extra money and sell at the official price.",
		"How many quintals did you buy, and do you still have the receipt?",
		"The woreda trade office inspected the shop and compared its receipts with the official price list. The "
		"overcharge was confirmed.",
		"The cooperative was ordered to refund the difference to every buyer and to post the official price at "
		"the shop.",
	),
	(
		"Schemes",
		"Beneficiary Left Out of Scheme",
		"My name was not on the list when the kebele announced the households receiving support this year. I am a "
		"widow with four children and farm less than half a hectare in {kebele} kebele. Neighbours in the same "
		"situation are on the list.",
		"Add my household to the beneficiary list and pay what I missed.",
		"Please give your household number from the kebele registry and the name of the kebele chairperson.",
		"Checked the beneficiary register with the woreda social affairs office. The household had been dropped "
		"during a transfer of the register.",
		"The household was restored to the beneficiary list and the missed months were paid with the next "
		"transfer.",
	),
	(
		"Schemes",
		"Productive Safety Net Enrolment Error",
		"I have been a Productive Safety Net client in {kebele} kebele for three years. This season I was moved to "
		"the list of households that no longer qualify, but nothing in my household has changed.",
		"Put my household back on the safety net list and explain why I was removed.",
		"Please tell us your client number and when your last payment arrived.",
		"The woreda safety net office reviewed the graduation list. The household was marked as graduated by a "
		"data entry mistake.",
		"The error was corrected and the household was reinstated with back payment for two months.",
	),
	(
		"Schemes",
		"Subsidy Allocation Issue",
		"The fertilizer subsidy for {kebele} kebele was shared out by the committee without telling farmers how "
		"much each household would get. Some households received three quintals and others received none.",
		"Publish the allocation list and give every registered household its share.",
		"Please tell us how many hectares you farm and whether you were present at the kebele meeting.",
		"Asked the woreda office for the allocation list and went through it with the kebele committee.",
		"A corrected allocation list was posted at the kebele office and the households that had received "
		"nothing were given their share.",
	),
	(
		"Payments",
		"Mobile Money Wallet Not Credited",
		"The payment for my teff delivered to the union in {woreda} was sent three weeks ago. The union says it "
		"paid, but nothing arrived in my telebirr wallet.",
		"Credit the payment to my wallet or pay me in cash.",
		"Please send the mobile number linked to your wallet and the date you delivered the teff.",
		"Traced the transaction with the union and the mobile money agent. The payment had been sent to the wrong "
		"number.",
		"The payment was reversed and credited to the correct wallet, and the union confirmed with a receipt.",
	),
	(
		"Payments",
		"Wrong Amount Paid",
		"I delivered 18 quintals of maize to the cooperative in {kebele} kebele. They paid me for 14. I have the "
		"weighing slip showing 18.",
		"Pay me the 4 quintals that are missing from the payment.",
		"Please send a photo of the weighing slip.",
		"Compared the weighing slip with the cooperative's ledger. The ledger had recorded 14 quintals.",
		"The ledger was corrected and the difference of 4 quintals was paid to the farmer.",
	),
	(
		"Payments",
		"Delayed Payment Settlement",
		"The cooperative has not paid us for the sesame we sold two months ago. Forty households in {kebele} "
		"kebele are waiting, and we need the money to prepare for the next season.",
		"Pay all households this month with the interest the agreement requires.",
		"Please give the name of the buyer and the date of the sales agreement.",
		"Wrote to the union and the buyer. The buyer had held the payment for a quality dispute that was never "
		"communicated to farmers.",
		"The dispute was settled and the cooperative paid all households in full.",
	),
	(
		"Credit",
		"Loan Application Rejected Without Reason",
		"I applied for a loan of 40,000 birr from the microfinance institution in {woreda} to buy a pair of oxen. "
		"They rejected it and gave no reason. I have repaid my previous loan on time.",
		"Tell me the reason in writing and reconsider the application.",
		"Please send the date of your application and the branch where you applied.",
		"Asked the institution for the file. The application had been rejected because a guarantor's signature "
		"was missing.",
		"The farmer was helped to submit a new guarantor and the loan was approved the following week.",
	),
	(
		"Credit",
		"Collateral Release Delay",
		"I finished repaying my loan in Hamle but the bank in {woreda} still holds my land certificate. They tell "
		"me to come back every week.",
		"Return my land certificate now.",
		"Please send your loan account number and the date of your last payment.",
		"Contacted the branch manager and confirmed the loan was fully repaid. The certificate was waiting for a "
		"signature at the regional office.",
		"The certificate was returned to the farmer and the branch confirmed the loan as closed.",
	),
	(
		"Credit",
		"Interest Rate Discrepancy",
		"My loan agreement says 11 percent a year. The statement I was given shows 15 percent. I am a member of the "
		"{kebele} kebele savings group.",
		"Correct the interest to what the agreement says and refund what I overpaid.",
		"Please send a copy of your agreement and your latest statement.",
		"Compared the agreement and the statement. A higher rate for a different product had been applied.",
		"The rate was corrected and the overpaid interest was credited against the next instalment.",
	),
	(
		"Markets",
		"Unfair Weighing at Collection Point",
		"The scale at the coffee collection point in {kebele} kebele reads about 3 kilograms less on every sack "
		"than the scale at the {woreda} market. Many of us have been selling there all season.",
		"Check and certify the scale, and compensate farmers who were underweighed.",
		"Please tell us which day you sold and how many sacks.",
		"The woreda trade office tested the scale with standard weights and found it out of calibration.",
		"The scale was replaced and farmers who could show their slips were compensated.",
	),
	(
		"Markets",
		"Cooperative Payment Withheld",
		"Our union has been holding the final payment for the coffee we delivered in Tikimt. They say the bonus "
		"will be added later but have not said when. I am a member in {woreda}.",
		"Pay the final payment and the bonus with a date.",
		"Please send your membership number and your delivery receipts.",
		"Met the union manager and reviewed the payment schedule. The bonus had been approved but not released.",
		"The union released the final payment and the bonus to all members.",
	),
	(
		"Markets",
		"Price Reporting Dispute",
		"The price board at the {woreda} market shows 5,800 birr a quintal for white teff, but traders only offer "
		"5,000. Farmers cannot tell which price is real.",
		"Publish the real market price every day and check the board.",
		"Please tell us the date and the market you sold at.",
		"The woreda trade office compared the board with prices at the nearest commodity exchange centre.",
		"The board is now updated every morning from the official price report.",
	),
	(
		"Extension Services",
		"Extension Agent Not Visiting",
		"The development agent for {kebele} kebele has not visited our farms since the start of the rains. We have "
		"questions about the new wheat variety and nobody answers at the farmer training centre.",
		"Assign an agent who visits and answers farmers regularly.",
		"Please tell us the name of your farmer training centre and your village.",
		"Spoke with the woreda agriculture office. The agent had been reassigned to cover another kebele.",
		"A new development agent was posted to the kebele and a schedule of farm visits was shared with farmers.",
	),
	(
		"Extension Services",
		"Wrong Agronomic Advice",
		"The agent in {woreda} told us to apply urea at planting on our maize. The yield is poor and an agent from "
		"another kebele says it should have been split. Many of us followed the advice.",
		"Give correct guidance for the rest of the season and review what was recommended.",
		"Please tell us the plot size and when you applied the fertilizer.",
		"Subject matter specialists reviewed the recommendation and visited several plots.",
		"A corrected recommendation was issued and agents were retrained.",
	),
	(
		"Extension Services",
		"Training Not Delivered",
		"The training on row planting promised for {kebele} kebele was announced three times and never held. "
		"Farmers prepared their time and were told on the day that it was cancelled.",
		"Hold the training on a fixed date and tell farmers in advance.",
		"Please tell us how many farmers attended on the cancelled day.",
		"Checked the training plan with the woreda office. The trainer had been called to another assignment.",
		"The training was held two weeks later and 60 farmers attended.",
	),
	(
		"Irrigation and Water",
		"Irrigation Water Not Reaching Plot",
		"The canal serving our plots in {kebele} kebele has been dry at the tail end for a month. The first plots "
		"take all the water. Our onions are drying.",
		"Make the water reach the tail plots through a fair rotation.",
		"Please tell us the block number of your plot and the name of the canal.",
		"The scheme technician visited the canal and found a broken diversion gate. A rotation schedule was agreed "
		"with the water users.",
		"The gate was repaired and water now reaches the tail plots on the agreed rotation.",
	),
	(
		"Irrigation and Water",
		"Canal Maintenance Neglected",
		"The main canal near {kebele} kebele is filled with silt and weeds. It was not cleaned before the season "
		"and water overflows into our fields.",
		"Clean the canal and repair the banks before the next flow.",
		"Please send a photo of the damaged section if you can.",
		"The irrigation authority inspected the canal and organised community labour with its machinery.",
		"The canal was cleared and the banks repaired.",
	),
	(
		"Irrigation and Water",
		"Pump or Drip Equipment Fault",
		"The solar pump the woreda gave our group in {woreda} stopped working after two months. Nobody has come to "
		"fix it and the vegetable beds are dying.",
		"Repair or replace the pump quickly.",
		"Please tell us the pump brand and the date it stopped.",
		"A technician from the irrigation authority tested the pump. The controller had failed.",
		"The controller was replaced under warranty and the pump now runs.",
	),
	(
		"Land and Tenure",
		"Land Certificate Delay",
		"I applied for the second level land holding certificate for my plot in {kebele} kebele eight months ago. "
		"The woreda land office keeps telling me the file is being processed.",
		"Issue the certificate or tell me what is missing.",
		"Please send the file number from your receipt.",
		"Checked the file at the woreda land administration office. It was waiting for a survey signature.",
		"The survey was signed and the certificate was issued.",
	),
	(
		"Land and Tenure",
		"Boundary Dispute",
		"My neighbour moved the boundary stones between our plots in {kebele} kebele and now farms about a quarter "
		"of a hectare of my land. The kebele elders have not decided.",
		"Have the boundary measured again and returned to the registered line.",
		"Please tell us your certificate number and your neighbour's name.",
		"The woreda land office sent a surveyor and the kebele land committee measured the plots.",
		"The boundary was set again on the registered line and both farmers signed.",
	),
	(
		"Land and Tenure",
		"Land Holding Registration Error",
		"The land certificate for my plot in {woreda} lists my late husband as the holder. I have asked three "
		"times to put the land in my name and the children's.",
		"Correct the certificate to show the right holders.",
		"Please send your husband's death certificate and your marriage certificate.",
		"Reviewed the documents with the land administration office and prepared the transfer.",
		"The certificate was reissued in the names of the widow and the children.",
	),
	(
		"Livestock and Veterinary",
		"Vaccination Not Available",
		"The veterinary post in {kebele} kebele has had no lumpy skin vaccine for over a month. Cattle in the "
		"village are falling sick and some have died.",
		"Bring the vaccine and vaccinate the herds.",
		"Please tell us how many cattle are sick and in which village.",
		"The woreda livestock office sent vaccine from the regional store and organised a vaccination campaign.",
		"More than 900 cattle were vaccinated in the kebele.",
	),
	(
		"Livestock and Veterinary",
		"Veterinary Clinic Unstaffed",
		"The animal health clinic in {woreda} has been closed for weeks. The animal health worker left and nobody "
		"has replaced them. We travel a day to the next clinic.",
		"Post an animal health worker to the clinic.",
		"Please tell us the name of the clinic.",
		"Raised the vacancy with the woreda office and the regional livestock bureau.",
		"A new animal health worker started at the clinic.",
	),
	(
		"Livestock and Veterinary",
		"Artificial Insemination Service Delay",
		"I registered my cow for artificial insemination at the {woreda} service point three times. Each time the "
		"technician says there is no semen or no fuel for the motorbike.",
		"Provide the service in time or tell us who else can do it.",
		"Please tell us the breed of the cow and the dates you registered.",
		"Ordered semen from the national centre and arranged transport for the technician.",
		"The cow was inseminated and the technician will return for a pregnancy check.",
	),
	(
		"Crop Insurance",
		"Claim Not Settled",
		"Hail destroyed my wheat in {kebele} kebele in Meskerem. I filed an insurance claim the same week and "
		"nobody has come to assess it. I have the premium receipt.",
		"Assess my damage and pay the claim.",
		"Please send the policy number and photos of the damaged field if you have them.",
		"Contacted the insurance office. An assessor was scheduled and visited the plot with the kebele committee.",
		"The damage was assessed at 62 percent and the claim was paid.",
	),
	(
		"Crop Insurance",
		"Premium Deduction Dispute",
		"The union deducted 840 birr as an insurance premium from my payment, but I never asked for insurance. "
		"Other members in {woreda} had the same deduction.",
		"Refund the premium or give me the policy.",
		"Please tell us the date of the deduction and the amount you were paid.",
		"Checked the union's list of insured members. Households that had not signed were included by mistake.",
		"The premium was refunded to the households that had not agreed to the policy.",
	),
	(
		"Crop Insurance",
		"Policy Not Issued After Payment",
		"I paid the insurance premium through the cooperative in {kebele} kebele before planting. It is already "
		"harvest time and I have not been given any policy document.",
		"Issue the policy certificate that shows my cover.",
		"Please send the premium receipt.",
		"Traced the payment with the cooperative and the insurer. The premium had not been forwarded.",
		"The premium was forwarded and the policy issued with cover from the date of payment.",
	),
]

REJECTION_REASONS = [
	"This grievance is about a private dispute between two households and is outside what this service can resolve. "
	"The kebele social court or the woreda administration can hear it.",
	"We could not find a service provider or programme record that matches this complaint. If you have a receipt or "
	"a file number, please submit a new grievance with it.",
	"This is a duplicate of a grievance already being handled. Please follow the earlier case.",
	"The matter you describe has already been decided on appeal at the woreda office. We cannot reopen it here.",
]

FARMER_MESSAGES = [
	"Thank you. I will be at the kebele office on Thursday if someone needs to see the plot.",
	"Any news on this? The season is moving on and we cannot wait much longer.",
	"I have spoken with the other farmers and they confirm what I wrote.",
	"Please call me on this number after 4 in the afternoon. I am in the field before that.",
]

OFFICER_MESSAGES = [
	"We have written to the woreda office and are waiting for their reply.",
	"A team will visit {kebele} kebele this week. Please keep your documents ready.",
	"We have asked the cooperative for their records and will update you here.",
	"Thank you for your patience. The case is with the woreda office for review.",
]

INTERNAL_NOTES = [
	"Called the woreda office. Focal person says the file is with the zonal desk.",
	"Spoke with the development agent at the farmer training centre. Matches what the farmer says.",
	"Checked the records. Complaint looks valid. Following up with the cooperative union.",
	"Asked the L2 officer for guidance on whether this belongs to our desk. Keeping it for now.",
]

CLOSE_REASONS = [
	"Thank you. The problem is solved and I have received what I asked for.",
	"I confirm this has been resolved. Thank you for your help.",
	"The officer came to the kebele and fixed it. I am satisfied.",
]

REOPEN_REASONS = [
	"The problem has come back. What was done did not last more than a week.",
	"I do not accept this. Nothing has actually changed in my kebele.",
	"Only part of the problem was solved. The rest is still waiting.",
]

FEEDBACK = [
	(5, "The officer called me and the problem was solved in a week. Thank you."),
	(4, "It took longer than I hoped but it was handled properly."),
	(5, "Someone came to the kebele. I did not expect that."),
	(3, "Solved, but I had to ask several times for news."),
	(4, "Clear reply and the money arrived."),
]

# What happens to each case. Each step is (action, hours after the one before: low, high).
PATHS = {
	"assigned": ([], (4, 70), 6),
	"in_progress": ([("start", 8, 30)], (6, 140), 12),
	"info_waiting": ([("start", 6, 24), ("info", 4, 40)], (12, 110), 6),
	"info_replied": ([("start", 6, 24), ("info", 4, 40), ("reply", 10, 70)], (4, 90), 4),
	"resolved_waiting": ([("start", 8, 30), ("resolve", 24, 160)], (6, 120), 7),
	"closed_confirmed": ([("start", 8, 30), ("resolve", 24, 200), ("close", 3, 90)], (24, 2100), 14),
	"closed_auto_resolved": ([("start", 8, 30), ("resolve", 24, 160), ("auto_close", 0, 0)], (24, 1900), 4),
	"closed_auto_info": ([("start", 6, 24), ("info", 4, 40), ("auto_close", 0, 0)], (24, 1900), 3),
	"rejected": ([("start", 6, 30), ("reject", 6, 90)], (24, 1700), 5),
	"reopened_open": ([("start", 8, 30), ("resolve", 24, 160), ("reopen", 10, 80)], (24, 140), 3),
	"reopened_closed": (
		[
			("start", 8, 30),
			("resolve", 24, 160),
			("reopen", 10, 80),
			("resolve", 40, 200),
			("close", 5, 60),
		],
		(48, 1600),
		3,
	),
	"escalated_open": ([("start", 6, 20), ("escalate", 0, 0)], (12, 140), 3),
	"escalated_closed": (
		[("start", 6, 20), ("escalate", 0, 0), ("resolve", 10, 60), ("close", 5, 70)],
		(48, 1500),
		2,
	),
}


def seed():
	"""Create the farmers and their grievances, then commit. Rolls everything back on a failure."""
	frappe.flags.in_demo_seed = True
	rng = random.Random(SEED)
	try:
		now = frappe_data.now_datetime()
		# The desks were set up before the cases below were filed. Without this a desk "effective
		# from today" is not active on the day a case is routed or escalated.
		frappe.db.set_value(
			"Grievance RBAC Assignment",
			{"active": 1},
			"effective_from",
			(now - timedelta(days=WINDOW_DAYS + 30)).date(),
			update_modified=False,
		)
		farmers = _seed_farmers(rng)
		made = _seed_cases(rng, farmers, now)
		_clean_up()
		# Explicit commit is required for a standalone bench execute seed script
		frappe.db.commit()  # nosemgrep
	except Exception:
		frappe.db.rollback()
		raise
	return {"farmers": len(farmers), "grievances": made}


@contextlib.contextmanager
def clock(moment):
	"""Make every `now_datetime` the app and Frappe use read `moment`.

	The app imports the function by name into many modules, so each of those names is swapped
	for the length of the block rather than only the one in Frappe.
	"""
	real = frappe_data.now_datetime
	swapped = []
	for module in list(sys.modules.values()):
		name = getattr(module, "__name__", "") or ""
		if not name.startswith(("oan_grievance_service", "frappe")):
			continue
		try:
			if getattr(module, "now_datetime", None) is real:
				module.now_datetime = lambda: moment
				swapped.append(module)
		except Exception:
			continue
	try:
		yield
	finally:
		for module in swapped:
			module.now_datetime = real


def _snap(rng, moment, after):
	"""A working-hours moment at or after `moment`, and later than `after`."""
	moment = moment.replace(
		hour=rng.randint(8, 16),
		minute=rng.randint(0, 59),
		second=rng.randint(0, 59),
		microsecond=rng.randint(0, 999999),
	)
	while moment <= after + timedelta(minutes=10):
		moment += timedelta(days=1)
	return moment


# Farmers -------------------------------------------------------------------------------------


def _seed_farmers(rng):
	areas = {}
	for woreda, group in WOREDAS:
		kebeles = frappe.get_all(
			"Grievance Administrative Area",
			filters={"parent_administrative_area": woreda, "is_group": 0},
			fields=["name", "area_name"],
			order_by="name",
		)
		if kebeles:
			areas[woreda] = (
				group,
				frappe.db.get_value("Grievance Administrative Area", woreda, "area_name"),
				kebeles,
			)

	farmers = []
	taken = set()
	woredas = list(areas)
	for index in range(FARMERS):
		woreda = woredas[index % len(woredas)]
		group, woreda_name, kebeles = areas[woreda]
		kebele = rng.choice(kebeles)
		pool = NAMES[group]
		female = rng.random() < 0.38
		while True:
			given = rng.choice(pool["f"] if female else pool["m"])
			name = f"{given} {rng.choice(pool['father'])}"
			if name not in taken:
				taken.add(name)
				break
		mobile = _mobile(rng)
		fayda = "".join(str(rng.randint(0, 9)) for _ in range(16))
		email = f"{given}.{name.split()[1]}{index + 1}@{FARMER_DOMAIN}".lower()
		profile = identity.find_or_create_submitter(
			{
				"fayda_id": fayda,
				"submitter_type": "Individual Farmer",
				"submitter_name": name,
				"contact_mobile": mobile,
				"administrative_area": kebele.name,
				"administrative_unit": kebele.area_name,
			}
		)
		_login(email, name)
		frappe.db.set_value("Grievance Submitter Profile", profile, "user", email, update_modified=False)
		farmers.append(
			frappe._dict(
				profile=profile,
				user=email,
				name=name,
				mobile=mobile,
				woreda=woreda_name,
				kebele=kebele.name,
				kebele_name=kebele.area_name,
			)
		)
	return farmers


def _mobile(rng):
	while True:
		number = f"+2519{rng.choice('1234')}{''.join(str(rng.randint(0, 9)) for _ in range(7))}"
		try:
			return identity.validate_mobile(number)
		except Exception:
			continue


def _login(email, full_name):
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"send_welcome_email": 0,
			"enabled": 1,
			"roles": [{"role": C.ROLE_SUBMITTER}],
		}
	)
	# Frappe refuses more than 60 new Users an hour, which the staff made just before already
	# count against; an import is the one thing it lets past.
	frappe.flags.in_import = True
	try:
		user.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_import = False
	update_password(user=user.name, pwd=PASSWORD)


# Cases ---------------------------------------------------------------------------------------


def _seed_cases(rng, farmers, now):
	paths = [name for name, (_steps, _tail, count) in PATHS.items() for _ in range(count)]
	assert len(paths) == CASES, len(paths)
	rng.shuffle(paths)

	slug_of = {entry["name"]: entry["slug"] for entry in CATEGORIES}

	for index, path in enumerate(paths):
		template = TEMPLATES[index % len(TEMPLATES)]
		farmer = farmers[rng.randrange(len(farmers))]
		_walk(rng, now, path, template, farmer, slug_of)
	return len(paths)


def _walk(rng, now, path, template, farmer, slug_of):
	category, grievance_type, text, outcome, question, action_taken, resolution = template
	steps, tail_range, _count = PATHS[path]
	where = {"woreda": farmer.woreda, "kebele": farmer.kebele_name}
	desk = slug_of[category]
	l1 = email_of(desk, "l1")

	# File it as far back as the last step needs to land `tail` before now. A step can drift a
	# day later than its nominal gap (working hours, weekends), so step back until it fits.
	tail = timedelta(hours=rng.randint(*tail_range))
	base = now - tail - _span(category, steps)
	while True:
		opened = _filed_at(rng, base)
		times = _timeline(rng, opened, steps, category)
		if times[-1] <= now - tail:
			break
		base -= timedelta(hours=12)

	anonymous = rng.random() < 0.06
	doc = _file(rng, category, grievance_type, text.format(**where), outcome, farmer, anonymous, opened)

	assigned_at = times[0]
	with clock(assigned_at):
		frappe.set_user("Administrator")
		routing.apply_routing(doc, commit_status=False)
		lifecycle.transition(doc, "Assign", reason="Auto-routed by assignment", automated=True, notify=False)
		doc.reload()

	for (step, _lo, _hi), moment in zip(steps, times[1:], strict=True):
		with clock(moment):
			doc.reload()
			officer = doc.assigned_to or l1
			if step == "start":
				_act(
					doc,
					"In Progress",
					officer,
					f"{_first(officer)} has taken up your grievance.",
					"status_change",
				)
			elif step == "info":
				_act(doc, "Request More Info", officer, question, "info_request")
			elif step == "reply":
				_act(
					doc,
					"Submitter Reply",
					farmer.user,
					"Here is the information you asked for. I can show the documents at the kebele office if needed.",
					"info_response",
				)
			elif step == "resolve":
				reason = response_body.compose(action_taken, resolution)
				_act(
					doc, rng.choice(["Resolve", "Resolve", "Partially Resolve"]), officer, reason, "response"
				)
			elif step == "close":
				_act(doc, "Close Case", farmer.user, rng.choice(CLOSE_REASONS), "resolution", confirmed=True)
				doc.db_set("closure_reason", "Resolved and confirmed by the submitter", update_modified=False)
				_feedback(rng, doc, farmer, moment)
			elif step == "reopen":
				_act(doc, "Reopen", farmer.user, rng.choice(REOPEN_REASONS), "status_change")
				doc.db_set("reopen_count", (doc.reopen_count or 0) + 1, update_modified=False)
			elif step == "reject":
				_act(doc, "Reject", officer, rng.choice(REJECTION_REASONS), "rejection")
			elif step == "auto_close":
				_auto_close(doc)
			elif step == "escalate":
				sla.escalate(doc, reason=None)

	frappe.set_user("Administrator")
	_extra_entries(rng, doc, farmer, where, l1, steps, times)


def _first(user):
	return (frappe.db.get_value("User", user, "first_name") or user).split()[0]


def _span(category, steps):
	"""The longest the steps take, one after the other, as nominal gaps."""
	span = timedelta(hours=sum(hi for (_a, _lo, hi) in steps))
	if any(name == "escalate" for name, _lo, _hi in steps):
		probe = datetime(2026, 8, 3, 9, 0)
		span += _escalation_at(category, probe) - probe
	if any(name == "auto_close" for name, _lo, _hi in steps):
		span += timedelta(hours=168)
	return span


def _filed_at(rng, moment):
	"""A moment in the day a farmer would file: after dawn, before bedtime."""
	return moment.replace(
		hour=rng.randint(6, 21),
		minute=rng.randint(0, 59),
		second=rng.randint(0, 59),
		microsecond=rng.randint(0, 999999),
	)


def _escalation_at(category, start):
	"""When the escalation clock will point, for a case whose SLA starts at `start`."""
	policy = sla.resolve_policy(category)
	due = sla.calculate_working_deadline(start, policy.sla_days, holiday_list_name=policy.holiday_list)
	threshold = policy.auto_escalation_threshold or sla.auto_escalation_threshold()
	if 0 < threshold < 100:
		return start + (due - start) * threshold / 100
	return due


def _timeline(rng, opened, steps, category):
	"""The moment of each event: assignment first, then one per step."""
	moments = [opened + timedelta(minutes=rng.randint(3, 40))]
	for name, lo, hi in steps:
		previous = moments[-1]
		if name == "auto_close":
			# The state timer runs seven days from the step before; the sweep picks it up soon after.
			moments.append(previous + timedelta(hours=168, minutes=rng.randint(5, 55)))
		elif name == "escalate":
			at = _escalation_at(category, moments[0])
			moments.append(max(at, previous + timedelta(hours=1)) + timedelta(minutes=rng.randint(5, 55)))
		else:
			moments.append(_snap(rng, previous + timedelta(hours=rng.randint(lo, hi)), previous))
	return moments


def _file(rng, category, grievance_type, text, outcome, farmer, anonymous, opened):
	channel = rng.choices(CHANNELS, CHANNEL_WEIGHTS)[0]
	with clock(opened):
		frappe.set_user(farmer.user)
		doc = frappe.new_doc("Grievance")
		doc.client_submission_uuid = frappe.generate_hash(length=32)
		doc.consent_given = 1
		doc.consent_recorded_at = opened
		doc.is_anonymous = 1 if anonymous else 0
		doc.submission_channel = channel
		doc.service_category = category
		doc.grievance_type = frappe.db.get_value(
			"Grievance Type", {"service_category": category, "type_name": grievance_type}
		)
		doc.description = text
		doc.desired_outcome = outcome
		doc.administrative_area = farmer.kebele
		doc.administrative_unit = farmer.kebele_name
		doc.submitter = farmer.profile
		doc.submitter_type = "Individual Farmer"
		doc.submitter_name = farmer.name
		doc.contact_mobile = farmer.mobile
		doc.insert(ignore_permissions=True)
		GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="submission",
			is_internal=False,
			body=text,
			author_user=farmer.user,
			author_type="submitter",
			ref_doctype="Grievance",
			ref_docname=doc.name,
		)
		frappe.set_user("Administrator")
	return doc


def _act(doc, action, by, reason, entry_type, confirmed=False):
	"""One workflow move and the timeline entry the action API writes for it."""
	frappe.set_user(by)
	try:
		history = lifecycle.transition(doc, action, reason=reason, notify=False, confirmed=confirmed)
	finally:
		frappe.set_user("Administrator")
	GrievanceTimeline.record(
		grievance=doc.name,
		entry_type=entry_type,
		is_internal=False,
		body=reason or f"Action: {action}",
		author_user=by,
		ref_doctype="Grievance Status History",
		ref_docname=history.name if history else None,
	)
	doc.reload()


def _auto_close(doc):
	"""What `tasks.expire_state_timers` does when a state's timer runs out."""
	if doc.workflow_state == C.STATE_RESOLVED:
		reason = "Closed - resolution period elapsed without objection"
		body = "Grievance auto-closed: resolution period elapsed without objection."
	else:
		reason = "Closed - no response to information request"
		body = "Grievance auto-closed: no response to information request."
	doc.db_set("closure_reason", reason, update_modified=False)
	lifecycle.transition(doc, "Auto Close", reason=reason, automated=True, notify=False)
	GrievanceTimeline.record(
		grievance=doc.name, entry_type="status_change", is_internal=False, body=body, author_user=None
	)
	doc.reload()


def _feedback(rng, doc, farmer, moment):
	if rng.random() < 0.35:
		return False
	rating, comment = rng.choice(FEEDBACK)
	frappe.get_doc(
		{
			"doctype": "Grievance Feedback",
			"grievance": doc.name,
			"rating": rating,
			"feedback_type": "Resolution",
			"comments": comment,
			"submitted_by": farmer.user,
			"author_submitter": farmer.profile,
			"submitted_at": moment + timedelta(minutes=rng.randint(2, 30)),
		}
	).insert(ignore_permissions=True)
	return True


def _extra_entries(rng, doc, farmer, where, officer, steps, times):
	"""A few messages and internal notes between the milestones, so threads are not bare."""
	if len(times) < 3 or rng.random() < 0.4:
		return
	first, last = times[0], times[min(len(times) - 1, 2)]
	span = (last - first).total_seconds()
	if span < 6 * 3600:
		return
	moment = first + timedelta(seconds=span * rng.uniform(0.3, 0.7))
	body, author, kind, internal = rng.choice(
		[
			(rng.choice(FARMER_MESSAGES), farmer.user, "message", False),
			(rng.choice(OFFICER_MESSAGES).format(**where), None, "message", False),
			(rng.choice(INTERNAL_NOTES), None, "note", True),
		]
	)
	with clock(moment):
		GrievanceTimeline.record(
			grievance=doc.name,
			entry_type=kind,
			is_internal=internal,
			body=body,
			author_user=author or doc.assigned_to or officer,
			author_type="submitter" if author else "officer",
			created_on=moment,
		)


def _clean_up():
	"""Nothing queued may reach a real phone or inbox if the scheduler is switched on later."""
	for doctype in ("Grievance Notification Log", "Email Queue", "Notification Log"):
		if frappe.db.table_exists(doctype):
			frappe.db.delete(doctype)
