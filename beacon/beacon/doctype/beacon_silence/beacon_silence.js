const MATCH_FIELDS = ["region", "resource_type", "resource_id", "rule", "alert_key"];

class SilenceForm {
	constructor(frm) {
		this.frm = frm;
	}
	async preview() {
		const fields = [...MATCH_FIELDS, "include_descendants"];
		const identity = JSON.stringify(fields.map((field) => this.frm.doc[field]));
		if (!MATCH_FIELDS.some((field) => this.frm.doc[field])) return;
		const wrapper = this.frm.fields_dict.matching_html.$wrapper;
		wrapper.html(`<p class="text-muted">${__("Loading match preview...")}</p>`);
		try {
			const { message } = await this.frm.call("preview_matches");
			if (identity !== JSON.stringify(fields.map((field) => this.frm.doc[field]))) return;
			const view = new beacon.RecordView(this.frm);
			wrapper.html(
				`${this.heading(
					__("Resources"),
					message.resource_count,
					message.resources
				)}${await view.table("Beacon Resource", message.resources, [
					["title", "Title"],
					["resource_type", "Type"],
					["region", "Region"],
				])}${this.heading(
					__("Open alerts"),
					message.alert_count,
					message.alerts
				)}${await view.table("Beacon Alert", message.alerts, [
					["title", "Title"],
					["status", "Status"],
				])}`
			);
		} catch (error) {
			console.error(error);
			wrapper.html(
				`<p class="text-danger">${__(
					"The preview did not load. This does not mean that no item matches."
				)}</p>`
			);
		}
	}
	heading(label, count, rows) {
		const shown = count > rows.length ? ` · ${__("first {0} shown", [rows.length])}` : "";
		return `<h6 class="beacon-panel-heading">${label} (${count})${shown}</h6>`;
	}

	refresh() {
		this.preview();
		if (this.frm.is_new() || this.frm.doc.revoked_at) return;
		beacon.add_action(this.frm, __("Extend Silence"), "extend_expiry", [
			{
				fieldname: "expires_at",
				fieldtype: "Datetime",
				label: __("Expires at"),
				reqd: 1,
				default: this.frm.doc.expires_at,
			},
		]);
		beacon.add_action(this.frm, __("End Silence"), "revoke", null, null, {
			confirm: [
				__("End this silence now?"),
				__("Matching alerts can fire again from the next check."),
			],
		});
	}
}
frappe.ui.form.on("Beacon Silence", {
	refresh: (frm) => new SilenceForm(frm).refresh(),
	region: (frm) => new SilenceForm(frm).preview(),
	resource_type: (frm) => new SilenceForm(frm).preview(),
	resource_id: (frm) => new SilenceForm(frm).preview(),
	include_descendants: (frm) => new SilenceForm(frm).preview(),
	rule: (frm) => new SilenceForm(frm).preview(),
	alert_key: (frm) => new SilenceForm(frm).preview(),
});
