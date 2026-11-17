<script setup>
import { useCall } from "frappe-ui";
import { BarChart, ChartCard, LineChart, NumberCard } from "frappe-ui/charts";
import { computed, watch } from "vue";
import { chartSummary, formatValue, methodUrl, utcClock } from "./api";

const props = defineProps({
	resource: { type: String, required: true },
	chart: { type: Object, required: true },
	start: { type: String, required: true },
	end: { type: String, required: true },
	alerts: { type: Array, default: () => [] },
	summary: { type: Boolean, default: false },
});
const emit = defineEmits(["data"]);

const result = useCall({
	url: computed(() => methodUrl("Beacon Resource", props.resource, "get_chart")),
	params: () => ({ chart: props.chart.name, start: props.start, end: props.end }),
	refetch: true,
});
watch(
	() => result.data,
	(data) => emit("data", data)
);

const rows = computed(() =>
	(result.data?.series || []).flatMap((series) =>
		series.points.map(([ts, value]) => ({ ts: utcClock(ts), series: series.name, value }))
	)
);

const referenceLines = computed(() =>
	props.alerts.map((alert) => ({
		axis: "x",
		value: utcClock(alert.fired_at || alert.started_at),
		label: alert.title,
		color: "var(--ink-red-5)",
		dashed: !alert.fired_at,
	}))
);
</script>

<template>
	<NumberCard
		v-if="summary"
		v-bind="chartSummary(chart, result.data)"
		:loading="result.loading && !result.data"
	/>
	<ChartCard v-else class="h-72">
		<component
			:is="chart.style === 'Bar' ? BarChart : LineChart"
			:title="chart.title"
			:data="rows"
			x="ts"
			y="value"
			split-by="series"
			:stacked="chart.style === 'Bar'"
			:x-axis="{ type: 'time' }"
			:y-axis="{ format: (value) => formatValue(value, chart.unit || '') }"
			:reference-lines="referenceLines"
			:loading="result.loading && !result.data"
			:error="result.error?.message"
		/>
	</ChartCard>
</template>
