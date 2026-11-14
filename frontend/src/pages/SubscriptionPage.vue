<template>
	<section class="card">
		<p v-if="error" class="error">
			{{ error === "Not found" ? "This link is not valid." : error }}
		</p>
		<p v-else-if="incidentUrl">
			{{
				action === "unsubscribe"
					? "You will not get more updates for this incident."
					: "You will get an email for each update."
			}}
			<a :href="incidentUrl">Open the incident</a>
		</p>
		<p v-else class="muted">Saving…</p>
	</section>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { post } from "../api.js";

const props = defineProps({ token: String });
const action = useRoute().query.action === "unsubscribe" ? "unsubscribe" : "confirm";
const incidentUrl = ref("");
const error = ref("");

onMounted(async () => {
	try {
		incidentUrl.value = await post("change_subscription", { token: props.token, action });
	} catch (failure) {
		error.value = failure.message;
	}
});
</script>
