<template>
	<p v-if="error" class="card error">Could not load the status: {{ error }}</p>
	<template v-else-if="data">
		<section class="card banner" :class="statusClass(data.status)">
			<h1>{{ SUMMARY[data.status] }}</h1>
			<span class="muted">Page refreshed {{ formatTime(data.updated_at) }}</span>
		</section>

		<IncidentList heading="Active incidents" :incidents="data.incidents" />
		<IncidentList heading="Maintenance" :incidents="data.maintenances" />

		<section v-for="region in data.regions" :key="region.region" class="card">
			<div class="row">
				<h2>{{ region.title }}</h2>
				<StatusBadge :status="region.status" />
			</div>
			<ServiceList :services="region.services" />
		</section>
		<section v-if="data.global_services?.length" class="card">
			<h2>Global services</h2>
			<ServiceList :services="data.global_services" />
		</section>
	</template>
	<p v-else class="muted">Loading…</p>
</template>

<script setup>
import { watch } from "vue";

import { formatTime, useCall } from "../api.js";
import IncidentList from "../components/IncidentList.vue";
import StatusBadge from "../components/StatusBadge.vue";
import ServiceList from "../components/ServiceList.vue";
import { SUMMARY, statusClass } from "../status.js";

const emit = defineEmits(["title"]);
const { data, error } = useCall("get_status", () => ({}), 60);
watch(data, (value) => value?.title && emit("title", value.title));
</script>
