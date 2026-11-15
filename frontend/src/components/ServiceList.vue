<template>
	<template v-for="item in items" :key="item.service.service">
		<h3 v-if="item.heading" class="group">{{ item.heading }}</h3>
		<ServiceHealth :service="item.service" />
	</template>
</template>

<script setup>
import { computed } from "vue";
import ServiceHealth from "./ServiceHealth.vue";

const props = defineProps({ services: { type: Array, required: true } });

// A group shows under one heading, at the place of its first service.
const items = computed(() => {
	const shown = new Set();
	return props.services.flatMap((service) => {
		if (!service.group) return [{ service }];
		if (shown.has(service.group)) return [];
		shown.add(service.group);
		return props.services
			.filter((member) => member.group === service.group)
			.map((member, index) => ({ service: member, heading: index ? "" : service.group }));
	});
});
</script>
