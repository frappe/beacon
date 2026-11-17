frappe.ui.form.on("Beacon Alert Rule", {
	kind(frm) {
		frm.set_df_property("http_section", "label", __("{0} check", [frm.doc.kind]));
	},

	refresh(frm) {
		frm.trigger("kind");
		const view = new beacon.RecordView(frm);
		if (frm.doc.is_custom || frm.is_new()) {
			frm.set_df_property("kind", "options", ["SQL", "HTTP", "TLS"]);
			frm.set_intro(
				__(
					"A Custom rule lives only in this site. To keep it in code, add a file with the same Rule Key to a definition source. The next sync takes this rule over."
				),
				"blue"
			);
		} else
			view.managed(
				frm.doc.kind === "SLO Burn"
					? ["enabled", "query_region"]
					: ["HTTP", "TLS"].includes(frm.doc.kind)
					? ["enabled", "regions"]
					: ["enabled"]
			);
		if (frm.is_new()) return;
		if (frm.doc.kind === "SLO Burn") {
			frm.set_intro(
				__(
					"This rule is the service definition. Each minute Beacon measures it and raises the fast, medium and slow burn alerts, and the SLA alert when the service has an SLA Target. The targets come from the service file. Turn it on or off to start or stop its measurement."
				)
			);
			view.related("health_html", "Beacon Service State", { service: frm.doc.name }, [
				["region", "Region"],
				["status", "Status"],
				["checked_at", "Last measured"],
				["budget_left", "Budget left, 30 days"],
			]);
		}
		view.related("evaluations_html", "Beacon Rule Evaluation", { rule: frm.doc.name }, [
			["region", "Region"],
			["state", "State"],
			["last_success_at", "Last success"],
			["last_error", "Last error"],
		]);
		beacon.add_runbook_button(frm, frm.doc.runbook_url);
		if (frm.doc.kind === "SLO Burn" && frm.doc.measurement === "Measured")
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
		else if (["SQL", "HTTP", "TLS"].includes(frm.doc.kind))
			beacon.add_action(
				frm,
				__("Test Rule"),
				"test",
				null,
				(results) => beacon.show_results(__("Test result"), results),
				{ standalone: true, draft: true }
			);
		beacon.add_silence_actions(frm);
	},
});
