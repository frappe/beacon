# Incidents

## What an incident is

An incident is the record that people work on. It holds the alerts of one cause, the updates, the paging state and the postmortem. It has a visibility:

| Visibility | Who can see it |
| --- | --- |
| Internal | Staff only. Each incident starts as Internal. |
| Unlisted | Staff, and each person who has its private link. The status page does not list it. |
| Public | Everyone on the status page. |

A person decides what customers see. Beacon never publishes an incident and never reopens a public one.

## The incident rule

After each rule evaluation and each region run, every Firing alert that is not suppressed belongs to an open incident. The coordinator holds one lock for each region while it applies this rule.

For each Firing alert without an incident, the coordinator tries these steps in order. It stops at the first step that finds an incident.

1. **Cover.** An open incident has a scope row that covers the resource of the alert, or a parent of it. The alert joins that incident.
2. **Pair.** A burn alert joins the newest open incident in its region that has a cause alert, else an incident that opened in the last 30 minutes. A cause alert joins an open incident that has only burn alerts, and adds its resource to the scope.
3. **Group.** An open incident has the group key of the alert (its rule and region), and it is Triggered or younger than 30 minutes. The alert joins it. An older, acknowledged incident does not take a second outage.
4. **Reopen.** An Internal incident that Beacon made, with the same group key, was resolved in the last 30 minutes. Beacon reopens it to Triggered and starts paging again.
5. **Create.** Beacon creates an Internal incident in Triggered. When the resource has no parent, paging starts at once. Else paging waits for the page delay (90 seconds), so an incident of the parent can take the alert first.

A join is never silent. The coordinator posts one chat message for each incident in each run, with the alerts that joined. When an alert raises the severity, Beacon announces it and pages again. When an alert joins an incident in Monitoring, the incident goes back to Investigating and pages again.

## Scope

A scope row names a region, and optionally a resource type and a resource, with or without its descendants. The incident starts with the scope of its first alert. When the rule sets `scope_includes_descendants`, the scope covers everything under the resource, such as all VMs of a host. Staff can add scope rows.

When a new incident covers alerts of another open, unacknowledged, Internal incident, the covered alerts move to the new incident. The other incident closes as Merged only when it has no alerts left. Staff can also merge an incident by hand.

Beacon does not store the affected resources. "Affected resources" computes them when a person asks: the scope rows, the resources of the alerts, and their children when the rule includes them.

Example: a host fails and a proxy fails in the same region.

```text
Incident 1                          Incident 2
scope: host h12, with descendants    scope: proxy node proxy-2
  covers vm-a, vm-b, ...             affected service: Networking
one update stream, one private link
```

## Incident states

| From | Event | To |
| --- | --- | --- |
| none | The coordinator creates it | Triggered |
| Triggered | The Acknowledge action, or a key press on the call | Investigating |
| Investigating, Identified, Monitoring | Post an update | The status of the update |
| Monitoring | An alert joins | Investigating, and paging starts again |
| Any open | All alerts recover, and it is Internal and made by Beacon | Resolved |
| Any open | All alerts recover, and it is Unlisted or Public | No change. Beacon posts to chat that a person must resolve it. |
| Any open | Resolve while unsuppressed alerts fire | Refused, unless the person silences those alerts for some hours |
| Any open | Merged into another incident | Merged |
| Resolved | A new alert of its group in the next 30 minutes, Internal and made by Beacon | Triggered |
| Resolved | Reopen by staff, in the next 7 days | Investigating, without paging |

## Publish

The Publish action sets the visibility, the affected services with their impact, and the first public message. Unlisted makes a private link with a random token. Support can send that link to the customers who are affected.

A public update shows on the status page. Visitors can follow one published incident by email or RSS. A new public update emails each confirmed subscriber.

## Postmortem

When an incident resolves, Beacon marks a postmortem as required for an incident that is public or Critical, that took more than 1 hour, or that a customer found first. It starts the postmortem from a template. Each day, from 3 to 30 days after the resolve, Beacon posts to chat for each required postmortem that is still empty.

## Maintenance

A maintenance is an incident of type Maintenance. It has a scheduled start, a scheduled end and a scope.

| From | Event | To |
| --- | --- | --- |
| none | Created | Scheduled |
| Scheduled | The scheduled start, or Start Now | In Progress |
| Scheduled | Cancel | Cancelled |
| In Progress | The scheduled end, or Complete | Completed |

While a maintenance is In Progress, and for 10 minutes after it ends, Beacon suppresses the alerts in its scope. The 10 minutes let rebooted hosts come back. Then alerts that still fire join incidents as usual. With "Mute SLO Burn", Beacon also suppresses the burn alerts of its regions.

A Public maintenance that is published before its start does not count against the SLO. An Internal maintenance counts, because customers felt it. You can extend the end of a maintenance until the end passes.

## Silences

A silence mutes paging for a known problem. It starts now and ends at a set time, at most 30 days later. It needs a reason. It matches a rule, an alert key, a resource or a scope, or a combination of them.

An alert under a silence keeps its state and stays visible. It does not join an incident and does not page. When the silence expires or a person revokes it, the alerts that still fire join incidents.

| Use | When | Alerts | SLO |
| --- | --- | --- | --- |
| Maintenance | Planned work with a start and an end | Suppressed in its scope | Not counted only when it is Public and published before its start |
| Silence | A known problem, for a set time | Suppressed | Counted |
| Monitoring disabled on a resource | A resource that we never want to check | Not evaluated | Its rows are dropped |
