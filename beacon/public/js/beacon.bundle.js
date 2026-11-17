import { DocumentAction } from "./document_action";
import { RecordView } from "./record_view";
import { showFields, showRows, showTest } from "./result_view";

frappe.provide("beacon");
beacon.add_action = (frm, label, method, fields, on_done, options = {}) =>
	new DocumentAction(frm, label, method, fields, on_done, options).add();
beacon.show_fields = showFields;
beacon.show_rows = showRows;
beacon.show_test = showTest;
beacon.RecordView = RecordView;
beacon.add_runbook_button = (frm, url) => {
	if (/^https?:\/\//.test(url || ""))
		return frm.add_custom_button(__("Open Runbook"), () =>
			window.open(url, "_blank", "noopener")
		);
};
beacon.add_silence_actions = (frm) => {
	if (frm.is_new()) return;
	const until = frm.doc.silenced_until;
	if (until && frappe.datetime.now_datetime() < until) {
		const line = __("Silenced until {0}.", [frappe.datetime.str_to_user(until)]);
		const intro = frm.$intro_message?.text();
		frm.set_intro(intro ? `${intro} ${line}` : line, intro ? "blue" : "orange");
		beacon.add_action(frm, __("End Silence"), "silence", null, null, {
			confirm: __("End this silence now?"),
			prepare: () => ({ hours: 0 }),
			read: true,
		});
	}
	beacon.add_action(
		frm,
		__("Silence"),
		"silence",
		[
			{ fieldname: "hours", fieldtype: "Int", label: __("Hours"), default: 4, reqd: 1 },
			{ fieldname: "reason", fieldtype: "Data", label: __("Reason"), reqd: 1 },
		],
		null,
		{ read: true }
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
