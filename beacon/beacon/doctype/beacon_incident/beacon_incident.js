const OPEN_STATUSES = ["Triggered", "Investigating", "Identified", "Monitoring"];
const MESSAGES = {
	Investigating: __("We are investigating the problem. We will post an update soon."),
	Identified: __("We found the cause and are working on a fix."),
	Monitoring: __("A fix is in place. We are monitoring the results."),
	Resolved: __("This incident is resolved. Everything works normally again."),
	Published: __(
		"We are investigating a problem. Some customers can see errors or slow responses. We will post an update soon."
	),
	Maintenance: __("The maintenance is going as planned."),
};
const messageField = (message) => ({
	fieldname: "message",
	fieldtype: "Small Text",
	label: __("Message"),
	default: message,
	reqd: 1,
});

class IncidentForm {
	constructor(frm) {
		this.frm = frm;
		this.doc = frm.doc;
		this.view = new beacon.RecordView(frm);
	}

	refresh() {
		this.frm.set_df_property("updates", "cannot_add_rows", true);
		this.frm.set_df_property("updates", "cannot_delete_rows", true);
		this.frm.dashboard.hide();
		if (this.frm.is_new()) {
			this.frm.layout.tabs.find((tab) => tab.df.fieldname === "details_tab")?.set_active();
			return;
		}
		this.renderSubscribers();
		this.view.related(
			"notifications_html",
			"Beacon Notification",
			{ incident: this.doc.name },
			[
				["status", "Status"],
				["channel", "Channel"],
				["responder", "Responder"],
				["sent_at", "Sent at"],
				["error", "Error"],
			]
		);
		if (this.doc.type === "Maintenance") this.maintenanceActions();
		else this.incidentActions();
		if (!["Merged", "Cancelled"].includes(this.doc.status)) this.visibilityActions();
		beacon.add_action(
			this.frm,
			__("Show Affected Resources"),
			"get_affected_resources",
			null,
			(rows) => this.showAffectedResources(rows),
			{ read: true }
		);
		this.irreversibleActions();
	}

	async renderSubscribers() {
		const field = this.frm.fields_dict.subscribers_html;
		if (!field) return;
		const count = await frappe.db.count("Beacon Incident Subscriber", {
			filters: { incident: this.doc.name },
		});
		field.$wrapper.html(`<p class="text-muted">${__("{0} subscribers", [count])}</p>`);
	}

	showAffectedResources(rows) {
		beacon.show_rows(
			__("Affected resources, including child resources"),
			rows
				.slice(1)
				.map((row) => Object.fromEntries(rows[0].map((key, index) => [key, row[index]])))
		);
	}

	messageFields(message, status) {
		return [
			...(status
				? [
						{
							fieldname: "status",
							fieldtype: "Select",
							label: __("Status"),
							options: ["Investigating", "Identified", "Monitoring"],
							default: status,
							reqd: 1,
							onchange() {
								const current = cur_dialog.get_value("message");
								if (!current || Object.values(MESSAGES).includes(current))
									cur_dialog.set_value("message", MESSAGES[this.get_value()]);
							},
						},
				  ]
				: []),
			...(this.doc.visibility === "Internal"
				? []
				: [
						{
							fieldname: "is_public",
							fieldtype: "Check",
							label: __("Show this update to customers"),
							default: 1,
						},
				  ]),
			messageField(message),
		];
	}

	incidentActions() {
		if (!OPEN_STATUSES.includes(this.doc.status)) {
			if (
				this.doc.status === "Resolved" &&
				this.doc.resolved_at &&
				frappe.datetime.get_diff(frappe.datetime.now_datetime(), this.doc.resolved_at) <= 7
			)
				beacon.add_action(this.frm, __("Reopen"), "reopen", null, null, {
					confirm: [
						__("Reopen this incident?"),
						__("Customers do not get a new update."),
					],
				});
			return;
		}
		if (this.doc.status === "Triggered")
			beacon.add_action(this.frm, __("Acknowledge"), "acknowledge", null, null, {
				primary: true,
			});
		const status = this.doc.status === "Triggered" ? "Investigating" : this.doc.status;
		beacon.add_action(
			this.frm,
			__("Post Update"),
			"post_update",
			this.messageFields(MESSAGES[status], status),
			null,
			{ standalone: true }
		);
		beacon.add_action(this.frm, __("Resolve"), "resolve", [
			...this.messageFields(MESSAGES.Resolved),
			{
				fieldname: "silence_hours",
				fieldtype: "Int",
				label: __("Silence Hours"),
				description: __(
					"Only for alerts that still fire. Without it, Beacon does not resolve while alerts fire."
				),
				default: 0,
			},
		]);
		beacon.add_action(this.frm, __("Notify On-call"), "page_on_call", null, null, {
			confirm: [
				__("Notify the on-call team about this incident again?"),
				__("Beacon notifies one person after another. Phone calls cost money."),
			],
		});
	}

	get maintenanceWindow() {
		const { str_to_user } = frappe.datetime;
		return `${str_to_user(this.doc.scheduled_start)} to ${str_to_user(
			this.doc.scheduled_end
		)}`;
	}

	maintenanceActions() {
		const window = this.maintenanceWindow;
		if (this.doc.status === "Scheduled")
			beacon.add_action(this.frm, __("Start Maintenance"), "start_maintenance", null, null, {
				primary: true,
				confirm: [
					__("Start this maintenance now?"),
					__("Planned time: {0}.", [window]),
					__("Alerts for its resources stop until the maintenance ends."),
				],
			});
		if (["Scheduled", "In Progress"].includes(this.doc.status))
			beacon.add_action(
				this.frm,
				__("Post Update"),
				"post_update",
				this.messageFields(MESSAGES.Maintenance)
			);
		if (this.doc.status === "In Progress") {
			beacon.add_action(this.frm, __("Extend Maintenance"), "extend_maintenance", [
				{
					fieldname: "scheduled_end",
					fieldtype: "Datetime",
					label: __("New end time"),
					reqd: 1,
					default: this.doc.scheduled_end,
				},
			]);
			beacon.add_action(
				this.frm,
				__("Complete Maintenance"),
				"complete_maintenance",
				null,
				null,
				{
					primary: true,
					confirm: [
						__("Complete this maintenance now?"),
						__("Alerts for its resources start again after a short delay."),
					],
				}
			);
		}
	}

	visibilityActions() {
		if (this.doc.visibility === "Internal") {
			beacon.add_action(
				this.frm,
				__("Publish"),
				"publish",
				[
					{
						fieldname: "visibility",
						fieldtype: "Select",
						label: __("Visibility"),
						options: ["Public", "Unlisted"],
						default: "Public",
						reqd: 1,
					},
					{
						fieldname: "affected_services",
						fieldtype: "Table",
						label: __("Customer impact"),
						data: (this.doc.affected_services || []).map((row) => ({
							service: row.service,
							region: row.region,
							impact: row.impact,
						})),
						fields: [
							{
								fieldname: "service",
								fieldtype: "Link",
								options: "Beacon Alert Rule",
								label: __("Service"),
								reqd: 1,
								in_list_view: 1,
								link_filters: '[["Beacon Alert Rule", "kind", "=", "SLO Burn"]]',
							},
							{
								fieldname: "region",
								fieldtype: "Link",
								options: "Beacon Region",
								label: __("Region (empty = all regions)"),
								in_list_view: 1,
							},
							{
								fieldname: "impact",
								fieldtype: "Select",
								options: [
									"Degraded",
									"Partial Outage",
									"Major Outage",
									"Maintenance",
								],
								label: __("Impact"),
								reqd: 1,
								in_list_view: 1,
							},
						],
					},
					{
						fieldname: "information_only",
						fieldtype: "Check",
						label: __("No service is affected (notice only)"),
					},
					messageField(
						this.doc.type === "Maintenance"
							? __("Planned maintenance: {0}.", [this.maintenanceWindow])
							: MESSAGES.Published
					),
				],
				null,
				{
					prepare: (values) => {
						if (!values.affected_services?.length && !values.information_only)
							frappe.throw(
								__(
									'Add at least one affected service, or tick "No service is affected".'
								)
							);
						return {
							visibility: values.visibility,
							message: values.message,
							affected_services: values.affected_services || [],
						};
					},
				}
			);
			return;
		}
		const visibility = this.doc.visibility === "Public" ? "Unlisted" : "Public";
		beacon.add_action(
			this.frm,
			__("Make {0}", [__(visibility)]),
			"change_visibility",
			null,
			null,
			{
				confirm: [
					__("Make this incident {0}?", [__(visibility)]),
					visibility === "Public"
						? __("The status page lists it for everyone.")
						: __(
								"The status page does not list it. Only people with its link can see it."
						  ),
				],
				prepare: () => ({ visibility }),
			}
		);
		beacon.add_action(
			this.frm,
			__("Copy Link"),
			"get_public_link",
			null,
			(link) => frappe.utils.copy_to_clipboard(link),
			{ read: true }
		);
	}

	irreversibleActions() {
		if (this.doc.type === "Incident" && OPEN_STATUSES.includes(this.doc.status))
			beacon.add_action(this.frm, __("Merge Into Another Incident"), "merge_into", [
				{
					fieldname: "target",
					fieldtype: "Link",
					label: __("Incident to keep"),
					description: __(
						"Beacon closes this incident as Merged and moves its open alerts to the incident that you choose."
					),
					options: "Beacon Incident",
					reqd: 1,
				},
			]);
		if (this.doc.status === "Scheduled")
			beacon.add_action(
				this.frm,
				__("Cancel Maintenance"),
				"cancel_maintenance",
				null,
				null,
				{
					confirm: [
						__("Cancel this planned maintenance?"),
						__("Planned time: {0}.", [this.maintenanceWindow]),
						__("The maintenance does not start."),
					],
				}
			);
		if (
			this.doc.visibility !== "Internal" &&
			!["Merged", "Cancelled"].includes(this.doc.status)
		)
			beacon.add_action(
				this.frm,
				__("Regenerate Link"),
				"regenerate_public_link",
				null,
				null,
				{
					confirm: [__("Make a new link?"), __("The old link stops working.")],
				}
			);
	}
}

frappe.ui.form.on("Beacon Incident", { refresh: (frm) => new IncidentForm(frm).refresh() });
