# Services and SLOs

Beacon follows the Google SRE book and workbook for SLOs and burn-rate alerts.

## SLI, SLO and error budget

A service is one line on the status page, such as Sites. It can be regional or global. Each service has an SLI query, an SLO target and an optional SLA target.

| Term | Meaning | Example |
| --- | --- | --- |
| SLI | Good events divided by valid events. | 49,600 sites up of 50,000 valid sites. |
| SLO | The SLI target over 30 days. The default is 99.9%. | 99.9% |
| Error budget | The share of valid events that the SLO lets fail. | 0.1% |
| SLA | A promise to customers. It must be equal to or lower than the SLO. Beacon reports on it. | 99.5% |

Count only failures that the platform causes. For example, a site that fails because of its own app code is the problem of its owner, and its own layer tells the owner. If it counted as down, a few broken customer apps would use the whole budget of the region.

## One measurement

Each minute, the region run measures each service:

1. Run the SLI query. It returns one row for each reporter, such as a VM, seen in the last 30 minutes: `resource_id`, `good`, `valid`, `last_ts`.
2. Drop the rows of resources that are not checked, and of resources in a Public maintenance that was published before its start.
3. A reporter with no sample for 3 minutes counts its last valid events as bad only when it, or a parent of it, has a Firing cause alert, such as "Host down". Else Beacon leaves it out. So a dead host counts its VMs and sites as down, but a Pilot restart or a Datum gap never looks like an outage.
4. Compute the status.
5. Store good, valid, the count of bad reporters and the status in a Service Minute (kept 3 days). Add good and valid to the Service Day, and count the minute as a degraded or outage minute of the day when the status is Degraded or an outage.
6. Compute the burn alerts.

When the query fails or returns no valid events, Beacon adds no Service Minute or Service Day totals. It sets the measured status to Unknown, unless a matching Public maintenance sets it to Maintenance.

## Burn-rate alerts

The burn rate is how fast the service uses its budget: (1 - good / valid) / (1 - SLO). A burn rate of 1 uses the whole 30-day budget in 30 days.

Each burn alert has a long window and a short window. It fires when the burn rate meets or exceeds its limit in both windows. The long window shows that the problem is real. The short window makes the alert resolve a few minutes after the fix.

| Alert key | Budget used | Long window | Short window | Burn rate | Severity |
| --- | --- | --- | --- | --- | --- |
| `fast` | 2% | 1 hour | 5 minutes | 14.4 | High, pages |
| `medium` | 5% | 6 hours | 30 minutes | 6 | High, pages |
| `slow` | 10% | 3 days | 6 hours | 1 | Low |
| `sla` | Not applicable | 1 hour | 15 minutes | The SLA error rate | High, pages |

The `sla` alert exists only when the service has an SLA target. It fires when the error rate is above `100 - SLA target` percent in both windows.

These limits stop false pages:

- A window with fewer than 100 valid events does not alert.
- A window alerts only when Service Minutes cover at least half of it, so a new service does not page on its first bad minutes.
- A burn alert pages only when, in one minute of its short window, 3 or more reporters were bad, or every reporter when fewer than 3 reported. Else it is Low, because one bad reporter has its own cause alert.
- A service with "Page On Burn" off gets Medium instead of High.

A burn alert is a normal alert. Its rule is the "SLO burn" rule of the service, its resource is the region (or the global resource for a global service), and its alert key is the name in the table. It fires at once and resolves after 5 minutes without a burn.

The error budget for 30 days sums the Service Days. Until 30 days are measured, Beacon projects the valid events to 30 days.

## Service status

The status of a service in a region comes from the SLI of the last minute.

1. When a Public maintenance with this service is In Progress, the status is Maintenance.
2. Else, when the region is not OK or the service has no valid measurement, the status is Unknown.
3. Else the SLI sets it: 99.5% or more is Operational, 95% or more is Degraded, 80% or more is Partial Outage, and less is Major Outage.

A worse status must stay for 3 minutes before Beacon shows it. A better status must stay for 2 minutes.

The status page shows the worse of the measured status and the impact of open published incidents. Unknown shows as grey "Status unavailable", never as the last green state.

## Uptime

Uptime on the status page is the measured SLI: good divided by valid, for each day and for 90 days. Each day also shows its coverage, the share of the day's minutes that Beacon measured. A bar shows the worst status that the page showed on that day, with the impact of published incidents. So a bad minute that does not hold long enough to change the status lowers the uptime but does not change the color of the bar.

## The status page

The status page is a Vue app at `/` of the Beacon site. It reads only these guest methods: `get_status`, `get_history`, `get_incident` and `get_unlisted_incident`. They return only published data and cache each result for 60 seconds, so traffic during an outage does not reach ClickHouse. An action that changes published data clears the cache keys that it changes.
