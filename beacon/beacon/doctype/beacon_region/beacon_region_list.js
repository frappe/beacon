const TELEMETRY_COLORS = { OK: "green", Suspect: "orange", Unreachable: "red" };

frappe.listview_settings["Beacon Region"] = {
	hide_name_column: true,
	hide_name_filter: true,
	add_fields: ["status", "telemetry_state", "reporting_percent"],
	formatters: {
		telemetry_state: (value) =>
			value
				? `<span class="indicator-pill ${TELEMETRY_COLORS[value]}">${__(value)}</span>`
				: "",
	},
};
