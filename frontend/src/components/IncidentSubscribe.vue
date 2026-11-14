<template>
	<section class="card">
		<h2>Get updates</h2>
		<p v-if="sent" class="muted">
			We sent a link to {{ email }}. Open it to confirm the updates.
		</p>
		<form v-else class="subscribe" @submit.prevent="submit">
			<input
				v-model="email"
				type="email"
				required
				placeholder="you@example.com"
				aria-label="Email"
			/>
			<button type="submit" :disabled="busy">Email me</button>
		</form>
		<p v-if="error" class="error">{{ error }}</p>
		<p class="muted"><a :href="feed">RSS feed</a> of this incident</p>
	</section>
</template>

<script setup>
import { computed, ref } from "vue";
import { feedUrl, post } from "../api.js";

const props = defineProps({ name: String, token: String });
const target = computed(() => (props.token ? { token: props.token } : { name: props.name }));
const feed = computed(() => feedUrl(target.value));
const email = ref("");
const busy = ref(false);
const sent = ref(false);
const error = ref("");

async function submit() {
	busy.value = true;
	error.value = "";
	try {
		await post("subscribe", { ...target.value, email: email.value });
		sent.value = true;
	} catch (failure) {
		error.value = failure.message;
	} finally {
		busy.value = false;
	}
}
</script>
