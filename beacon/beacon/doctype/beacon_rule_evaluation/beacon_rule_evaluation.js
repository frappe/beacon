frappe.ui.form.on("Beacon Rule Evaluation", {
	refresh(frm) {
		if (frm.is_new()) return;
		const view = new beacon.RecordView(frm);
		if (frappe.model.can_write("Beacon Alert Rule"))
			beacon.add_action(
				frm,
				__("Run Now"),
				"run_now",
				null,
				async () => {
					frappe.show_alert({
						message: __(
							"The evaluation started. Reload this page after some seconds to see the result."
						),
						indicator: "blue",
					});
					await frm.reload_doc();
				},
				{ read: true, standalone: true }
			);
		view.link("View Alerts", "Beacon Alert", {
			rule: frm.doc.rule,
			region: frm.doc.region,
		});
	},
});
