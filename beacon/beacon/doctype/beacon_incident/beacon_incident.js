const OPEN_STATUSES = ["Triggered", "Investigating", "Identified", "Monitoring"];
const escape = frappe.utils.escape_html;
// Each dialog starts with the usual message for its status. Staff edit it before they send it.
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

// Replace the message only while it is still a prefilled one, so a typed message stays.
function prefillMessage(dialog, message) {
	const current = dialog.get_value("message");
	if (!current || Object.values(MESSAGES).includes(current))
		dialog.set_value("message", message);
}

class IncidentForm {
	constructor(frm) {
		this.frm = frm;
		this.doc = frm.doc;
		this.view = new beacon.RecordView(frm);
	}

	refresh() {
		// Only actions add updates. Staff can change only the Public flag of a row.
		this.frm.set_df_property("updates", "cannot_add_rows", true);
		this.frm.set_df_property("updates", "cannot_delete_rows", true);
		if (this.frm.is_new()) {
			this.frm.layout.tabs.find((tab) => tab.df.fieldname === "details_tab")?.set_active();
			return;
		}
		this.renderOverview();
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

	async renderOverview() {
		const name = this.doc.name;
		const field = this.frm.fields_dict.overview_html;
		field.$wrapper.html(`<p class="text-muted">${__("Loading overview...")}</p>`);
		try {
			const { message } = await this.frm.call("get_overview");
			if (this.frm.doc.name !== name) return;
			this.overview = message;
			field.$wrapper.html(this.overviewHtml(message));
			this.bindOverview();
		} catch (error) {
			console.error(error);
			if (this.frm.doc.name === name)
				field.$wrapper.html(
					`<p class="text-danger">${__(
						"The overview did not load. Reload to try again."
					)}</p>`
				);
		}
	}

	overviewHtml({ cause, alerts, total, firing }) {
		return `<div class="beacon-overview">${this.overviewBanner(firing, total)}${
			cause ? this.causeCard(cause) : ""
		}${this.alertsCard(alerts, firing, total)}${this.timelineCard()}</div>`;
	}

	overviewBanner(firing, total) {
		if (["Waiting", "Calling"].includes(this.doc.escalation)) {
			const next = frappe.datetime.str_to_user(this.doc.next_page_at);
			const text =
				this.doc.escalation === "Waiting"
					? __("Beacon notifies the on-call team at {0}. Acknowledge to stop it.", [
							next,
					  ])
					: __("Beacon is notifying the on-call team. Next notification at {0}.", [
							next,
					  ]);
			return `<div class="beacon-overview-banner">${escape(text)}</div>`;
		}
		if (this.doc.status === "Monitoring")
			return `<div class="beacon-overview-banner blue">${escape(
				__("{0} of {1} alerts still fire.", [firing, total])
			)}</div>`;
		return "";
	}

	causeCard(cause) {
		const facts = [
			[__("Resource"), `${cause.resource_title} (${cause.resource_type})`],
			[__("Value"), this.text(cause.value)],
			[__("Failing since"), this.time(cause.started_at)],
			[__("Fired"), this.time(cause.fired_at)],
		];
		const heading =
			cause.resource_type === "Region"
				? __("{0} in {1}", [cause.rule_title, cause.resource_title])
				: __("{0} on {1}", [cause.rule_title, cause.resource_title]);
		return `<div class="beacon-overview-card">
			<h3>${escape(heading)}</h3>
			${
				cause.rule_description
					? `<p class="beacon-overview-desc">${escape(cause.rule_description)}</p>`
					: ""
			}
			<dl class="beacon-overview-facts">${facts
				.map(
					([label, value]) =>
						`<div><dt>${escape(label)}</dt><dd title="${escape(value)}">${escape(
							value
						)}</dd></div>`
				)
				.join("")}</dl>
			${cause.detail ? `<div class="beacon-overview-evidence">${escape(cause.detail)}</div>` : ""}
			<div class="beacon-overview-links">
				${
					/^https?:\/\//.test(cause.runbook_url || "")
						? `<a href="${escape(
								cause.runbook_url
						  )}" target="_blank" rel="noopener">${__("Runbook")}</a>`
						: ""
				}
				<a href="#" data-action="affected">${__("Affected resources")}</a>
				<a href="#" data-action="rule">${__("Rule")}</a>
			</div>
		</div>`;
	}

	alertsCard(alerts, firing, total) {
		if (!alerts.length) return "";
		const count = __("{0} firing", [firing]);
		const limit =
			total > alerts.length
				? ` (${__("Showing first {0} of {1}", [alerts.length, total])})`
				: "";
		return `<div class="beacon-overview-card">
			<h2>${__("Alerts")} <span class="beacon-overview-muted">${escape(count + limit)}</span></h2>
			${alerts.map((alert) => this.alertRow(alert)).join("")}
		</div>`;
	}

	alertRow(alert) {
		const open = ["Pending", "Firing"].includes(alert.status);
		const pill = open
			? this.pill(alert.severity, this.severityColor(alert.severity))
			: this.pill(alert.resolution || alert.status, "green");
		return `<div class="beacon-overview-row">
			<div class="beacon-overview-row-title">${escape(alert.title)}</div>
			<div class="beacon-overview-row-detail">${escape(alert.detail || "")}</div>
			<div class="beacon-overview-row-side">${pill}<small>${escape(
			this.age(alert.started_at)
		)}</small></div>
		</div>`;
	}

	pill(label, color) {
		return `<span class="beacon-overview-pill ${color}">${escape(label)}</span>`;
	}

	severityColor(severity) {
		return (
			{ Critical: "red", High: "orange", Medium: "amber", Low: "gray" }[severity] || "gray"
		);
	}

	timelineCard() {
		const updates = [...(this.doc.updates || [])].reverse();
		if (!updates.length) return "";
		return `<div class="beacon-overview-card">
			<h2>${__("Timeline")}</h2>
			<ul class="beacon-overview-timeline">${updates
				.map(
					(update) =>
						`<li><time title="${escape(this.time(update.posted_at))}">${escape(
							this.clock(update.posted_at)
						)}</time><div>${escape(
							update.message
						)} <span class="beacon-overview-who">· ${escape(
							this.who(update.posted_by)
						)}</span></div></li>`
				)
				.join("")}</ul>
		</div>`;
	}

	bindOverview() {
		const wrapper = this.frm.fields_dict.overview_html.$wrapper;
		wrapper.find('[data-action="affected"]').on("click", (event) => {
			event.preventDefault();
			this.frm
				.call("get_affected_resources")
				.then(({ message }) => this.showAffectedResources(message));
		});
		wrapper.find('[data-action="rule"]').on("click", (event) => {
			event.preventDefault();
			frappe.set_route("Form", "Beacon Alert Rule", this.overview.cause.rule);
		});
	}

	showAffectedResources(rows) {
		beacon.show_results(
			__("Affected resources, including child resources"),
			rows
				.slice(1)
				.map((row) => Object.fromEntries(rows[0].map((key, index) => [key, row[index]])))
		);
	}

	text(value) {
		return value === null || value === undefined || value === "" ? "–" : String(value);
	}

	time(value) {
		return value ? frappe.datetime.str_to_user(value) : "–";
	}

	clock(value) {
		if (!value) return "–";
		return frappe.datetime.convert_to_user_tz(value, false).format("HH:mm");
	}

	age(value) {
		if (!value) return "";
		const seconds = moment.utc().diff(moment.utc(value), "seconds");
		return frappe.utils.get_formatted_duration(seconds, { hide_seconds: 1 });
	}

	who(user) {
		return user === "Administrator" ? __("Beacon") : frappe.user.full_name(user) || user;
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
			[
				{
					fieldname: "status",
					fieldtype: "Select",
					label: __("Status"),
					options: ["Investigating", "Identified", "Monitoring"],
					default: status,
					reqd: 1,
					onchange() {
						prefillMessage(cur_dialog, MESSAGES[this.get_value()]);
					},
				},
				...this.audienceField(),
				messageField(MESSAGES[status]),
			],
			null,
			{ standalone: true }
		);
		beacon.add_action(this.frm, __("Resolve"), "resolve", [
			...this.audienceField(),
			messageField(MESSAGES.Resolved),
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
		if (this.doc.status === "Scheduled") {
			beacon.add_action(this.frm, __("Start Maintenance"), "start_maintenance", null, null, {
				primary: true,
				confirm: [
					__("Start this maintenance now?"),
					__("Planned time: {0}.", [this.maintenanceWindow]),
					__("Alerts for its resources stop until the maintenance ends."),
				],
			});
		}
		if (["Scheduled", "In Progress"].includes(this.doc.status))
			beacon.add_action(this.frm, __("Post Update"), "post_update", [
				messageField(MESSAGES.Maintenance),
				...this.audienceField(),
			]);
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

	audienceField() {
		return this.doc.visibility === "Internal"
			? []
			: [
					{
						fieldname: "is_public",
						fieldtype: "Check",
						label: __("Show this update to customers"),
						default: 1,
					},
			  ];
	}

	visibilityActions() {
		if (this.doc.visibility === "Internal") {
			beacon.add_action(
				this.frm,
				__("Publish"),
				"publish",
				[
					{
						fieldname: "preview",
						fieldtype: "HTML",
						options: `<p><b>${frappe.utils.escape_html(this.doc.title)}</b></p><p>${__(
							"Customers see this title, the message and the impact below."
						)}</p><p>${__("Earlier internal updates stay hidden.")}</p>`,
					},
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
								options: "Beacon Service",
								label: __("Service"),
								reqd: 1,
								in_list_view: 1,
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
		} else {
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
				{ confirm: [__("Make a new link?"), __("The old link stops working.")] }
			);
	}
}

frappe.ui.form.on("Beacon Incident", { refresh: (frm) => new IncidentForm(frm).refresh() });
