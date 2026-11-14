frappe.ui.form.on("Beacon Resource Source", {
	async refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.last_error) frm.set_intro(frm.doc.last_error, "red");
		beacon.add_action(
			frm,
			__("Fetch Now"),
			"sync",
			null,
			async (result) => {
				beacon.show_results(__("Fetch result"), result);
				await frm.reload_doc();
			},
			{ standalone: true }
		);
		if (!frm.doc.held_removals) return;
		const wrapper = frm.fields_dict.removals_html.$wrapper;
		wrapper.html(`<p>${__("Loading held removals...")}</p>`);
		try {
			const { message } = await frm.call("get_held_removals");
			const view = new beacon.RecordView(frm);
			wrapper.html(
				`<p><b>${__(
					"Beacon did not remove {0} resources and their child resources, because too many resources are missing at the same time.",
					[message.total]
				)}</b></p><p class="text-muted">${
					message.truncated
						? __("This list shows the first 20.")
						: __("This is the full list.")
				}</p>${await view.table("Beacon Resource", message.sample, [
					["resource_id", "Resource"],
					["resource_type", "Type"],
					["parent_resource", "Parent"],
				])}`
			);
			view.link("View Held Removals", "Beacon Resource", {
				source: frm.doc.name,
				removal_held: 1,
			});
			beacon.add_action(
				frm,
				__("Approve Removals"),
				"apply_held_removals",
				null,
				async (result) => {
					beacon.show_results(__("Removal result"), result);
					await frm.reload_doc();
				},
				{
					primary: true,
					confirm: [
						__("Remove these {0} resources from Beacon?", [message.total]),
						__(
							"First, make sure that the source system does not have these resources. Beacon fetches the list again before it removes them."
						),
					],
					prepare: () => ({ expected_count: message.total }),
				}
			);
		} catch (error) {
			console.error(error);
			wrapper.html(
				`<p class="text-danger">${__(
					"The held removals did not load. Reload the page, then examine them before you approve."
				)}</p>`
			);
		}
	},
});
