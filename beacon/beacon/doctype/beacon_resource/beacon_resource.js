frappe.ui.form.on("Beacon Resource", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Metrics"), () =>
			frappe.set_route("beacon-metrics", frm.doc.name)
		);
		frm.add_custom_button(__("Log viewer"), () =>
			frappe.set_route("beacon-logs", frm.doc.region, frm.doc.name)
		);
		const view = new beacon.RecordView(frm);
		view.related(
			"now_html",
			"Beacon Alert",
			{ resource: frm.doc.name, status: ["in", ["Pending", "Firing"]] },
			[
				["title", "Title"],
				["status", "Status"],
				["severity", "Severity"],
				["incident", "Incident"],
			]
		);
		if (frm.doc.monitoring_disabled)
			beacon.add_action(frm, __("Enable Monitoring"), "enable_monitoring", null, null, {
				confirm: [
					__("Enable monitoring for this resource?"),
					__("The checks start again after a short delay."),
				],
			});
		else
			beacon.add_action(frm, __("Disable Monitoring"), "disable_monitoring", [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					description: __(
						"Checks stop for this resource only and its open alerts close. Child resources stay checked."
					),
					reqd: 1,
				},
			]);
	},
});
