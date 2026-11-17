<template>
	<div class="bars" role="group" aria-label="Daily service history">
		<button
			v-for="(day, index) in slots"
			:key="day.date"
			class="bar"
			:class="day.class"
			:title="day.title"
			:aria-label="day.title"
			:aria-pressed="selected === day.date"
			:tabindex="focused === index ? 0 : -1"
			@focus="focused = index"
			@click="selected = day.date"
			@keydown="move($event, index)"
		></button>
	</div>
	<div v-if="detail" class="day-detail" role="status">
		<div class="row">
			<strong>{{ detail.date }} UTC</strong><span>{{ detail.label }}</span>
		</div>
		<dl v-if="detail.sample" class="day-facts">
			<div v-if="!declaredSince">
				<dt>Uptime</dt>
				<dd>{{ detail.sample.uptime }}%</dd>
			</div>
			<div>
				<dt>Major outage</dt>
				<dd>{{ detail.major }} min</dd>
			</div>
			<div>
				<dt>Partial outage</dt>
				<dd>{{ detail.sample.partial_outage_minutes }} min</dd>
			</div>
			<div>
				<dt>Degraded</dt>
				<dd>{{ detail.sample.degraded_minutes }} min</dd>
			</div>
		</dl>
		<p class="muted">{{ detail.reason }}</p>
	</div>
</template>
<script setup>
import { computed, ref } from "vue";
const props = defineProps({
	days: { type: Array, required: true },
	// A declared service lists only the days with a published impact. Every other day since it was added had none.
	declaredSince: { type: String, default: "" },
});
const NO_IMPACT = {
	degraded_minutes: 0,
	outage_minutes: 0,
	partial_outage_minutes: 0,
	maintenance_minutes: 0,
};
const focused = ref(89),
	selected = ref(null);
const slots = computed(() => {
	const byDate = Object.fromEntries(props.days.map((day) => [day.date, day]));
	const today = new Date();
	return Array.from({ length: 90 }, (_, index) => {
		const date = new Date(today);
		date.setUTCDate(today.getUTCDate() - (89 - index));
		const key = date.toISOString().slice(0, 10);
		const declared = props.declaredSince && key >= props.declaredSince;
		const day = byDate[key] || (declared ? { date: key, ...NO_IMPACT } : undefined);
		if (!day || (day.uptime === null && !declared))
			return {
				date: key,
				class: "bar-empty",
				title: `${key}: no usable data`,
				label: "No data",
				reason: "No usable measurement.",
				sample: day,
			};
		const description = describe(day);
		return {
			date: key,
			sample: day,
			title: declared
				? `${key}: ${description.label}`
				: `${key}: ${description.label}, ${day.uptime}% uptime`,
			...description,
		};
	});
});
// Statuspage counts a Partial Outage minute as 0.3 of a Major Outage minute.
const PARTIAL_OUTAGE_WEIGHT = 0.3,
	RED_DOWNTIME_MINUTES = 15;
function describe(day) {
	const major = day.outage_minutes - day.partial_outage_minutes;
	const downtime = major + PARTIAL_OUTAGE_WEIGHT * day.partial_outage_minutes;
	return {
		major,
		...colorDay(
			day,
			downtime,
			`${downtime.toFixed(
				1
			)} minutes of downtime: ${major} major + ${PARTIAL_OUTAGE_WEIGHT} × ${
				day.partial_outage_minutes
			} partial.`
		),
	};
}
function colorDay(day, downtime, reason) {
	if (downtime >= RED_DOWNTIME_MINUTES)
		return { class: "status-major-outage", label: "Downtime", reason };
	if (downtime > 0) return { class: "status-partial-outage", label: "Minor downtime", reason };
	if (day.degraded_minutes)
		return {
			class: "status-degraded",
			label: "Degraded",
			reason: "No downtime. Some minutes were degraded.",
		};
	if (day.maintenance_minutes)
		return {
			class: "status-maintenance",
			label: "Maintenance",
			reason: `No downtime. ${day.maintenance_minutes} minutes of maintenance.`,
		};
	return { class: "status-operational", label: "Operational", reason: "No downtime." };
}
const detail = computed(() => slots.value.find((day) => day.date === selected.value));
function move(event, index) {
	let next;
	if (event.key === "ArrowLeft") next = Math.max(0, index - 1);
	else if (event.key === "ArrowRight") next = Math.min(89, index + 1);
	else if (event.key === "Home") next = 0;
	else if (event.key === "End") next = 89;
	else return;
	event.preventDefault();
	focused.value = next;
	selected.value = slots.value[next].date;
	event.currentTarget.parentElement.children[next].focus();
}
</script>
