<script setup>
import { Alert, useCall } from "frappe-ui";
import { NumberCard } from "frappe-ui/charts";
import { computed, reactive } from "vue";
import ChartPanel from "./ChartPanel.vue";
import { chartSummary, methodUrl } from "./api";

const props = defineProps({
	name: { type: String, required: true },
	start: { type: String, required: true },
	end: { type: String, required: true },
	keyOnly: { type: Boolean, default: false },
});

const results = reactive({});

const listing = useCall({
	url: computed(() => methodUrl("Beacon Resource", props.name, "get_charts")),
	params: () => ({ start: props.start, end: props.end }),
	refetch: true,
});
const charts = computed(() => listing.data?.charts || []);
const shown = computed(() => charts.value.filter((chart) => !props.keyOnly || chart.is_key_chart));
const sections = computed(() => {
	const bySection = {};
	for (const chart of shown.value) (bySection[chart.section || "Other"] ||= []).push(chart);
	return Object.entries(bySection);
});
</script>

<template>
	<div class="flex flex-col gap-6 text-ink-gray-8">
		<Alert
			v-if="listing.error"
			theme="red"
			title="The charts did not load."
			:description="listing.error.message"
			:primary-action="{ label: 'Try again', onClick: () => listing.reload() }"
		/>
		<p v-else-if="listing.data && !shown.length && !keyOnly" class="text-sm text-ink-gray-5">
			No chart applies to this resource type.
		</p>

		<div
			v-else-if="keyOnly && shown.length"
			class="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(9rem,1fr))]"
		>
			<ChartPanel
				v-for="chart in shown"
				:key="chart.name"
				summary
				:resource="name"
				:chart="chart"
				:start="start"
				:end="end"
			/>
		</div>

		<template v-else-if="shown.length">
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
				<NumberCard
					v-for="chart in charts.filter((chart) => chart.is_key_chart)"
					:key="chart.name"
					v-bind="chartSummary(chart, results[chart.name])"
				/>
			</div>
			<section
				v-for="[section, items] in sections"
				:key="section"
				class="flex flex-col gap-3"
			>
				<h2 class="text-lg-semibold">{{ section }}</h2>
				<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
					<ChartPanel
						v-for="chart in items"
						:key="chart.name"
						:resource="name"
						:chart="chart"
						:start="start"
						:end="end"
						:alerts="listing.data.alerts"
						@data="results[chart.name] = $event"
					/>
				</div>
			</section>
		</template>
	</div>
</template>
