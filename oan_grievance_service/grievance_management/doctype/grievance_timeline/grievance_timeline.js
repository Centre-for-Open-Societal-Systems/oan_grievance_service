// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("Grievance Timeline", {
	refresh(frm) {
		// Read-only in form view
		frm.disable_save();
	},
});
