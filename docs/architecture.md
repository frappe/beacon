# Architecture

## The problem

Frappe Cloud runs in many regions. Each region sends telemetry to Datum. Datum writes the data to ClickHouse. Beacon reads the data to find platform problems before a customer reports them. It then alerts the responder one time and includes the cause.

Without state, a scheduled query posts the same problem on each run, never says "resolved", and cannot be muted or acknowledged. Beacon keeps that state.

## The parts

Beacon is one Frappe app on one global site. It runs outside the regions that it watches.

| Part | Job |
| --- | --- |
| Datum and ClickHouse, one in each region | Store samples, logs and traces. Datum owns the schema. Beacon reads with a read-only user and writes nothing. |
| Resource sync | Each minute, reads the resource list of each Resource Source, such as the Atlas of a region, and writes the changed rows to MariaDB. |
| Rule evaluation | Runs each due rule query in each region on the `long` queue, and writes alert transitions. |
| Region run | Each minute, for each region: probes ClickHouse, measures the services, refreshes suppression and makes sure each firing alert has an incident. |
| Scheduler | Each minute: starts and ends maintenance, resolves disabled alerts, advances escalations and sends the heartbeat. |
| Notifiers | Phone calls (Twilio), email and a chat webhook. They send after the database commit, on the `short` queue. |
| Desk | Staff work incidents and change configuration. The Beacon workspace is the control panel. |
| Status page | A Vue app at `/`. It reads guest methods that return only published data and cache each result for 60 seconds. |

Beacon keeps all alert, incident and SLO state in its MariaDB.

## Who watches what

Each layer of the platform watches the layer below it and tells its owner when one item fails. Beacon reads the counts of each layer. It acts when a failure is too wide or too frequent for one owner, or when a watcher stops.

| Layer | Watched by | Beacon acts when |
| --- | --- | --- |
| Site | Pilot in the VM pings each site and counts the sites by state. | Many sites fail for platform reasons, so the Sites SLO burns. |
| VM | metald on the host compares the wanted and the observed state of each VM. | Many VMs on one host are unavailable, or the Servers SLO burns. |
| Host | Atlas syncs each host and reports the age of the last sync. | The sync is old and metald is silent: the host is down. |
| Service node, such as a proxy | Atlas probes the readiness URL of the node. | The node is not ready for 3 minutes. |
| Requests through the proxy | The proxy counts requests by result. | The Networking SLO burns, or the proxy makes too many errors. |
| Atlas, Datum and the region network | Beacon probes ClickHouse and reads the newest Atlas sample. | The probe fails, or Atlas is silent. |
| Beacon | An external heartbeat service. | The heartbeat stops. |

A failure is always visible one level up. A dead site shows in the Pilot counts. A dead VM shows in the metald counts. A dead host shows in Atlas, and its metald goes silent. A dead Atlas or Datum shows in the Beacon probe. So Beacon does not need a "not reporting" check for each site or VM.

One failing site or VM is not a Beacon alert. Its owner layer already tells the customer.

## Main decisions

1. **Page on symptoms, explain with causes.** Each customer-facing service has an SLI and an SLO. A fast burn of the error budget pages. A short list of cause rules names the broken host, node or watcher, and gives the incident its scope.
2. **SQL rules and SLI queries run in ClickHouse.** HTTP rules call a URL from the Beacon site. A SQL rule returns only failing rows. An SLI returns good and valid counts for each reporter. ClickHouse limits the time, memory and threads of each query. Beacon runs no Python from a rule.
3. **The registry holds infrastructure only.** Hosts, VMs and service nodes come from the Resource Sources, with their parents and status. Sites are counts, not rows. Beacon drops rule rows for unknown or unchecked resources, so a stopped or removed resource does not alert.
4. **One alert row for each problem, written only on transitions.** A firing alert causes no writes.
5. **The incident rule.** After each evaluation and each region run, every Firing alert that is not suppressed belongs to an open incident.
6. **One incident for one cause.** An incident has a few scope rows, such as "host h12 and everything under it". Beacon computes the affected resources when a person asks for them.
7. **A person decides what customers see.** Beacon never publishes an incident and never reopens a public one. The only automatic public signal is the service status.
8. **Fail loudly.** A query error never resolves an alert. A failed fetch never removes a resource. A region with broken telemetry gives one alert. If Beacon stops, the external heartbeat service calls the team.

## Example: one host fails

A host has 30 VMs and 400 sites.

1. The host loses power. Its metald and the Pilots in its VMs stop.
2. The Pilots are silent for 3 minutes. No cause alert covers them yet, so the Sites SLI leaves them out. A Pilot restart looks the same, and must not page.
3. The Atlas sync of the host is older than 5 minutes, and metald is silent. The "Host down" rule returns the host. The alert fires after its pending time.
4. Beacon opens an incident with the scope "this host and everything under it", and pages at once because the host has no parent.
5. Now a cause alert covers the silent reporters, so the SLIs count them as bad. A burn alert that fires later joins the same incident.
6. The responder presses 1 on the call. The incident moves to Investigating. The responder publishes it as Unlisted and sends the private link to support.
7. The host comes back. The cause alert recovers after its resolve time. The responder posts a final update and resolves the incident. The control panel shows how much error budget the outage used.
