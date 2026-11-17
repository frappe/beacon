# Incidents

## What an incident is

An incident is the record that people work on. It holds the alerts of one cause, the updates, the paging state and the postmortem. It has a visibility:

| Visibility | Who can see it |
| --- | --- |
| Internal | Staff only. Each incident starts as Internal. |
| Unlisted | Staff, and each person who has its link. The status page does not list it. |
| Public | Everyone on the status page. |

A person decides what customers see. Beacon never publishes an incident and never reopens a public one.

## The incident rule

After each rule evaluation and each region run, every Firing alert that is not suppressed belongs to an open incident. The coordinator holds one lock for each region while it applies this rule.

For each Firing alert without an incident, the coordinator tries these steps in order. It stops at the first step that finds an incident.

1. **Cover.** An open incident has a scope row that covers the resource of the alert, or a parent of it. The alert joins that incident. An alert on the region resource is never covered, because all region-level rules report on it. It joins by pair or rule only.
2. **Pair.** Each minute, the service measurement keeps the resources that sent bad events (the bad reporters). A burn alert joins an open incident in its region that has a cause alert on a bad reporter of its service or on a parent of one. A cause alert on such a resource joins an open incident that has only burn alerts, and adds its resource to the scope. Alerts that are not related in this way do not pair.
3. **Rule.** An open incident in the region has an alert of the same rule, and it is Triggered or younger than 30 minutes. The alert joins it. An older, acknowledged incident does not take a second outage.
4. **Create.** Beacon creates an Internal incident in Triggered. When the resource has no parent, notifications start at once. Else notifications wait for the notification delay (90 seconds), so an incident of the parent can take the alert first.

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
one update stream, one link
```

## Incident states

| From | Event | To |
| --- | --- | --- |
| none | The coordinator creates it | Triggered |
| Triggered | The Acknowledge action, or an answered call | Investigating |
| Investigating, Identified, Monitoring | Post an update | The status of the update |
| Monitoring | An alert joins | Investigating, and paging starts again |
| Any open | All alerts recover, and it is Internal and made by Beacon | Resolved |
| Any open | All alerts recover, and a person made it or it is Unlisted or Public | No change. Beacon posts to chat one time that a person must resolve it. |
| Any open | Resolve while unsuppressed alerts fire | Refused, unless the person silences those alerts for some hours |
| Any open | Merged into another incident | Merged |
| Resolved | Reopen by staff, in the next 7 days | Investigating, without paging |

Only a move in this table can change the status. The status field is read-only in Desk, and the `STATUS` state machine of the incident refuses every other move. A new problem after a resolve opens a new incident. Beacon does not reopen an incident.

Each minute, the recovery sweep looks for open incidents whose alerts all recovered. Because it reads all open incidents, a missed run delays a close and never loses it.

## Overview tab

The Overview tab of the incident form shows what a responder needs before they open an alert. It only reads data. Actions stay in the form.

- The form intro shows only when someone must act: Beacon is about to call, Beacon is calling, or nobody has acknowledged the incident.
- The cause card names the worst open alert, else the oldest alert. It shows the severity, the problem title and description, the resource, the fire time, the acknowledge or resolve time, the count of firing alerts, the last 30 days of the same rule on the same resource, the alert detail and the affected list. Runbook and Rule open the runbook and the alert rule.
- What happened shows one lane for each rule and resource, with the pending, firing and clean periods, and a dot for each notification. It shows the first 6 lanes, and Show all shows the rest.
- The resource card shows the key charts of the cause resource and its logs while the incident was open, grouped by pattern. Metrics, Log viewer, Atlas and Central open the full views. Metrics and the Log viewer open at the incident window: from 30 min before the first alert to 15 min after the resolution or now, at most 7 days. A Region without charts has no resource card, and its links show in the cause card.

An open incident reads the overview again each minute, because alerts change without a save of the incident.

## Publish

The Publish action sets the visibility, the affected services with their impact, and the first public message. Publish gives the incident a link with a random public ID: `/incidents/<public_id>`. Public and Unlisted incidents use the same link, and only Public ones are listed. Nobody can guess the link or count the incidents from it. Support can send the link of an Unlisted incident to the customers who are affected. A change of visibility keeps the link. Regenerate Link makes a new one, and the old link stops working.

A public update shows on the status page. Visitors can follow one published incident by email or RSS. The confirm and stop links open a page with a button, so a link scanner changes nothing. A new public update emails each confirmed subscriber. Every update of one incident has the same subject, so mail clients keep them in one thread.

## Postmortem

When an incident resolves, Beacon marks a postmortem as required for an incident that is public or Critical, that is High and took more than 1 hour, or that a customer found first. It starts the postmortem from a template. Beacon posts to chat one reminder at 3 days and one at 7 days after the resolve, for each required postmortem that is still empty. A missed daily run skips that reminder.

Write the action items in the RCA as a checklist: `[ ] Title - @assignee - issue link`. Mark one done with `[x]`. The template has one example.

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

A silence mutes paging for a known problem. A person silences one alert, or a rule and then every alert of that rule. A silence ends at a set time, at most 30 days later, or when a person ends it. It needs a reason.

A silenced alert keeps its state and stays visible. It does not join an incident and does not page. When the silence ends, the alerts that still fire join incidents.

The Resolve action of an incident can silence the alerts that still fire, for some hours. When alerts still fire, the Resolve form shows their count and requires the hours. The incident then closes, and those alerts stay open and silenced. An SLO burn alert clears only when the error rate drops, so a burn that continues needs a silence to resolve.

| Use | When | Alerts | SLO |
| --- | --- | --- | --- |
| Maintenance | Planned work with a start and an end | Suppressed in its scope | Not counted only when it is Public and published before its start |
| Silence | A known problem, for a set time | Suppressed | Counted |
| Monitoring disabled on a resource | A resource that we never want to check | Not evaluated | Its rows are dropped |
