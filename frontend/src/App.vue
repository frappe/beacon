<template>
	<div class="page">
		<header class="header">
			<router-link to="/" class="brand">{{ title }}</router-link>
			<router-link to="/history">History</router-link>
		</header>
		<router-view />
	</div>
</template>

<script setup>
import { ref, watch } from "vue";
import { useCall } from "./api.js";

const title = ref("Status");
const { data } = useCall("get_status");
watch(data, (value) => {
	if (value?.title) title.value = value.title;
});
watch(title, (value) => (document.title = value));
</script>
