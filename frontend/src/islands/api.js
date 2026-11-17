import { mountVueIsland } from "@framework/ui/island";
import { setConfig } from "frappe-ui";

// Beacon shows every time in UTC, so the date pickers read and write UTC.
setConfig("systemTimezone", "UTC");
setConfig("localTimezone", "UTC");

export const mountComponent = (component) => (el, context) =>
	mountVueIsland(el, { ...context, component });

/** The URL of a whitelisted document method, for `useCall`. */
export const methodUrl = (doctype, name, method) =>
	`/api/v2/document/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}/method/${method}`;

/** A UTC time as a date picker value, such as "2026-11-17 13:00:00". */
export const pickerTime = (date) => date.toISOString().slice(0, 19).replace("T", " ");

/** A Desk route that opens Metrics or the Log viewer at the given window, as picker values. */
export const routeWithWindow = (route, from, to) =>
	`${route}?${new URLSearchParams({ from, to })}`;

/** The picker values of the window that ends now, the last 6 hours by default. */
export function lastHours(hours = 6) {
	const now = new Date();
	return { from: pickerTime(new Date(now - hours * 3600000)), to: pickerTime(now) };
}

/** A UTC time without its offset. Charts read it as local time, so their axes print UTC. */
export const utcClock = (value) =>
	value &&
	(/[Z+]/.test(value.slice(10)) ? new Date(value).toISOString() : value.replace(" ", "T")).slice(
		0,
		23
	);

export const formatTime = (value) => (value ? new Date(value).toISOString().slice(11, 16) : "");

const SCALES = [
	[1e12, "T"],
	[1e9, "G"],
	[1e6, "M"],
	[1e3, "k"],
];

/** Prints a chart value with its unit. Bytes use binary steps. */
export function formatValue(value, unit = "") {
	if (value == null) return "";
	if (unit.startsWith("bytes")) {
		const steps = ["B", "KiB", "MiB", "GiB", "TiB"];
		let index = 0;
		while (Math.abs(value) >= 1024 && index < steps.length - 1)
			[value, index] = [value / 1024, index + 1];
		return `${round(value)} ${steps[index]}${unit.slice(5)}`;
	}
	const [size, suffix] = SCALES.find(([size]) => Math.abs(value) >= size) || [1, ""];
	const space = unit && !["%", "°C"].includes(unit) ? " " : "";
	return `${round(value / size)}${suffix}${space}${unit}`;
}

/** A NumberCard for a chart: the series that is highest now, with its trend. */
export function chartSummary(chart, data) {
	const series = data?.series || [];
	const last = (item) => item.points.at(-1)?.[1] ?? -Infinity;
	const top = series.reduce(
		(best, item) => (best && last(best) >= last(item) ? best : item),
		null
	);
	return {
		title: top && series.length > 1 ? `${chart.title}, ${top.name}` : chart.title,
		value: top ? last(top) : null,
		format: (value) => formatValue(value, chart.unit || ""),
		sparkline: top ? { data: top.points.map(([, value]) => value), type: "line" } : undefined,
	};
}

const round = (value) => (Math.abs(value) >= 10 ? Math.round(value) : Math.round(value * 10) / 10);
