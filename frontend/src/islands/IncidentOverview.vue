<script setup>
import { useHost } from "@framework/ui/island";
import { Alert, Badge, Button, Skeleton, Tooltip, useCall } from "frappe-ui";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import LogViewer from "./LogViewer.vue";
import ResourceCharts from "./ResourceCharts.vue";
import { formatTime, methodUrl, routeWithWindow } from "./api";

const props = defineProps({
	name: { type: String, required: true },
	doc: { type: Object, default: () => ({}) },
});

const SEGMENTS = {
	pending: { label: "Pending", class: "bg-surface-amber-3" },
	firing: { label: "Firing", class: "bg-surface-red-5" },
	clean: { label: "Clean", class: "bg-surface-gray-4" },
};
const LANES_SHOWN = 6;
const SEVERITY_THEME = { Critical: "red", High: "red", Medium: "amber", Low: "gray" };
const CARD = "rounded-6 border border-outline-gray-1 bg-surface-base p-4";

const host = useHost();
const result = useCall({
	url: computed(() => methodUrl("Beacon Incident", props.name, "get_overview")),
	refetch: true,
});
watch(
	() => props.doc.modified,
	() => result.reload()
);
// Alerts change without a save of the incident, so an open incident reads them again each minute.
const timer = setInterval(() => props.doc.status !== "Resolved" && result.reload(), 60000);
onBeforeUnmount(() => clearInterval(timer));

const overview = computed(() => result.data);
const cause = computed(() => overview.value?.cause);
const history = computed(() =>
	overview.value?.history.find(
		(item) => item.rule === cause.value?.rule && item.resource === cause.value?.resource
	)
);

const time = (value) => (value ? new Date(`${value.replace(" ", "T")}Z`).getTime() : null);
const iso = (milliseconds) => new Date(milliseconds).toISOString();
const date = (milliseconds) => new Date(milliseconds).toUTCString().slice(5, 11);
const utc = (value) => (value ? `${formatTime(iso(time(value)))} UTC` : "");
const plural = (count, word) => `${count} ${count === 1 ? word : `${word}s`}`;
const openDocument = (doctype, name) =>
	host.navigate?.(`${doctype.toLowerCase().replaceAll(" ", "-")}/${name}`);

function duration(from, to) {
	const minutes = Math.max(1, Math.round((time(to) - time(from)) / 60000));
	if (minutes < 60) return `${minutes} min`;
	if (minutes < 1440) return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
	return `${Math.floor(minutes / 1440)} d ${Math.floor((minutes % 1440) / 60)} h`;
}

const firedAt = computed(() => cause.value.fired_at || cause.value.started_at);
const resourceMeta = computed(() =>
	[
		cause.value.resource_type !== cause.value.resource_title && cause.value.resource_type,
		cause.value.resource_type !== "Region" && cause.value.region,
	]
		.filter(Boolean)
		.join(", ")
);
const historyText = computed(() => {
	if (!history.value) return "First time";
	const last = date(time(history.value.last_started_at));
	if (history.value.count === 1) return `Once, on ${last}`;
	return `${history.value.count} times on ${plural(history.value.days, "day")}, last ${last}`;
});

// The lanes, charts and logs show the incident window that get_overview gives.
const span = computed(() => ({
	start: time(overview.value.window.from),
	end: time(overview.value.window.to),
}));
const position = (value) => {
	const { start, end } = span.value;
	return `${(100 * (Math.min(Math.max(value, start), end) - start)) / (end - start)}%`;
};
const width = (from, to) => `calc(${position(to)} - ${position(from)})`;
const ticks = computed(() =>
	[0, 1 / 3, 2 / 3, 1].map(
		(part) => span.value.start + part * (span.value.end - span.value.start)
	)
);
const tickLabel = (tick) =>
	`${span.value.end - span.value.start > 86400000 ? `${date(tick)} ` : ""}${formatTime(
		iso(tick)
	)}`;

function segments(alert) {
	const started = time(alert.started_at),
		fired = time(alert.fired_at),
		clean = time(alert.clean_since);
	const end = time(alert.resolved_at) || span.value.end;
	return [
		{ kind: "pending", from: started, to: fired || end },
		fired && { kind: "firing", from: fired, to: clean || end },
		clean && { kind: "clean", from: clean, to: end },
	].filter((segment) => segment && segment.to > segment.from);
}
// One lane for each rule and resource, with every alert period of that pair on it.
const lanes = computed(() => {
	const byKey = {};
	for (const alert of overview.value?.alerts || []) {
		const lane = (byKey[`${alert.rule || alert.title}|${alert.resource}`] ||= {
			...alert,
			count: 0,
			segments: [],
		});
		lane.count += 1;
		lane.segments.push(...segments(alert));
		if (["Pending", "Firing"].includes(alert.status)) lane.status = alert.status;
	}
	return Object.values(byKey);
});
const showAllLanes = ref(false);
const shownLanes = computed(() =>
	showAllLanes.value ? lanes.value : lanes.value.slice(0, LANES_SHOWN)
);
const legend = computed(() => {
	const kinds = new Set(
		lanes.value.flatMap((lane) => lane.segments.map((segment) => segment.kind))
	);
	return Object.entries(SEGMENTS).filter(([kind]) => kinds.has(kind));
});
const notifications = computed(() =>
	(overview.value?.notifications || []).filter((item) => item.sent_at)
);

// Metrics and the Log viewer open at the window of the incident.
const openWithWindow = (route) =>
	host.navigate?.(routeWithWindow(route, overview.value.window.from, overview.value.window.to));
const resourceLinks = computed(() => {
	const resource = overview.value.cause_resource;
	if (!resource) return [];
	return [
		{ label: "Metrics", icon: "lucide-line-chart", route: `beacon-metrics/${resource}` },
		{
			label: "Log viewer",
			icon: "lucide-scroll-text",
			route: `beacon-logs/${cause.value.region}/${resource}`,
		},
		cause.value.url && { label: "Atlas", href: cause.value.url },
		overview.value.central_url && { label: "Central", href: overview.value.central_url },
	].filter(Boolean);
});
// A Region shows no logs, so its card is empty without charts or a parent. Its links then join the cause.
const chartCount = ref(null);
const isResourceEmpty = computed(
	() =>
		cause.value?.resource_type === "Region" && chartCount.value === 0 && !overview.value.parent
);

const copied = ref(false);
async function copyAffected() {
	const lines = overview.value.affected.map((item) =>
		[item.key, item.text].filter(Boolean).join(": ")
	);
	await navigator.clipboard.writeText(lines.join("\n"));
	copied.value = true;
	setTimeout(() => (copied.value = false), 2000);
}
</script>

<template>
	<Alert
		v-if="result.error"
		theme="red"
		title="The overview did not load."
		:description="result.error.message"
		:primary-action="{ label: 'Try again', onClick: () => result.reload() }"
	/>

	<div v-else-if="!overview" class="flex flex-col gap-4">
		<Skeleton class="h-40 rounded-6" />
		<Skeleton class="h-32 rounded-6" />
	</div>

	<p
		v-else-if="!cause"
		class="rounded-6 border border-outline-gray-1 px-3 py-10 text-center text-p-sm text-ink-gray-5"
	>
		This incident has no alerts. The Updates tab has what people wrote about it.
	</p>

	<div v-else class="flex flex-col gap-4 pb-4 text-ink-gray-8">
		<section :class="CARD">
			<div class="flex flex-wrap items-start gap-x-3 gap-y-2">
				<div class="min-w-0 flex-1 basis-72">
					<div class="flex min-h-7 items-center gap-2">
						<Badge
							size="lg"
							:theme="SEVERITY_THEME[cause.severity]"
							:label="cause.severity"
						/>
						<h2 class="truncate text-lg-semibold text-ink-gray-9">
							{{ cause.rule_title || cause.title }}
						</h2>
					</div>
					<p v-if="cause.rule_description" class="mt-1 text-p-sm text-ink-gray-5">
						{{ cause.rule_description }}
					</p>
				</div>
				<div class="flex flex-wrap gap-2">
					<Button
						v-if="cause.runbook_url"
						label="Runbook"
						icon-left="lucide-book-open"
						icon-right="lucide-arrow-up-right"
						:href="cause.runbook_url"
					/>
					<Button
						v-if="cause.rule"
						label="Rule"
						icon-left="lucide-list-checks"
						@click="openDocument('Beacon Alert Rule', cause.rule)"
					/>
					<template v-if="isResourceEmpty">
						<Button
							v-for="link in resourceLinks"
							:key="link.label"
							:label="link.label"
							:icon-left="link.icon"
							:icon-right="link.href && 'lucide-arrow-up-right'"
							:href="link.href"
							@click="link.route && openWithWindow(link.route)"
						/>
					</template>
				</div>
			</div>

			<dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 md:grid-cols-3">
				<div class="min-w-0">
					<dt class="text-sm text-ink-gray-5">Resource</dt>
					<dd class="mt-1 truncate text-base-medium text-ink-gray-9">
						{{ cause.resource_title }}
						<span class="text-base text-ink-gray-5">{{ resourceMeta }}</span>
					</dd>
				</div>
				<div>
					<dt class="text-sm text-ink-gray-5">Fired</dt>
					<dd class="mt-1 text-base-medium tabular-nums text-ink-gray-9">
						{{ date(time(firedAt)) }}, {{ utc(firedAt) }}
					</dd>
				</div>
				<div v-if="doc.status === 'Resolved'">
					<dt class="text-sm text-ink-gray-5">Resolved</dt>
					<dd class="mt-1 text-base-medium tabular-nums text-ink-gray-9">
						{{ utc(doc.resolved_at) }}, after {{ duration(firedAt, doc.resolved_at) }}
					</dd>
				</div>
				<div v-else-if="doc.acknowledged_at" class="min-w-0">
					<dt class="text-sm text-ink-gray-5">Acknowledged</dt>
					<dd class="mt-1 truncate text-base-medium tabular-nums text-ink-gray-9">
						{{ doc.acknowledged_by }}, {{ utc(doc.acknowledged_at) }}
					</dd>
				</div>
				<div v-if="overview.total > 1">
					<dt class="text-sm text-ink-gray-5">Alerts</dt>
					<dd class="mt-1 text-base-medium tabular-nums text-ink-gray-9">
						{{
							overview.firing
								? `${overview.firing} of ${overview.total} firing`
								: `None of ${overview.total} firing`
						}}
					</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-sm text-ink-gray-5">Last 30 days</dt>
					<dd class="mt-1 truncate text-base-medium text-ink-gray-9">
						{{ historyText }}
					</dd>
				</div>
			</dl>

			<p
				v-if="cause.detail"
				class="mt-4 whitespace-pre-wrap break-words rounded-4 bg-surface-gray-1 px-3 py-2 font-mono text-sm text-ink-gray-7"
			>
				{{ cause.detail }}
			</p>

			<div
				v-if="overview.affected.length"
				class="mt-3 rounded-4 border border-outline-gray-1"
			>
				<div
					class="flex min-h-9 items-center gap-2 border-b border-outline-gray-1 py-1 pl-3 pr-1 text-sm"
				>
					<span class="text-base-medium text-ink-gray-9">{{
						cause.rule_kind === "SLO Burn" ? "Failing now" : "Affected when it fired"
					}}</span>
					<span class="flex-1 tabular-nums text-ink-gray-5">{{
						overview.affected.length
					}}</span>
					<Button
						variant="ghost"
						icon-left="lucide-copy"
						:label="copied ? 'Copied' : 'Copy'"
						@click="copyAffected"
					/>
				</div>
				<div
					v-for="item in overview.affected"
					:key="item.key"
					class="grid min-h-9 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-outline-gray-1 py-0.5 pl-3 pr-1 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)_auto] [&+&]:border-t"
				>
					<span class="truncate text-base-medium text-ink-gray-9" :title="item.key">{{
						item.key
					}}</span>
					<span
						class="min-w-0 text-base text-ink-gray-6 max-md:col-span-2 max-md:row-start-2 max-md:pb-1 md:truncate"
						:title="item.text"
						>{{ item.text }}</span
					>
					<span class="flex">
						<Button
							v-if="item.resource"
							variant="ghost"
							icon="lucide-line-chart"
							tooltip="Metrics"
							@click="openWithWindow(`beacon-metrics/${item.resource}`)"
						/>
						<Button
							v-if="item.resource"
							variant="ghost"
							icon="lucide-scroll-text"
							tooltip="Log viewer"
							@click="openWithWindow(`beacon-logs/${cause.region}/${item.resource}`)"
						/>
						<Button
							v-if="item.url"
							variant="ghost"
							label="Atlas"
							icon-right="lucide-arrow-up-right"
							:href="item.url"
						/>
					</span>
				</div>
			</div>

			<div v-if="history?.incidents?.length" class="mt-3 flex flex-wrap items-center gap-1">
				<span class="mr-1 text-sm text-ink-gray-5">Earlier incidents</span>
				<Button
					v-for="incident in history.incidents"
					:key="incident.name"
					variant="ghost"
					size="sm"
					:label="incident.name"
					@click="openDocument('Beacon Incident', incident.name)"
				/>
			</div>
		</section>

		<section :class="CARD">
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
				<h2 class="flex-1 whitespace-nowrap text-base-semibold text-ink-gray-9">
					What happened
				</h2>
				<div class="flex flex-wrap gap-x-3 gap-y-1 text-sm text-ink-gray-5">
					<span
						v-for="[kind, segment] in legend"
						:key="kind"
						class="flex items-center gap-1.5"
					>
						<span class="size-2 rounded-1" :class="segment.class" />{{ segment.label }}
					</span>
					<span v-if="notifications.length" class="flex items-center gap-1.5">
						<span class="size-2 rounded-full bg-surface-blue-7" />{{
							plural(notifications.length, "notification")
						}}
					</span>
				</div>
			</div>

			<div class="mt-3 flex flex-col gap-3">
				<div v-for="lane in shownLanes" :key="lane.name">
					<div
						v-if="lanes.length > 1"
						class="flex min-w-0 flex-wrap items-center gap-x-1"
					>
						<Button
							variant="ghost"
							size="sm"
							class="-ml-2 min-w-0 max-w-full"
							:label="lane.title"
							:tooltip="lane.detail"
							@click="openDocument('Beacon Alert', lane.name)"
						/>
						<span v-if="lane.count > 1" class="text-sm text-ink-gray-5">{{
							plural(lane.count, "alert")
						}}</span>
					</div>
					<div
						class="relative mt-1 h-4 cursor-pointer rounded-3 bg-surface-gray-2"
						@click="openDocument('Beacon Alert', lane.name)"
					>
						<Tooltip
							v-for="(segment, index) in lane.segments"
							:key="index"
							:text="`${SEGMENTS[segment.kind].label} from ${formatTime(
								iso(segment.from)
							)} to ${formatTime(iso(segment.to))} UTC`"
						>
							<div
								class="absolute inset-y-0.5 rounded-1"
								:class="SEGMENTS[segment.kind].class"
								:style="{
									left: position(segment.from),
									width: width(segment.from, segment.to),
								}"
							/>
						</Tooltip>
					</div>
				</div>
				<Button
					v-if="lanes.length > LANES_SHOWN"
					class="-ml-2 self-start"
					variant="ghost"
					size="sm"
					:label="showAllLanes ? 'Show fewer' : `Show all ${lanes.length}`"
					@click="showAllLanes = !showAllLanes"
				/>
			</div>

			<div
				class="relative mt-3 h-8 border-t border-outline-gray-1 text-sm tabular-nums text-ink-gray-5"
			>
				<Tooltip
					v-for="(notification, index) in notifications"
					:key="index"
					:text="`${formatTime(iso(time(notification.sent_at)))} UTC, ${
						notification.channel
					} to ${notification.responder || 'chat'}: ${notification.status}`"
				>
					<span
						class="absolute top-0 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-[var(--surface-base)]"
						:class="
							notification.answered_at ? 'bg-surface-green-7' : 'bg-surface-blue-7'
						"
						:style="{ left: position(time(notification.sent_at)) }"
					/>
				</Tooltip>
				<span
					v-for="(tick, index) in ticks"
					:key="tick"
					class="absolute top-3 whitespace-nowrap"
					:class="[
						['', '-translate-x-1/2', '-translate-x-1/2', '-translate-x-full'][index],
						index % 3 && 'max-md:hidden',
					]"
					:style="{ left: position(tick) }"
					>{{ tickLabel(tick) }}{{ index === ticks.length - 1 ? " UTC" : "" }}</span
				>
			</div>
		</section>

		<section
			v-if="overview.cause_resource"
			v-show="!isResourceEmpty"
			class="flex flex-col gap-4"
			:class="CARD"
		>
			<div class="flex flex-wrap items-center gap-x-2 gap-y-2">
				<h2 class="min-w-0 flex-1 truncate text-base-semibold text-ink-gray-9">
					{{ cause.resource_title }}
					<span class="text-base font-normal text-ink-gray-5">{{ resourceMeta }}</span>
				</h2>
				<div class="flex flex-wrap gap-2">
					<Button
						v-for="link in resourceLinks"
						:key="link.label"
						:label="link.label"
						:icon-left="link.icon"
						:icon-right="link.href && 'lucide-arrow-up-right'"
						:href="link.href"
						@click="link.route && openWithWindow(link.route)"
					/>
				</div>
			</div>
			<ResourceCharts
				:name="overview.cause_resource"
				:start="iso(span.start)"
				:end="iso(span.end)"
				key-only
				:chart-keys="overview.chart_keys"
				@count="chartCount = $event"
			/>
			<div
				v-if="overview.parent"
				class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-4 bg-surface-gray-2 py-1 pl-3 pr-1 text-p-sm"
			>
				<span class="text-ink-gray-5">Runs on</span>
				<span class="text-base-medium text-ink-gray-9">{{
					overview.parent.title || overview.parent.resource_id
				}}</span>
				<span class="text-ink-gray-5">{{ overview.parent.resource_type }}</span>
				<Badge
					v-if="overview.parent.firing"
					theme="red"
					:label="`${overview.parent.firing} firing`"
				/>
				<span class="flex-1" />
				<Button
					variant="ghost"
					icon="lucide-line-chart"
					tooltip="Metrics"
					@click="openWithWindow(`beacon-metrics/${overview.parent.name}`)"
				/>
				<Button
					variant="ghost"
					icon="lucide-scroll-text"
					tooltip="Log viewer"
					@click="
						openWithWindow(
							`beacon-logs/${overview.parent.region}/${overview.parent.name}`
						)
					"
				/>
				<Button
					v-if="overview.parent.url"
					variant="ghost"
					label="Atlas"
					icon-right="lucide-arrow-up-right"
					:href="overview.parent.url"
				/>
			</div>
			<LogViewer
				v-if="cause.resource_type !== 'Region'"
				:region="cause.region"
				:resource="overview.cause_resource"
				:start="iso(span.start)"
				:end="iso(span.end)"
				initial-mode="groups"
				empty-text="No log lines while this incident was open."
			/>
		</section>
	</div>
</template>
