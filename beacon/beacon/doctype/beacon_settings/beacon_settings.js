frappe.ui.form.on("Beacon Settings", {
	refresh(frm) {
		const user = {
			fieldname: "user",
			fieldtype: "Link",
			label: __("User"),
			options: "User",
			reqd: 1,
		};
		if (frm.doc.paging_paused)
			beacon.add_action(frm, __("Resume Notifications"), "resume_paging", null, null, {
				primary: true,
				confirm: __("Start phone calls and emails to the on-call team again?"),
			});
		beacon.add_action(frm, __("Change Primary On-call"), "set_primary_override", [
			{
				...user,
				description: __("Beacon notifies this person first until the time below."),
			},
			{ fieldname: "until", fieldtype: "Datetime", label: __("Until"), reqd: 1 },
		]);
		beacon.add_action(frm, __("Send Test Notification"), "send_test_page", [
			{
				fieldname: "channel",
				fieldtype: "Select",
				label: __("Channel"),
				options: ["Call", "Email", "Chat"],
				reqd: 1,
			},
			{
				...user,
				reqd: 0,
				depends_on: 'eval: doc.channel !== "Chat"',
				mandatory_depends_on: 'eval: doc.channel !== "Chat"',
			},
		]);
		beacon.add_action(
			frm,
			__("Sync Definitions"),
			"sync_definitions",
			null,
			async (results) => {
				beacon.show_rows(__("Sync result"), results);
				await frm.reload_doc();
			},
			{ confirm: __("Read the rule and service files of every source and apply them now?") }
		);
		if (!frm.doc.paging_paused)
			beacon.add_action(frm, __("Pause Notifications"), "pause_paging", [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					description: __(
						"Beacon stops phone calls and emails to the on-call team until you resume. Chat messages and incidents continue."
					),
					reqd: 1,
				},
			]);
	},
});
