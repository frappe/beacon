export class DocumentAction {
	constructor(frm, label, method, fields, onDone, options) {
		Object.assign(this, { frm, label, method, fields, onDone, options });
	}

	add() {
		if (!this.frm.perm.some((permission) => permission.write) && !this.options.read) return;
		const { primary, standalone } = this.options;
		const group = primary || standalone ? null : this.options.group || __("Actions");
		const button = this.frm.add_custom_button(this.label, () => this.open(), group);
		if (primary) this.frm.change_custom_button_type(this.label, null, "primary");
		return button;
	}

	open() {
		if (this.pending) return;
		if (this.fields) {
			this.dialog = new frappe.ui.Dialog({
				title: this.label,
				fields: this.fields,
				primary_action_label: this.label,
				primary_action: (values) => this.run(values),
			});
			this.dialog.show();
		} else if (this.options.confirm) {
			frappe.confirm([this.options.confirm].flat().join("<br><br>"), () => this.run());
		} else this.run();
	}

	async run(args = {}) {
		if (this.pending) return;
		if (this.options.prepare) args = this.options.prepare(args);
		const { draft } = this.options;
		if (!draft && this.frm.is_dirty()) {
			frappe.msgprint(__("Save your changes first."));
			return;
		}
		this.pending = true;
		this.dialog?.disable_primary_action();
		try {
			// Beacon changes records in the background, so send the current version, not the one the form loaded.
			// A draft action, such as a test, runs on the form as edited and saves nothing.
			if (!draft) await this.frm.reload_doc();
			const { message } = await this.frm.call(this.method, args, null, {
				freeze: true,
				freeze_message: __("Please wait..."),
			});
			this.dialog?.hide();
			if (this.onDone) await this.onDone(message);
			else await this.frm.reload_doc();
		} finally {
			this.pending = false;
			this.dialog?.enable_primary_action();
		}
	}
}
