import { DocumentAction, ResultView } from "./document_action";
import { RecordView } from "./record_view";

frappe.provide("beacon");
beacon.add_action = (frm, label, method, fields, on_done, options = {}) =>
	new DocumentAction(frm, label, method, fields, on_done, options).add();
beacon.show_results = (title, results) => new ResultView(title, results).show();
beacon.RecordView = RecordView;
beacon.add_runbook_button = (frm, url) => {
	if (/^https?:\/\//.test(url || ""))
		return frm.add_custom_button(__("Open Runbook"), () =>
			window.open(url, "_blank", "noopener")
		);
};

$(document).on("click", ".beacon-table tr[data-name]", function (event) {
	if (!this.dataset.name || event.target.closest("a")) return;
	frappe.set_route("Form", $(this).closest(".beacon-table").data("doctype"), this.dataset.name);
});

// Desk shows every time in the user's zone; the zone hint only matters where a time is typed.
$(document).on("form-refresh", (event, frm) => {
	if (!frm.doctype.startsWith("Beacon ")) return;
	for (const field of Object.values(frm.fields_dict))
		if (field.df.fieldtype === "Datetime" && field.df.read_only) field.df.hide_timezone = 1;
});
