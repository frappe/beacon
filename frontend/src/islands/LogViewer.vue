<script setup>
import {
	Alert,
	Badge,
	Button,
	Select,
	Switch,
	TabButtons,
	TextInput,
	debounce,
	useCall,
} from "frappe-ui";
import { BarChart } from "frappe-ui/charts";
import { List, ListCell, ListHeader, ListHeaderCell, ListRow, ListRows } from "frappe-ui/list";
import { useHost } from "@framework/ui/island";
import { computed, ref, watch } from "vue";
import { formatTime, lastHours, methodUrl, utcClock } from "./api";
import TimeWindow from "./TimeWindow.vue";

// `start` and `end` fix the window, as in the incident view. Without them the reader picks the window, from `initialFrom` and `initialTo` or the last 6 hours.
// Without `resource`, the viewer shows every resource of the region.
const props = defineProps({
	region: { type: String, required: true },
	resource: { type: String, default: null },
	start: { type: String, default: null },
	end: { type: String, default: null },
	initialFrom: { type: String, default: null },
	initialTo: { type: String, default: null },
	emptyText: { type: String, default: "No log lines in this window." },
	initialMode: { type: String, default: "lines" },
});
const host = useHost();

const LEVELS = [
	{
		value: "emergency",
		label: "Emergency",
		text: "text-ink-red-7",
		color: "var(--surface-red-7)",
	},
	{ value: "alert", label: "Alert", text: "text-ink-red-7", color: "var(--surface-red-7)" },
	{
		value: "critical",
		label: "Critical",
		text: "text-ink-red-7",
		color: "var(--surface-red-6)",
	},
	{ value: "error", label: "Error", text: "text-ink-red-5", color: "var(--surface-red-5)" },
	{
		value: "warning",
		label: "Warning",
		text: "text-ink-amber-7",
		color: "var(--surface-amber-5)",
	},
	{ value: "notice", label: "Notice", text: "text-ink-gray-6", color: "var(--surface-gray-6)" },
	{ value: "info", label: "Info", text: "text-ink-gray-4", color: "var(--surface-gray-4)" },
	{ value: "debug", label: "Debug", text: "text-ink-gray-4", color: "var(--surface-gray-3)" },
];
const levelOf = (value) => LEVELS.find((level) => level.value === value);
const MODES = [
	{ label: "Each line", value: "lines" },
	{ label: "Grouped", value: "groups" },
];

const search = ref("");
const searched = ref("");
const levels = ref([]);
const service = ref("");
const mode = ref(props.initialMode);
const wrap = ref(false);
const opened = ref(null);
const older = ref(null);
const lines = ref([]);
const from = ref(props.initialFrom || lastHours().from);
const to = ref(props.initialTo || lastHours().to);
watch(
	() => [props.initialFrom, props.initialTo],
	([start, end]) => start && end && ((from.value = start), (to.value = end))
);

const timeWindow = computed(() =>
	props.start ? { start: props.start, end: props.end } : { start: from.value, end: to.value }
);
// "Load older lines" keeps its cursor only while the filters stay the same.
const filters = computed(() =>
	JSON.stringify([
		props.region,
		props.resource,
		timeWindow.value,
		levels.value,
		service.value,
		searched.value,
		mode.value,
	])
);
const logs = useCall({
	url: computed(() => methodUrl("Beacon Region", props.region, "get_logs")),
	params: () => ({
		...timeWindow.value,
		resource: props.resource,
		levels: levels.value.join(","),
		service: service.value,
		search: searched.value,
		mode: mode.value,
		before: older.value?.filters === filters.value ? older.value.before : null,
	}),
	refetch: true,
	onSuccess: (data) =>
		(lines.value = logs.params.before ? [...lines.value, ...data.lines] : data.lines || []),
});
const result = computed(() => logs.data);
// A window with no lines at all needs no filters, only the message.
const isEmpty = computed(
	() =>
		Boolean(result.value) && !Object.keys(result.value.levels || {}).length && !searched.value
);
// The previous mode's response stays until the new one arrives, and it has no groups.
const groups = computed(() => result.value?.groups || []);
// The chart orders its series as the rows first name them, so severity order keeps the legend in step with the level filters.
const histogram = computed(() =>
	[...(result.value?.histogram || [])]
		.sort((a, b) => LEVELS.indexOf(levelOf(a.level)) - LEVELS.indexOf(levelOf(b.level)))
		.map((row) => ({ ...row, ts: utcClock(row.ts) }))
);

watch(
	search,
	debounce((value) => (searched.value = value.trim()), 400)
);
// An opened line is an index, so new lines would open another line.
watch(filters, () => (opened.value = null));

const serviceOptions = computed(() => [
	{ label: "All services", value: "" },
	...(result.value?.services || []).map((name) => ({ label: name, value: name })),
]);
const searchParts = (text) =>
	searched.value
		? text.split(new RegExp(`(${searched.value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "i"))
		: [text];

const detailsOf = (line) =>
	[
		["resource", props.resource ? null : line.resource_title],
		["service", line.service],
		["source", line.source],
		...Object.entries(line.attributes || {}),
		["message", line.message],
	].filter(([, value]) => value);

function toggleLevel(value) {
	levels.value = levels.value.includes(value)
		? levels.value.filter((item) => item !== value)
		: [...levels.value, value];
}
</script>

<template>
	<div class="flex min-w-0 flex-col gap-3 text-ink-gray-8">
		<div v-if="!(start && isEmpty)" class="flex flex-wrap items-center gap-2">
			<slot name="filters" />
			<TextInput
				v-model="search"
				class="min-w-0 flex-1 basis-48 max-sm:basis-full"
				type="search"
				placeholder="Search messages and attributes"
			/>
			<TimeWindow
				v-if="!start"
				v-model:from="from"
				v-model:to="to"
				:loading="logs.loading"
			/>
		</div>

		<div v-if="!isEmpty" class="flex flex-wrap items-center gap-1">
			<Button
				v-for="level in LEVELS.filter((level) => result?.levels?.[level.value])"
				:key="level.value"
				:variant="levels.includes(level.value) ? 'subtle' : 'ghost'"
				:label="level.label"
				@click="toggleLevel(level.value)"
			>
				<template #prefix
					><span class="size-2 rounded-full" :style="{ background: level.color }"
				/></template>
				<template #suffix
					><span class="tabular-nums text-ink-gray-5">{{
						result.levels[level.value].toLocaleString()
					}}</span></template
				>
			</Button>
			<Select
				v-if="serviceOptions.length > 2"
				v-model="service"
				class="ml-1 w-40"
				:options="serviceOptions"
			/>
			<span class="flex-1" />
			<TabButtons v-model="mode" :options="MODES" />
			<Switch v-if="mode === 'lines'" v-model="wrap" class="ml-1" label="Wrap" />
		</div>

		<Alert
			v-if="logs.error"
			theme="red"
			title="The logs did not load."
			:description="logs.error.message"
			:primary-action="{ label: 'Try again', onClick: () => logs.reload() }"
		/>

		<p v-else-if="isEmpty" class="text-p-sm text-ink-gray-5">{{ emptyText }}</p>

		<template v-else-if="result">
			<div v-if="result.histogram.length" class="h-32">
				<BarChart
					:data="histogram"
					x="ts"
					y="count"
					split-by="level"
					stacked
					:x-axis="{ type: 'time' }"
					:series-config="
						Object.fromEntries(
							LEVELS.map((level) => [
								level.value,
								{ label: level.label, color: level.color },
							])
						)
					"
				/>
			</div>

			<div class="max-h-[70vh] overflow-auto">
				<List
					v-if="mode === 'groups' && groups.length"
					class="list-row-px-3"
					:columns="{
						base: ['56px', 'minmax(0,1fr)'],
						md: ['64px', 'minmax(0,1fr)', '128px'],
					}"
				>
					<ListHeader>
						<ListHeaderCell class="justify-end">Count</ListHeaderCell>
						<ListHeaderCell>Pattern</ListHeaderCell>
						<ListHeaderCell class="max-md:hidden">Last seen</ListHeaderCell>
					</ListHeader>
					<ListRows :items="groups" row-key="pattern" v-slot="{ item: group }">
						<ListRow class="py-1.5">
							<ListCell class="justify-between gap-1.5 tabular-nums">
								<span
									class="size-2 shrink-0 rounded-full"
									:style="{ background: levelOf(group.level).color }"
								/>{{ group.count.toLocaleString() }}
							</ListCell>
							<ListCell class="gap-1 font-mono text-xs" :title="group.sample">
								<span class="truncate">
									<template
										v-for="(part, index) in group.pattern.split(/(<id>|<n>)/)"
										:key="index"
									>
										<Badge
											v-if="part === '<id>' || part === '<n>'"
											theme="blue"
											:label="part.slice(1, -1)"
										/>
										<template v-else>{{ part }}</template>
									</template>
								</span>
							</ListCell>
							<ListCell class="text-ink-gray-5 max-md:hidden">{{
								formatTime(group.last_seen)
							}}</ListCell>
						</ListRow>
					</ListRows>
				</List>

				<List
					v-else-if="mode === 'lines' && lines.length"
					class="list-row-px-3"
					:columns="{
						base: ['64px', 'minmax(0,1fr)'],
						md: ['64px', '72px', '144px', 'minmax(0,1fr)'],
					}"
				>
					<ListHeader>
						<ListHeaderCell>Time</ListHeaderCell>
						<ListHeaderCell class="max-md:hidden">Level</ListHeaderCell>
						<ListHeaderCell class="max-md:hidden">{{
							resource ? "Service" : "Resource"
						}}</ListHeaderCell>
						<ListHeaderCell>Message</ListHeaderCell>
					</ListHeader>
					<ListRows
						:items="lines"
						:row-key="(line, index) => index"
						v-slot="{ item: line, index }"
					>
						<ListRow
							:class="['py-1.5', opened === index && 'bg-surface-gray-2']"
							@click="opened = opened === index ? null : index"
						>
							<ListCell class="tabular-nums text-ink-gray-5">{{
								utcClock(line.ts).slice(11, 19)
							}}</ListCell>
							<ListCell class="max-md:hidden" :class="levelOf(line.level).text">{{
								levelOf(line.level).label
							}}</ListCell>
							<ListCell class="text-ink-gray-6 max-md:hidden"
								><span class="truncate">{{
									resource ? line.service || line.source : line.resource_title
								}}</span></ListCell
							>
							<ListCell class="font-mono text-xs">
								<span
									:class="wrap ? 'whitespace-pre-wrap break-words' : 'truncate'"
								>
									<template
										v-for="(part, partIndex) in searchParts(line.message)"
										:key="partIndex"
									>
										<mark
											v-if="partIndex % 2"
											class="rounded-1 bg-surface-amber-2 text-inherit"
											>{{ part }}</mark
										>
										<template v-else>{{ part }}</template>
									</template>
								</span>
							</ListCell>
						</ListRow>
						<ListRow v-if="opened === index" class="bg-surface-gray-1 pb-3 pt-2">
							<ListCell class="col-span-full">
								<dl
									class="grid w-full grid-cols-[64px_minmax(0,1fr)] gap-x-2 gap-y-1 md:grid-cols-[296px_minmax(0,1fr)]"
								>
									<template v-for="[key, value] in detailsOf(line)" :key="key">
										<dt class="text-ink-gray-5 md:text-right">{{ key }}</dt>
										<dd
											class="flex min-w-0 items-center gap-2 font-mono text-xs"
										>
											<span class="min-w-0 whitespace-pre-wrap break-all">{{
												value
											}}</span>
											<Button
												v-if="key === 'resource' && line.resource"
												size="sm"
												label="Metrics"
												@click="
													host.navigate?.(
														`beacon-metrics/${line.resource}`
													)
												"
											/>
										</dd>
									</template>
								</dl>
							</ListCell>
						</ListRow>
					</ListRows>
				</List>
				<p v-else class="py-4 text-sm text-ink-gray-5">No log lines in this window.</p>
			</div>

			<div
				v-if="mode === 'lines' && lines.length"
				class="flex items-center gap-2 px-3 text-sm text-ink-gray-5"
			>
				<span>{{ lines.length }} lines, newest first</span>
				<Button
					v-if="result.next_before"
					class="ml-auto"
					label="Load older lines"
					:loading="logs.loading"
					@click="older = { filters, before: result.next_before }"
				/>
			</div>
		</template>
	</div>
</template>
