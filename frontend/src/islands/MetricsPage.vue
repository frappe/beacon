<script setup>
import { useHost } from "@framework/ui/island";
import { Alert, useCall } from "frappe-ui";
import { computed, ref, watch } from "vue";
import { lastHours } from "./api";
import ResourceCharts from "./ResourceCharts.vue";
import ResourcePicker from "./ResourcePicker.vue";
import TimeWindow from "./TimeWindow.vue";

// Desk page /desk/beacon-metrics/<resource>.
const props = defineProps({
	route: { type: Array, default: () => [] },
	query: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["title", "actions"]);
const host = useHost();

const name = computed(() => props.route[0] || null);
const from = ref(lastHours().from);
const to = ref(lastHours().to);
const resource = useCall({
	url: computed(() => `/api/v2/document/Beacon Resource/${encodeURIComponent(name.value)}`),
	immediate: false,
});
watch(name, (value) => value && resource.reload(), { immediate: true });

watch(
	() => resource.data,
	(doc) => {
		emit("title", doc ? `${doc.title || doc.resource_id}: metrics` : "Metrics");
		emit(
			"actions",
			doc
				? [
						{
							label: "Log viewer",
							onClick: () =>
								host.navigate?.(`beacon-logs/${doc.region}/${doc.name}`),
						},
						{
							label: "Open resource",
							onClick: () => host.navigate?.(`beacon-resource/${doc.name}`),
						},
				  ]
				: []
		);
	},
	{ immediate: true }
);
</script>

<template>
	<div
		class="flex h-full flex-col gap-4 overflow-y-auto px-3 pb-10 pt-5 text-ink-gray-8 sm:px-5"
	>
		<div class="flex flex-wrap items-center gap-2">
			<ResourcePicker
				:model-value="name"
				:title="resource.data?.title"
				@update:model-value="
					(value) => value && host.navigate?.(`beacon-metrics/${value}`)
				"
			/>
			<span class="flex-1" />
			<TimeWindow v-model:from="from" v-model:to="to" />
		</div>

		<div v-if="!name" class="flex flex-col items-center gap-3 py-16 text-center">
			<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
				<span class="lucide-line-chart block size-6" aria-hidden="true" />
			</div>
			<p class="text-base text-ink-gray-7">Choose a resource</p>
			<p class="text-sm text-ink-gray-5">Its charts for the chosen window show here.</p>
		</div>
		<Alert
			v-else-if="resource.error"
			theme="red"
			title="The resource did not load."
			:description="resource.error.message"
		/>
		<ResourceCharts
			v-else-if="resource.data"
			:key="name"
			:name="name"
			:start="from"
			:end="to"
		/>
	</div>
</template>
