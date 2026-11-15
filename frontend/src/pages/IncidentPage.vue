<template>
	<p v-if="error" class="card error">
		{{ error === "Not found" ? "This incident does not exist or is not published." : error }}
	</p>
	<template v-else-if="data">
		<section class="card">
			<div class="row">
				<h1>{{ data.title }}</h1>
				<span class="badge">{{ data.status }}</span>
			</div>
			<p class="muted">
				<template v-if="data.type === 'Maintenance'">
					Scheduled {{ formatTime(data.scheduled_start) }} to
					{{ formatTime(data.scheduled_end) }}
				</template>
				<template v-else>Started {{ formatTime(data.started_at) }}</template>
				<template v-if="data.resolved_at">
					· Resolved {{ formatTime(data.resolved_at) }}</template
				>
			</p>
			<p v-if="data.affected_services.length" class="muted">
				Affected:
				{{
					data.affected_services
						.map((row) => `${row.title}${row.region ? ` (${row.region})` : ""}`)
						.join(", ")
				}}
			</p>
		</section>

		<section class="card">
			<h2>Updates</h2>
			<div v-for="update in data.updates" :key="update.posted_at" class="update">
				<strong>{{ update.status }}</strong>
				<span class="muted"> · {{ formatTime(update.posted_at) }}</span>
				<p>{{ update.message }}</p>
			</div>
			<p v-if="!data.updates.length" class="muted">No updates yet.</p>
		</section>

		<!-- public_rca is Text Editor HTML written by staff. -->
		<section v-if="data.public_rca" class="card">
			<h2>Root cause analysis</h2>
			<div v-html="data.public_rca"></div>
		</section>

		<IncidentSubscribe v-if="!data.resolved_at" :public-id="publicId" />
	</template>
	<p v-else class="muted">Loading…</p>
</template>

<script setup>
import IncidentSubscribe from "../components/IncidentSubscribe.vue";
import { formatTime, useCall } from "../api.js";

const props = defineProps({ publicId: String });
const { data, error } = useCall("get_incident", () => ({ public_id: props.publicId }), 60);
</script>
