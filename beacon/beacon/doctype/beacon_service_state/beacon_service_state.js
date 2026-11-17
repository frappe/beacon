frappe.ui.form.on("Beacon Service State", {
	refresh(frm) {
		const view = new beacon.RecordView(frm);
		view.link("View Minutes", "Beacon Service Minute", {
			service: frm.doc.service,
			region: frm.doc.region,
		});
		if (frm.doc.service)
			frm.add_custom_button(__("Open Service"), () =>
				frappe.set_route("Form", "Beacon Alert Rule", frm.doc.service)
			);
	},
});
