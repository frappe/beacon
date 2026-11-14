<template>
	<span class="badge" :class="statusClass(status)">{{ status }}</span>
</template>

<script setup>
import { statusClass } from "../status.js";

defineProps({ status: { type: String, required: true } });
</script>
