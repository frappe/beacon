<template>
	<p v-if="error" class="card error">Could not load the history: {{ error }}</p>
	<template v-else-if="data">
		<IncidentList heading="Past 90 days" :incidents="data.incidents" />
		<p v-if="!data.incidents.length" class="card muted">No incidents in the past 90 days.</p>
	</template>
	<p v-else class="muted">Loading…</p>
</template>

<script setup>
import { useCall } from "../api.js";
import IncidentList from "../components/IncidentList.vue";

const { data, error } = useCall("get_history");
</script>
