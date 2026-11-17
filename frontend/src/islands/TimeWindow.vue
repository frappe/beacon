<script setup>
import { Button, DateTimePicker } from "frappe-ui";
import RefreshIcon from "~icons/lucide/refresh-cw";
import { pickerTime } from "./api";

// UTC From and To pickers. Refresh keeps the length of the window and moves it to end now.
const from = defineModel("from", { type: String, required: true });
const to = defineModel("to", { type: String, required: true });
defineProps({ loading: { type: Boolean, default: false } });

function refresh() {
	const length = new Date(`${to.value}Z`) - new Date(`${from.value}Z`);
	const end = new Date();
	[from.value, to.value] = [pickerTime(new Date(end - length)), pickerTime(end)];
}
</script>

<template>
	<DateTimePicker
		v-model="from"
		class="w-48 max-sm:min-w-36 max-sm:flex-1"
		placeholder="From"
		format="D MMM, HH:mm [UTC]"
	/>
	<DateTimePicker
		v-model="to"
		class="w-48 max-sm:min-w-36 max-sm:flex-1"
		placeholder="To"
		format="D MMM, HH:mm [UTC]"
	/>
	<Button
		:icon="RefreshIcon"
		tooltip="Refresh"
		aria-label="Refresh"
		:loading="loading"
		@click="refresh"
	/>
</template>
