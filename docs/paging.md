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

Beacon Settings has an ordered list of responders. Beacon calls the primary override until its end time, when that person is an active responder. Else it calls the first active responder. When the responder does not acknowledge in the backup time, Beacon calls the next one. After the last responder, Beacon starts another round after the repeat interval, 15 minutes by default.

A High incident stops after the maximum rounds, 3 by default, and Beacon posts to chat that nobody acknowledged. A Critical incident continues until a person acknowledges.

## Acknowledge

The call asks the responder to press 1. Only the digit 1 acknowledges. Beacon checks the Twilio signature, finds the notification of the call, follows a merge to the open incident, and ignores repeated callbacks.

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
| Calling | The Acknowledge action or a key press | Done |
| Calling | The maximum rounds without an answer, and the severity is High | Done, and a chat post |
| Waiting, Calling | The incident merges or resolves | Done |

## Paging pause

"Paging Paused" in Beacon Settings stops all calls and emails, for example after a database restore. Escalations continue when the pause ends.

## Delivery

Beacon writes each notification row in the same transaction as the change that caused it. A job sends it after the commit, on the `short` queue, so a page does not wait behind slow region runs. When a call or an email fails, Beacon posts the error to chat.

## Heartbeat

Each minute, Beacon sends a heartbeat to an external service. Beacon holds the heartbeat while a rule evaluation or a queued notification is more than 5 minutes late. So the external service calls the team when Beacon or its workers stop.
