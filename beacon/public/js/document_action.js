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

export class ResultView {
	constructor(title, results) {
		Object.assign(this, { title, results });
	}

	show() {
		frappe.msgprint({
			title: this.title,
			message: `<div class="beacon-result">${this.render(this.results)}</div>`,
			indicator: "green",
			wide: true,
		});
	}

	render(value) {
		if (value === null || value === undefined)
			return `<p class="beacon-empty">${__("The action gave no result.")}</p>`;
		if (Array.isArray(value)) return this.renderList(value);
		if (typeof value === "object") return this.renderRecord(value);
		return this.renderValue(value);
	}

	renderList(items) {
		if (!items.length) return `<p class="beacon-empty">${__("Nothing to show.")}</p>`;
		if (typeof items[0] !== "object")
			return `<ul class="beacon-result-list">${items
				.map((item) => `<li>${this.renderValue(item)}</li>`)
				.join("")}</ul>`;
		const columns = [...new Set(items.flatMap((item) => Object.keys(item)))];
		if (items.some((item) => columns.some((key) => this.isNested(item[key]))))
			return items.map((item) => this.renderRecord(item)).join("");
		return `<div class="beacon-table"><table><thead><tr>${columns
			.map((key) => `<th>${this.label(key)}</th>`)
			.join("")}</tr></thead><tbody>${items
			.map(
				(item) =>
					`<tr>${columns
						.map((key) => `<td>${this.renderValue(item[key])}</td>`)
						.join("")}</tr>`
			)
			.join("")}</tbody></table></div>`;
	}

	renderRecord(record) {
		const entries = Object.entries(record);
		const fields = entries.filter(([, value]) => !this.isNested(value));
		const sections = entries.filter(([, value]) => this.isNested(value));
		return `<div class="beacon-result-card">${
			fields.length
				? `<dl class="beacon-fields">${fields
						.map(
							([key, value]) =>
								`<div><dt>${this.label(key)}</dt><dd>${this.renderValue(
									value
								)}</dd></div>`
						)
						.join("")}</dl>`
				: ""
		}${sections
			.map(
				([key, value]) =>
					`<div class="beacon-result-section"><h6>${this.label(key)}</h6>${this.render(
						value
					)}</div>`
			)
			.join("")}</div>`;
	}

	renderValue(value) {
		if (value === null || value === undefined || value === "")
			return `<span class="text-muted">–</span>`;
		if (typeof value === "boolean") return value ? __("Yes") : __("No");
		return frappe.utils.escape_html(String(value));
	}

	isNested(value) {
		return value !== null && typeof value === "object";
	}

	label(key) {
		return frappe.utils.escape_html(
			frappe
				.unscrub(key)
				.replace(/\bId\b/g, "ID")
				.replace(/ Ms$/, " (ms)")
		);
	}
}
