frappe.ui.form.on("Beacon Notification", {
	refresh(frm) {
		if (frm.doc.incident)
			frm.add_custom_button(__("Open Incident"), () =>
				frappe.set_route("Form", "Beacon Incident", frm.doc.incident)
			);
		frm.add_custom_button(
			__("On-call Settings"),
			() => frappe.set_route("Form", "Beacon Settings", "Beacon Settings"),
			__("Related")
		);
		if (frm.doc.error) frm.set_intro(frm.doc.error, "red");
	},
});
