<template>
	<div class="service">
		<div class="row">
			<strong :title="service.description">{{ service.title }}</strong
			><StatusBadge :status="service.status" />
		</div>
		<p v-if="service.measurement === 'Declared'" class="muted">
			Set by incidents, not measured
		</p>
		<template v-else>
			<p class="muted">
				Last measured {{ formatTime(service.checked_at)
				}}<span v-if="service.stale"> · Measurement is stale or unavailable</span
				><span v-else-if="service.measurement_status === 'Unknown'">
					· No usable measurement</span
				>
			</p>
			<p class="muted">
				{{ service.history_days }}-day measured uptime:
				{{ service.uptime === null ? "No data" : service.uptime + "%" }} · Observed
				coverage {{ (100 * service.coverage).toFixed(1) }}%
			</p>
		</template>
		<UptimeBars :days="service.days" :declared-since="service.declared_since" />
		<p class="muted">
			UTC days<template v-if="!service.declared_since">
				· Maintenance is excluded from eligible observations.</template
			>
		</p>
	</div>
</template>
<script setup>
import { formatTime } from "../api.js";
import StatusBadge from "./StatusBadge.vue";
import UptimeBars from "./UptimeBars.vue";
defineProps({ service: { type: Object, required: true } });
</script>
