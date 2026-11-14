# Beacon facts and accepted risks

## Facts

- Beacon is one global Frappe site. Each region has its own Datum ClickHouse.
- Atlas checks Metal nodes, the Metal daemon checks hosts and VMs, and Pilot checks sites. Beacon works one level up: it reads what they report and opens incidents.
- Datum stores `ts` as `DateTime64(3, 'UTC')`. Beacon runs in UTC, so ClickHouse times compare directly with `now_datetime()`.
- In a parameterized ClickHouse view, a select alias that has the name of a column also replaces that column in `WHERE`. `latest_value` therefore returns `last_ts`, not `ts`.
- Central issues and renews Pilot's Datum token. Central serves the map of server IDs to VM IDs and regions. Atlas does not store it.
- A new or reactivated resource is not evaluated for 10 minutes.
- A `Document` subclass must not define a method that has the name of a `Document` method, such as `extend`. Frappe calls those methods when it loads child tables.

## Accepted risks

- The status page is served by Beacon. When Beacon is down, the status page is down.
- A service status on the status page comes from its SLI and does not wait for a published incident. A published incident can only make it worse.
- A new service cannot page on a fast burn for 30 minutes, because burn windows need half coverage.
