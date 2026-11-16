const PAGE_SIZE = 20;

export class RecordView {
	constructor(frm) {
		this.frm = frm;
	}

	async related(fieldname, doctype, filters, columns) {
		const field = this.frm.fields_dict[fieldname];
		if (!field || this.frm.is_new()) return;
		field.$wrapper.html(`<p class="text-muted">${__("Loading related records...")}</p>`);
		const name = this.frm.doc.name;
		try {
			const rows = await frappe.db.get_list(doctype, {
				filters,
				fields: ["name", ...columns.map((column) => column[0])],
				order_by: "modified desc",
				limit: PAGE_SIZE,
			});
			const total =
				rows.length < PAGE_SIZE
					? rows.length
					: await frappe.db.count(doctype, { filters });
			if (this.frm.doc.name !== name) return;
			this.frm.toggle_display(fieldname, rows.length > 0);
			field.$wrapper.html(await this.table(doctype, rows, columns, total));
			field.$wrapper
				.find(".beacon-table-footer")
				.on("click", () => frappe.set_route("List", doctype, filters));
		} catch (error) {
			console.error(error);
			if (this.frm.doc.name === name)
				field.$wrapper.html(
					`<p class="text-danger">${__(
						"The records did not load. Reload the page to try again."
					)}</p>`
				);
		}
	}

	async table(doctype, rows, columns, total = rows.length) {
		if (!rows.length) return `<p class="beacon-empty">${__("Nothing to show.")}</p>`;
		await frappe.model.with_doctype(doctype);
		const escape = frappe.utils.escape_html;
		const header = columns.map(([, label]) => `<th>${escape(__(label))}</th>`).join("");
		const body = rows
			.map(
				(row) =>
					`<tr data-name="${escape(row.name || "")}">${columns
						.map(([field]) => `<td>${this.cell(doctype, row, field)}</td>`)
						.join("")}</tr>`
			)
			.join("");
		return `<div class="beacon-table" data-doctype="${escape(
			doctype
		)}"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>${
			total > rows.length
				? `<a class="beacon-table-footer">${__("View all {0}", [total])} →</a>`
				: ""
		}</div>`;
	}

	cell(doctype, row, field) {
		if (["status", "state"].includes(field) && row[field]) {
			const [label, color] = frappe.get_indicator(row, doctype) || [row[field], "gray"];
			return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(
				label
			)}</span>`;
		}
		const value = frappe.format(row[field], frappe.meta.get_docfield(doctype, field), {
			inline: true,
		});
		return value || `<span class="text-muted">–</span>`;
	}

	link(label, doctype, filters) {
		this.frm.add_custom_button(__(label), () => frappe.set_route("List", doctype, filters));
	}

	managed(editable) {
		for (const field of this.frm.meta.fields) {
			if (!["Tab Break", "Section Break", "Column Break", "HTML"].includes(field.fieldtype))
				this.frm.set_df_property(
					field.fieldname,
					"read_only",
					!editable.includes(field.fieldname)
				);
		}
		const { source } = this.frm.doc;
		this.frm.set_intro(
			[
				source
					? __("This comes from {0}.", [source.bold()])
					: __("Beacon creates this record."),
				editable.length
					? __("You can only change {0} here.", [
							editable
								.map((field) => this.frm.fields_dict[field].df.label)
								.join(", "),
					  ])
					: "",
			].join(" "),
			"blue"
		);
	}
}
