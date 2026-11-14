frappe.ui.form.on("Beacon Service Day", {
	refresh(frm) {
		new beacon.RecordView(frm).link("Service health", "Beacon Service State", {
			service: frm.doc.service,
			region: frm.doc.region,
		});
	},
});
