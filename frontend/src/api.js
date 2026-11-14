import { onUnmounted, ref, toValue, watch } from "vue";

export async function call(method, params = {}) {
	const response = await fetch(
		`/api/method/beacon.api.status.${method}?${new URLSearchParams(params)}`,
		{ headers: { Accept: "application/json" } }
	);
	if (!response.ok)
		throw new Error(response.status === 404 ? "Not found" : "Service temporarily unavailable");
	return (await response.json()).message;
}

export async function post(method, params) {
	const response = await fetch(`/api/method/beacon.api.status.${method}`, {
		method: "POST",
		headers: {
			Accept: "application/json",
			"X-Frappe-CSRF-Token": window.frappe?.csrf_token || "",
		},
		body: new URLSearchParams(params),
	});
	if (response.status === 429) throw new Error("Too many requests. Try again later.");
	if (!response.ok)
		throw new Error(
			response.status === 404 ? "Not found" : "Something went wrong. Try again later."
		);
	return (await response.json()).message;
}

export function feedUrl(params) {
	return `/api/method/beacon.api.status.get_incident_feed?${new URLSearchParams(params)}`;
}

export function formatTime(value) {
	if (!value) return "Not available";
	const date = new Date(/[Zz]$|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
	return (
		new Intl.DateTimeFormat(undefined, {
			dateStyle: "medium",
			timeStyle: "short",
			timeZone: "UTC",
		}).format(date) + " UTC"
	);
}

export function useCall(method, params = () => ({}), refreshSeconds = 0) {
	const data = ref(null),
		error = ref("");
	let request = 0;
	async function load(reset = false) {
		const current = ++request;
		if (reset) {
			data.value = null;
			error.value = "";
		}
		try {
			const result = await call(toValue(method), params());
			if (current === request) {
				data.value = result;
				error.value = "";
			}
		} catch (failure) {
			if (current === request) error.value = failure.message;
		}
	}
	watch(
		() => JSON.stringify([toValue(method), params()]),
		() => load(true),
		{ immediate: true }
	);
	const timer = refreshSeconds ? setInterval(() => load(), refreshSeconds * 1000) : null;
	onUnmounted(() => {
		request++;
		clearInterval(timer);
	});
	return { data, error, load };
}
