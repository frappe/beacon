<template>
	<section v-if="incidents.length" class="card">
		<h2>{{ heading }}</h2>
		<router-link
			v-for="incident in incidents"
			:key="incident.name"
			:to="`/incidents/${incident.name}`"
			class="incident-row"
		>
			<span class="incident-title">{{ incident.title }}</span>
			<span class="muted">
				{{ incident.status }} ·
				{{ formatTime(incident.scheduled_start || incident.started_at) }}
			</span>
		</router-link>
	</section>
</template>

<script setup>
import { formatTime } from "../api.js";

defineProps({
	heading: { type: String, required: true },
	incidents: { type: Array, required: true },
});
</script>
