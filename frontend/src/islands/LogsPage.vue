<script setup>
import { useHost } from "@framework/ui/island";
import { Alert, Select, useCall } from "frappe-ui";
import { computed, watch } from "vue";
import LogViewer from "./LogViewer.vue";
import ResourcePicker from "./ResourcePicker.vue";

// Desk page /desk/beacon-logs/<region>/<resource>. The route holds the choice, so a link opens the same view.
const props = defineProps({
	route: { type: Array, default: () => [] },
	query: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["title", "actions"]);
emit("title", "Log viewer");
emit("actions", []);
const host = useHost();

const regions = useCall({
	url: "/api/v2/document/Beacon Region",
	params: {
		fields: JSON.stringify(["name", "title"]),
		filters: JSON.stringify({ status: "Active" }),
		order_by: "name asc",
		limit: 100,
	},
});
const regionOptions = computed(() =>
	(regions.data || []).map((row) => ({ label: row.title || row.name, value: row.name }))
);
const region = computed(() => props.route[0] || regions.data?.[0]?.name || null);
const resource = computed(() => props.route[1] || null);

// The picker shows the title of a resource that its search results leave out.
const chosen = useCall({
	url: computed(() => `/api/v2/document/Beacon Resource/${encodeURIComponent(resource.value)}`),
	immediate: false,
});
watch(resource, (value) => value && chosen.reload(), { immediate: true });
</script>

<template>
	<div
		class="flex h-full flex-col gap-4 overflow-y-auto px-3 pb-10 pt-5 text-ink-gray-8 sm:px-5"
	>
		<Alert
			v-if="regions.error"
			theme="red"
			title="The regions did not load."
			:description="regions.error.message"
		/>
		<LogViewer v-else-if="region" :region="region" :resource="resource">
			<template #filters>
				<Select
					:model-value="region"
					class="w-40"
					:options="regionOptions"
					placeholder="Region"
					@update:model-value="host.navigate?.(`beacon-logs/${$event}`)"
				/>
				<ResourcePicker
					:model-value="resource"
					:region="region"
					placeholder="All resources"
					clearable
					:title="chosen.data?.name === resource ? chosen.data.title : undefined"
					@update:model-value="
						host.navigate?.(['beacon-logs', region, $event].filter(Boolean).join('/'))
					"
				/>
			</template>
		</LogViewer>
	</div>
</template>
