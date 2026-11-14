frappe.ui.form.on("Beacon Alert", {
	refresh(frm) {
		if (frm.doc.rule)
			frappe.db
				.get_value("Beacon Alert Rule", frm.doc.rule, "runbook_url")
				.then(({ message }) =>
					beacon.add_runbook_button(frm, message?.runbook_url)?.addClass("btn-primary")
				);
		if (frm.doc.incident)
			frm.add_custom_button(__("Open Incident"), () =>
				frappe.set_route("Form", "Beacon Incident", frm.doc.incident)
			);
		frm.add_custom_button(__("Create Silence"), () =>
			beacon.RecordView.silence({
				region: frm.doc.region,
				resource_id: (frm.doc.resource || "").split(":").slice(1).join(":"),
				rule: frm.doc.rule,
				alert_key: frm.doc.alert_key,
			})
		);
	},
});
