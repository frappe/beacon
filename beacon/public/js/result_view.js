const escape = (value) => frappe.utils.escape_html(String(value ?? ""));

function label(key) {
	return escape(
		frappe
			.unscrub(key)
			.replace(/\bId\b/g, "ID")
			.replace(/ Ms$/, " (ms)")
	);
}

function value(item) {
	if (item === null || item === undefined || item === "")
		return `<span class="text-muted">–</span>`;
	if (typeof item === "boolean") return item ? __("Yes") : __("No");
	return escape(item).replace(/\n/g, "<br>");
}

function show(title, body) {
	frappe.msgprint({
		title,
		message: `<div class="beacon-result">${body}</div>`,
		indicator: "green",
		wide: true,
	});
}

function empty() {
	return `<p class="beacon-empty">${__("Nothing to show.")}</p>`;
}

function fields(record, skip = []) {
	return `<dl class="beacon-fields">${Object.entries(record)
		.filter(
			([key, item]) => !skip.includes(key) && (item === null || typeof item !== "object")
		)
		.map(([key, item]) => `<div><dt>${label(key)}</dt><dd>${value(item)}</dd></div>`)
		.join("")}</dl>`;
}

function table(rows) {
	const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
	return `<div class="beacon-table"><table><thead><tr>${columns
		.map((key) => `<th>${label(key)}</th>`)
		.join("")}</tr></thead><tbody>${rows
		.map((row) => `<tr>${columns.map((key) => `<td>${value(row[key])}</td>`).join("")}</tr>`)
		.join("")}</tbody></table></div>`;
}

export function showFields(title, record) {
	show(title, fields(record));
}

export function showRows(title, rows) {
	show(title, rows.length ? table(rows) : empty());
}

export function showTest(title, results) {
	show(
		title,
		results
			.map((result) => {
				const body = result.error
					? `<p class="text-danger">${escape(result.error)}</p>`
					: fields(result, ["region", "sample"]);
				const sample = result.sample?.length ? table(result.sample) : "";
				return `<div class="beacon-result-card"><h6>${escape(
					result.region
				)}</h6>${body}${sample}</div>`;
			})
			.join("") || empty()
	);
}
