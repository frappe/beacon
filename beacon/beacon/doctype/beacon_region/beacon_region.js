frappe.ui.form.on("Beacon Region", {
	refresh(frm) {
		if (frm.is_new()) return;
		beacon.add_action(
			frm,
			__("Check Connection"),
			"check_connection",
			null,
			(result) =>
				beacon.show_fields(__("Connection"), {
					latency_ms: result.latency_ms,
					...result.settings,
				}),
			{ draft: true }
		);
		if (frm.doc.status === "Active")
			beacon.add_action(frm, __("Disable Region"), "set_status", null, null, {
				confirm: [
					__("Disable this region?"),
					__("Beacon stops the checks for this region and closes its open alerts."),
				],
				prepare: () => ({ status: "Disabled" }),
			});
		else
			beacon.add_action(frm, __("Enable Region"), "set_status", null, null, {
				confirm: [
					__("Enable this region?"),
					__("Beacon starts the checks for this region again."),
				],
				prepare: () => ({ status: "Active" }),
			});
	},
});
