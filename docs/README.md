# Beacon design

These pages describe what Beacon does and why. [SPEC.md](../SPEC.md) describes where the code is.

Read them in this order:

| Page | Read it to learn |
| --- | --- |
| [Architecture](architecture.md) | The parts of Beacon, who watches what, and the main decisions. |
| [Alerts](alerts.md) | How a rule finds a problem, and how an alert changes state. |
| [Incidents](incidents.md) | How alerts become incidents, how maintenance and silences mute them, and what customers see. |
| [Paging](paging.md) | Who Beacon calls, when, and how a responder acknowledges. |
| [Services and SLOs](services.md) | How Beacon measures a service, uses the error budget and sets the status page. |
| [Integration](integration.md) | What a service team adds so that Beacon watches its service. |

## Terms

| Term | Meaning |
| --- | --- |
| Region | One Frappe Cloud region. It has its own Datum and ClickHouse. |
| Resource | One thing that Beacon watches in a region: a host, a VM (Server), a service node, or the region itself. |
| Rule | A check for a problem, such as a host that is down. It uses SQL or HTTP. |
| Alert | One problem on one resource, from its start to its end. |
| Incident | The record that people work on. Customers can see it after a person publishes it. |
| Service | One line on the status page, such as Sites in one region. It has an SLI and an SLO. |
| SLI | The share of good events: good events divided by valid events. |
| SLO | The SLI target for 30 days, such as 99.9%. |
| Error budget | The share of valid events that the SLO lets fail. For 99.9%, it is 0.1%. |
| Silence | A mute of paging for a known problem, for a set time. |
| Maintenance | Planned work with a start and an end. It mutes alerts in its scope. |

All times are UTC.
