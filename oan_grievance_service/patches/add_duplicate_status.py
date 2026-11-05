# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Add the Duplicate state and the Confirm Duplicate action to the Grievance Workflow."""

from oan_grievance_service.setup.install import seed_workflow


def execute():
	seed_workflow()
