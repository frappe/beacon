# Paging

## Channels by severity

| Severity | Chat | Email to responders | Phone call | Next responder after |
| --- | --- | --- | --- | --- |
| Critical | Yes | Yes | Yes | 2 minutes |
| High | Yes | Yes | Yes | 5 minutes |
| Medium | Yes | Yes | No | Not applicable |
| Low | Yes | No | No | Not applicable |

The times are defaults in Beacon Settings. An admin can change them.

## Who Beacon calls

Beacon Settings has an ordered list of responders. Each active responder must be an enabled user with the Beacon Responder or Beacon Admin role, because an answered call acknowledges as that person. Beacon calls the primary override until its end time, when that person is an active responder. Else it calls the first active responder. When the responder does not acknowledge in the backup time, Beacon calls the next one. After the last responder, Beacon starts another round after the repeat interval, 15 minutes by default.

A High incident stops after the maximum rounds, 3 by default, and Beacon posts to chat that nobody acknowledged. A Critical incident continues until a person acknowledges.

## Acknowledge

The call says the severity of the incident and what happened, without IDs, twice, in a neural voice. When the responder answers the call, Beacon acknowledges the incident. Twilio reports the answer to a callback URL that holds a token for that call, so Beacon needs a Twilio API key and not the account Auth Token. Beacon accepts a callback only with the token of a call notification, from the Twilio account in Beacon Settings, for the same call, and for 1 hour after the call. It follows a merge to the open incident and ignores repeated callbacks. A call that is answered after the incident closed acknowledges nothing. When Twilio reports that the call failed, Beacon posts to chat once.

Staff acknowledge with the Acknowledge action. Chat tools and scripts can call the same action:

```text
POST /api/v2/document/Beacon%20Incident/<name>/method/acknowledge
```

## Escalation states

| From | Event | To |
| --- | --- | --- |
| None | Incident created, and its resource has a parent | Waiting, for the page delay |
| None, Waiting | The page delay ends, the resource has no parent, or Page On-call | Calling |
| Calling | No acknowledge in the backup time | Calling the next responder |
| Calling | The Acknowledge action or an answered call | Done |
| Calling | The maximum rounds without an answer, and the severity is High | Done, and a chat post |
| Waiting, Calling | The incident merges or resolves | Done |

## Paging pause

"Paging Paused" in Beacon Settings stops all calls and emails, for example after a database restore. Escalations continue when the pause ends.

## Emails to responders

Each email is a report card from `beacon/templates/emails/` with the incident's severity, status, start time, regions and alerts, and a link to the incident. The subject is `[Severity][Incident] Title`.

| Email | Sent to | When |
| --- | --- | --- |
| New incident | Every active responder | An incident starts, unless it is Low |
| Escalation page | The responder that Beacon calls | Each page round, with the call |
| Incident resolved | Every active responder | The incident resolves, unless it is Low. It shows the closing note. |

## Delivery

Beacon writes each notification row in the same transaction as the change that caused it. A job sends it after the commit, on the `short` queue, so a page does not wait behind slow region runs. The job first moves the row from Queued to Sending, so a second job sends nothing. When paging is paused, or the channel or the responder is disabled when the job runs, the row becomes Cancelled. A row that stays in Sending means that the job stopped during the call to the provider. Beacon does not send it again, because the provider may have it.

Chat messages use Markdown, and the incident name links to the incident in Desk. The message for a new incident or a raised severity starts with the severity, the incident and the region, then shows a table with the resource and detail of the alert. The message for joined alerts shows the same table with the alert title. The message for a resolved incident shows it for the alerts of the incident, with how long each alert fired, or `silenced` for an alert that still fired at resolve. A table shows up to 5 alerts, then the number of the others. The value of an alert is only on its form, because it has no unit. Alert and incident titles are `<rule> on <resource>`, with the resource title, or the resource ID when it has no title.

When a call or an email fails, Beacon posts to chat who did not get it, with a link to the notification. The error stays on the notification, because a provider response can hold data that chat must not show. For an email, Sent means that Frappe accepted it into the Email Queue. Frappe sends it later and records a delivery error on the Email Queue row, not on the notification.

## Heartbeat

Each minute, Beacon sends a heartbeat to an external service. Beacon holds the heartbeat while a rule evaluation or a queued notification is more than 5 minutes late. So the external service calls the team when Beacon or its workers stop.
