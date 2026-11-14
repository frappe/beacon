frappe.ui.form.on("Beacon Service", {
	refresh(frm) {
		const view = new beacon.RecordView(frm);
		view.managed(["enabled", "query_region"]);
		if (frm.is_new()) return;
		view.related("health_html", "Beacon Service State", { service: frm.doc.name }, [
			["region", "Region"],
			["status", "Status"],
			["checked_at", "Last measured"],
			["budget_left", "Budget left, 30 days"],
		]);
		beacon.add_action(
			frm,
			__("Test SLI Query"),
			"test",
			null,
			(results) =>
				beacon.show_results(
					__(
						"Test result. These numbers include unknown resources and maintenance time."
					),
					results
				),
			{ standalone: true, draft: true }
		);
	},
});
