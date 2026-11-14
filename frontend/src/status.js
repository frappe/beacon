export const SUMMARY = {
	Operational: "All systems operational",
	Maintenance: "Maintenance in progress",
	Degraded: "Some systems are degraded",
	"Partial Outage": "Partial outage",
	"Major Outage": "Major outage",
	Unknown: "Status unknown",
};

export function statusClass(status) {
	return `status-${(status || "Unknown").toLowerCase().replace(" ", "-")}`;
}
