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
	<p v-if="detail" class="day-detail" role="status">
		<strong>{{ detail.date }} UTC</strong> · {{ detail.title
		}}<template v-if="detail.sample">
			· {{ detail.sample.observed_minutes }} observed minutes ·
			{{ (100 * detail.sample.coverage).toFixed(1) }}% coverage ·
			{{ detail.sample.good }} good / {{ detail.sample.valid }} valid ·
			{{ detail.sample.maintenance_minutes }} maintenance minutes</template
		>
	</p>
</template>
<script setup>
import { computed, ref } from "vue";
const props = defineProps({ days: { type: Array, required: true } });
const focused = ref(89),
	selected = ref(null);
const slots = computed(() => {
	const byDate = Object.fromEntries(props.days.map((day) => [day.date, day]));
	const today = new Date();
	return Array.from({ length: 90 }, (_, index) => {
		const date = new Date(today);
		date.setUTCDate(today.getUTCDate() - (89 - index));
		const key = date.toISOString().slice(0, 10),
			day = byDate[key];
		if (!day || day.uptime === null)
			return { date: key, class: "bar-empty", title: `${key}: no usable data`, sample: day };
		const state = day.outage_minutes
			? "status-major-outage"
			: day.degraded_minutes
			? "status-degraded"
			: day.maintenance_minutes
			? "status-maintenance"
			: "status-operational";
		return {
			date: key,
			class: state,
			title: `${key}: ${day.uptime}% measured uptime, ${day.outage_minutes} min outage, ${day.degraded_minutes} min degraded`,
			sample: day,
		};
	});
});
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
