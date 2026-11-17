<script setup>
import { Combobox, debounce, useCall } from "frappe-ui";
import { computed, ref } from "vue";

// A Link field for Beacon Resource, built as frappe-ui's server search Combobox.
// It searches with GET, because Desk gives an island no CSRF token for a POST.
const props = defineProps({
	modelValue: { type: String, default: null },
	region: { type: String, default: null },
	title: { type: String, default: null },
	placeholder: { type: String, default: "Choose a resource" },
	clearable: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue"]);

// `text` is the search box. `searched` follows it after a pause and drives the request.
const text = ref("");
const searched = ref("");
const search = useCall({
	url: "/api/v2/method/frappe.desk.search.search_link",
	params: () => ({
		doctype: "Beacon Resource",
		txt: searched.value,
		filters: JSON.stringify({ region: props.region || undefined }),
		page_length: 20,
	}),
	refetch: true,
});
const setSearched = debounce((value) => (searched.value = value), 250);

// A bound query is ours to clear when the list opens, or the chosen label stays in the search box.
function onOpen(open) {
	if (open) text.value = searched.value = "";
}

// The chosen resource keeps its title when the search results leave it out.
const options = computed(() => {
	const rows = (search.data || []).map((row) => ({
		label: row.label || row.value,
		value: row.value,
		description: row.description,
	}));
	const known = rows.some((row) => row.value === props.modelValue);
	return props.modelValue && props.title && !known
		? [{ label: props.title, value: props.modelValue }, ...rows]
		: rows;
});
</script>

<template>
	<Combobox
		v-model:query="text"
		class="w-64 max-w-full"
		trigger="button"
		:model-value="modelValue"
		:options="options"
		:filterable="false"
		:loading="search.loading"
		:placeholder="placeholder"
		@update:query="setSearched"
		@update:open="onOpen"
		@update:model-value="emit('update:modelValue', $event)"
	>
		<template v-if="clearable && modelValue" #suffix>
			<button
				type="button"
				aria-label="Clear"
				class="grid size-4 place-items-center rounded-1 text-ink-gray-5 hover:bg-surface-gray-3 hover:text-ink-gray-7"
				@click.stop="emit('update:modelValue', null)"
				@pointerdown.stop
			>
				<span class="lucide-x size-4" />
			</button>
		</template>
	</Combobox>
</template>
