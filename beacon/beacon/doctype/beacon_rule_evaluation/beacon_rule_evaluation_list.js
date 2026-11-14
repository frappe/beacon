const STATE_COLORS = { Healthy: "green", Erroring: "orange", Broken: "red" };

frappe.listview_settings["Beacon Rule Evaluation"] = {
	hide_name_column: true,
	hide_name_filter: true,
	add_fields: ["region", "state", "last_success_at"],
	get_indicator: (doc) => [__(doc.state), STATE_COLORS[doc.state], `state,=,${doc.state}`],
};
