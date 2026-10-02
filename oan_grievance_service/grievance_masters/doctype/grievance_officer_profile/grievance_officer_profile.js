// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("Grievance Officer Profile", {
	setup(frm) {
		frm.set_query("reports_to", () => ({ filters: { level: "L1" } }));
	},
});
