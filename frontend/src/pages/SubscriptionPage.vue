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
		<template v-else>
			<h2>
				{{
					action === "unsubscribe"
						? "Stop updates for this incident?"
						: "Get updates for this incident?"
				}}
			</h2>
			<!-- A link scanner opens the page but does not click, so nothing changes until a person does. -->
			<button class="action" :disabled="saving" @click="save">
				{{ action === "unsubscribe" ? "Stop updates" : "Confirm updates" }}
			</button>
		</template>
	</section>
</template>

<script setup>
import { ref } from "vue";
import { useRoute } from "vue-router";
import { post } from "../api.js";

const props = defineProps({ token: String });
const action = useRoute().query.action === "unsubscribe" ? "unsubscribe" : "confirm";
const incidentUrl = ref("");
const error = ref("");
const saving = ref(false);

async function save() {
	saving.value = true;
	try {
		incidentUrl.value = await post("change_subscription", { token: props.token, action });
	} catch (failure) {
		error.value = failure.message;
	} finally {
		saving.value = false;
	}
}
</script>
