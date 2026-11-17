<script setup>
import { useHost } from "@framework/ui/island";
import { Alert, Badge, Button, Skeleton, Tooltip, useCall } from "frappe-ui";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import LogViewer from "./LogViewer.vue";
import ResourceCharts from "./ResourceCharts.vue";
import { formatTime, methodUrl, pickerTime, routeWithWindow } from "./api";

const props = defineProps({
	name: { type: String, required: true },
	doc: { type: Object, default: () => ({}) },
});

const SEVERITY_THEME = { Critical: "red", High: "red", Medium: "amber", Low: "gray" };
const SEGMENTS = {
	pending: { label: "Pending", class: "bg-surface-amber-3" },
	firing: { label: "Firing", class: "bg-surface-red-5" },
	clean: { label: "Clean", class: "bg-surface-gray-4" },
};
const LANES_SHOWN = 6;

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
const openDocument = (doctype, name) =>
	host.navigate?.(`${doctype.toLowerCase().replaceAll(" ", "-")}/${name}`);

function duration(from, to) {
	const minutes = Math.max(1, Math.round((time(to) - time(from)) / 60000));
	if (minutes < 60) return `${minutes} min`;
	if (minutes < 1440) return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
	return `${Math.floor(minutes / 1440)} d ${Math.floor((minutes % 1440) / 60)} h`;
}

// The banner shows only when someone must act: Beacon is about to call, is calling, or nobody has acknowledged.
const banner = computed(() => {
	const doc = props.doc;
	if (doc.status === "Resolved") return null;
	if (doc.escalation === "Waiting")
		return {
			title: "Beacon calls the on-call team soon.",
			description: `At ${utc(doc.next_page_at)}, unless someone acknowledges.`,
		};
	if (doc.escalation === "Calling")
		return {
			title: "Beacon is calling the on-call team.",
			description: `Next call at ${utc(doc.next_page_at)}.`,
		};
	if (!doc.acknowledged_at) return { title: "Nobody has acknowledged this incident." };
	return null;
});
const firedAt = computed(() => cause.value.fired_at || cause.value.started_at);

// The lanes and charts show from 30 min before the first alert to the end, at most 7 days.
const span = computed(() => {
	const starts = (overview.value?.alerts || [])
		.map((alert) => time(alert.started_at))
		.filter(Boolean);
	const end = props.doc.resolved_at ? time(props.doc.resolved_at) + 15 * 60000 : Date.now();
	const start = Math.max(Math.min(...starts, end - 3600000) - 30 * 60000, end - 7 * 86400000);
	return { start, end };
});
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
// Metrics and the Log viewer open at the window of the incident.
const openWithWindow = (route) =>
	host.navigate?.(
		routeWithWindow(
			route,
			pickerTime(new Date(span.value.start)),
			pickerTime(new Date(span.value.end))
		)
	);
const resourceLinks = computed(() => {
	const resource = overview.value?.cause_resource;
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
// A Region shows no logs, so its section is empty without charts or a parent. Its links then join the cause.
const chartCount = ref(null);
const isResourceEmpty = computed(
	() =>
		cause.value?.resource_type === "Region" && chartCount.value === 0 && !overview.value.parent
);
const notifications = computed(() =>
	(overview.value?.notifications || []).filter((item) => item.sent_at)
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

	<div v-else class="flex flex-col pb-6 text-ink-gray-8">
		<Alert
			v-if="banner"
			class="mb-6"
			theme="amber"
			:title="banner.title"
			:description="banner.description"
		/>

		<section
			class="flex flex-col gap-4 border-t border-outline-gray-1 py-6 first-of-type:border-t-0 first-of-type:pt-0"
		>
			<div class="flex flex-wrap items-start gap-3">
				<div class="min-w-0 flex-1 basis-64">
					<h2 class="flex min-h-7 items-center text-lg-semibold text-ink-gray-9">
						{{ cause.rule_title || cause.title }}
					</h2>
					<p v-if="cause.rule_description" class="mt-1 text-p-sm text-ink-gray-5">
						{{ cause.rule_description }}
					</p>
				</div>
				<div class="flex flex-wrap gap-2">
					<Button
						v-if="cause.runbook_url"
						label="Runbook"
						icon-left="lucide-book-open"
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

			<dl class="flex flex-wrap gap-x-8 gap-y-4">
				<div class="min-w-0">
					<dt class="text-sm text-ink-gray-5">
						{{
							cause.resource_type === cause.resource_title
								? "Resource"
								: cause.resource_type
						}}
					</dt>
					<dd class="mt-1 flex h-5 items-center text-base-medium text-ink-gray-8">
						<span class="truncate">{{ cause.resource_title }}</span>
					</dd>
				</div>
				<div v-if="cause.resource_type !== 'Region'">
					<dt class="text-sm text-ink-gray-5">Region</dt>
					<dd class="mt-1 flex h-5 items-center text-base-medium text-ink-gray-8">
						{{ cause.region }}
					</dd>
				</div>
				<div>
					<dt class="text-sm text-ink-gray-5">Severity</dt>
					<dd class="mt-1 flex h-5 items-center">
						<Badge :theme="SEVERITY_THEME[cause.severity]" :label="cause.severity" />
					</dd>
				</div>
				<div>
					<dt class="text-sm text-ink-gray-5">Fired</dt>
					<dd
						class="mt-1 flex h-5 items-center text-base-medium text-ink-gray-8 tabular-nums"
					>
						{{ date(time(firedAt)) }}, {{ utc(firedAt) }}
					</dd>
				</div>
				<div v-if="doc.status === 'Resolved'">
					<dt class="text-sm text-ink-gray-5">Resolved</dt>
					<dd
						class="mt-1 flex h-5 items-center text-base-medium text-ink-gray-8 tabular-nums"
					>
						{{ utc(doc.resolved_at) }}, after {{ duration(firedAt, doc.resolved_at) }}
					</dd>
				</div>
				<div v-else-if="doc.acknowledged_at" class="min-w-0">
					<dt class="text-sm text-ink-gray-5">Acknowledged</dt>
					<dd
						class="mt-1 flex h-5 items-center text-base-medium text-ink-gray-8 tabular-nums"
					>
						<span class="truncate"
							>{{ doc.acknowledged_by }}, {{ utc(doc.acknowledged_at) }}</span
						>
					</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-sm text-ink-gray-5">Last 30 days</dt>
					<dd class="mt-1 flex h-5 items-center text-base-medium text-ink-gray-8">
						<span class="truncate">{{
							history
								? `${history.count} times on ${history.days} days, last ${date(
										time(history.last_started_at)
								  )}`
								: "First time"
						}}</span>
					</dd>
				</div>
			</dl>

			<p
				v-if="cause.detail"
				class="whitespace-pre-wrap break-words border-l-2 border-outline-gray-2 pl-3 font-mono text-sm text-ink-gray-7"
			>
				{{ cause.detail }}
			</p>

			<div v-if="overview.affected.length" class="divide-y divide-outline-gray-1">
				<div class="flex min-h-9 items-center gap-2 pb-1">
					<span class="flex-1 text-sm text-ink-gray-5"
						>{{
							cause.rule_kind === "SLO Burn"
								? "Failing now"
								: "Affected when it fired"
						}}, {{ overview.affected.length }}</span
					>
					<Button
						variant="ghost"
						:label="copied ? 'Copied' : 'Copy'"
						@click="copyAffected"
					/>
				</div>
				<div
					v-for="item in overview.affected"
					:key="item.key"
					class="grid min-h-9 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 py-1 md:grid-cols-[10rem_minmax(0,1fr)_auto]"
				>
					<span class="truncate text-base-medium text-ink-gray-8">{{ item.key }}</span>
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

			<div v-if="history?.incidents?.length" class="flex flex-wrap items-center gap-1">
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

		<section
			class="flex flex-col gap-4 border-t border-outline-gray-1 py-6 first-of-type:border-t-0 first-of-type:pt-0"
		>
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
				<h2 class="flex flex-1 items-baseline gap-2 whitespace-nowrap text-base-semibold">
					What happened
					<span
						v-if="overview.total > 1"
						class="text-base text-ink-gray-5 tabular-nums"
						>{{
							overview.firing
								? `${overview.firing} of ${overview.total} alerts firing`
								: `${overview.total} alerts, none firing`
						}}</span
					>
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
							notifications.length
						}}
						{{ notifications.length === 1 ? "notification" : "notifications" }}
					</span>
				</div>
			</div>

			<div class="flex flex-col gap-3">
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
						<span v-if="lane.count > 1" class="text-sm text-ink-gray-5"
							>{{ lane.count }} alerts</span
						>
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
				class="relative h-8 border-t border-outline-gray-1 text-sm tabular-nums text-ink-gray-5"
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
			class="flex flex-col gap-4 border-t border-outline-gray-1 py-6 first-of-type:border-t-0 first-of-type:pt-0"
		>
			<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
				<h2 class="flex-1 text-base-semibold">{{ cause.resource_title }}</h2>
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
					label="Metrics"
					@click="openWithWindow(`beacon-metrics/${overview.parent.name}`)"
				/>
				<Button
					variant="ghost"
					label="Log viewer"
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
