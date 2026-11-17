# Services and SLOs

Beacon follows the Google SRE book and workbook for SLOs and burn-rate alerts.

## SLI, SLO and error budget

A service is one line on the status page, such as Sites. It can be regional or global. A service is a Beacon Alert Rule of kind SLO Burn, so the rule of its burn alerts is the service definition itself. Each service has an SLI query, an SLO target and an optional SLA target.

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
5. Store good, valid, the count of bad reporters and the status in a Service Minute (kept 90 days). The stored status is the worse of the measured status and the impact of the open Public incidents on the service, so a declared outage also shows in the history. A declared outage does not change good and valid, so the uptime and the error budget stay measured. Beacon groups the minutes by UTC day for the history, the coverage and the per-status minute counts.
6. Compute the burn alerts.

When the query fails or returns no valid events, Beacon adds no Service Minute. It sets the measured status to Unknown, unless a matching Public maintenance sets it to Maintenance.

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
- A service with "Notify and Call on Burn" off gets Medium instead of High.

A burn alert is a normal alert. Its rule is the service, its title names the error budget problem, its resource is the region (or the global resource for a global service), and its alert key is the name in the table. It fires at once and resolves after 5 minutes without a burn. The customer title and description of the service stay separate from the responder problem text.

The error budget for 30 days aggregates the Service Minutes. Until 30 days are measured, Beacon projects the valid events to 30 days.

## Service status

The status of a service in a region comes from the SLI of the last minute.

1. When the worst impact of the open Public incidents and maintenances on this service is Maintenance, the status is Maintenance.
2. Else, when the region is not OK or the service has no valid measurement, the status is Unknown.
3. Else the SLI sets it: 99.5% or more is Operational, 95% or more is Degraded, the Major Outage Below value of the service (80% by default) or more is Partial Outage, and less is Major Outage.

A worse status must stay for 3 minutes before Beacon shows it. A better status must stay for 2 minutes.

The status page shows the worse of the measured status and the impact of open published incidents. The measurement and the status page load that impact through one loader, and only Public incidents count. Unknown shows as grey "Status unavailable", never as the last green state.

## Uptime

Uptime on the status page is the measured SLI: good divided by valid, for each UTC day and for 90 days, from indexed aggregates of the Service Minutes. Each day also shows its coverage, the share of the day's minutes that Beacon measured. The color of a bar comes from the minutes that the page showed each status on that day. The downtime of a day is the Major Outage minutes plus 0.3 times the Partial Outage minutes, as on Atlassian Statuspage. A day with 15 or more minutes of downtime is red, and a day with less downtime is orange. A day with no downtime and with Degraded minutes is yellow. Else the day is green, or blue when it had Maintenance minutes. So a bad minute that does not hold long enough to change the status lowers the uptime but does not change the color of the bar.

## Declared services and groups

A service with `measurement: Declared` has no SLI, and Beacon does not measure it. Its burn alerts resolve. Its status is the impact of the open published incidents on it, else Operational. Its bars show the minutes of published impact on each day since Beacon added it, with the same colors. When published incidents overlap, each minute counts once at its worst impact. The page says "Set by incidents, not measured" in place of the uptime. Use a declared service for a part that customers see but that has no metric yet.

Services with the same `status_page_group` show under one heading with that name. The group takes the place of its first service in the sort order.

## The status page

The status page is a Vue app at `/` of the Beacon site. It reads only these guest methods: `get_status`, `get_history` and `get_incident`, which takes the public ID of the incident link. They return only published data and cache each result for 60 seconds, so traffic during an outage does not reach ClickHouse. An action that changes published data clears the cache keys that it changes.
