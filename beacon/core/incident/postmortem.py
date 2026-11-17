from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_datetime, now_datetime, strip_html_tags

from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

LONG_INCIDENT = timedelta(hours=1)
# Beacon reminds the team once at 3 days and once at 7 days after the resolve.
REMINDER_DAYS = (3, 7)
REMINDER_STOPS_AFTER = timedelta(days=30)

TEMPLATE = """<h3>Summary</h3><p></p>
<h3>Impact</h3><p>Who was affected, how much and for how long.</p>
<h3>Timeline</h3><p>Start, detection, first response, fix and resolve, in UTC.</p>
<h3>Root cause</h3><p></p>
<h3>Trigger</h3><p>The change or event that started the problem.</p>
<h3>Detection</h3><p>How we found the problem, and how we can find it sooner.</p>
<h3>Lessons</h3><p>What went well, and what went badly.</p>
<h3>Action items</h3><p>[ ] Add a disk alert - @assignee - https://github.com/org/repo/issues/123</p>"""


class Postmortem:
	"""Decides when an incident needs a postmortem, and reminds the team until someone writes it."""

	def __init__(self, incident: BeaconIncident):
		self.incident = incident

	def prepare_on_resolve(self) -> None:
		"""Mark the incident when it needs a postmortem, and start the RCA from the template."""
		incident = self.incident
		if incident.type != "Incident":
			return
		if not incident.postmortem_required:
			incident.postmortem_required = int(self.is_required)
		if incident.postmortem_required and not incident.rca:
			incident.rca = TEMPLATE

	@property
	def is_required(self) -> bool:
		"""The triggers that the Google SRE book suggests: customer impact, high severity, a long outage, or late detection."""
		incident = self.incident
		duration = get_datetime(incident.resolved_at or now_datetime()) - get_datetime(incident.creation)
		return (
			incident.visibility != "Internal"
			or incident.severity == "Critical"
			or incident.detected_by == "Customer"
			or duration > LONG_INCIDENT
		)

	@staticmethod
	def remind_missing() -> None:
		"""Scheduled daily: post to chat at 3 and 7 days for each required postmortem that is still empty.
		A missed daily run catches up on the next run."""
		now = now_datetime()
		names = frappe.get_all(
			"Beacon Incident",
			filters={
				"postmortem_required": 1,
				"status": "Resolved",
				"resolved_at": (
					"between",
					[now - REMINDER_STOPS_AFTER, now - timedelta(days=REMINDER_DAYS[0])],
				),
			},
			pluck="name",
		)
		RecordTransaction.run_each(
			"Beacon Incident", names, "Beacon postmortem reminder failed", Postmortem._remind
		)

	@staticmethod
	def _remind(name: str) -> None:
		"""Lock the incident before the count, so two runs cannot queue the same reminder."""
		incident = frappe.get_doc("Beacon Incident", name, for_update=True)
		if not (incident.postmortem_required and incident.status == "Resolved" and is_empty(incident.rca)):
			return

		days = (now_datetime() - get_datetime(incident.resolved_at)).days
		target = sum(1 for day in REMINDER_DAYS if days >= day)
		if target > Postmortem._reminder_count(name):
			BeaconNotification.queue(
				"Chat",
				f"📝 **Postmortem needed for {ChatMessage.link(name)}.**"
				f" Resolved {days} days ago. Write the root cause.",
				name,
			)

	@staticmethod
	def _reminder_count(incident: str) -> int:
		"""A locking read, so a run that waited for the incident lock sees the reminder that just committed."""
		return len(
			frappe.db.get_values(
				"Beacon Notification",
				{
					"incident": incident,
					"channel": "Chat",
					"message": ("like", "📝 **Postmortem needed%"),
				},
				"name",
				for_update=True,
			)
		)


def is_empty(rca: str | None) -> bool:
	"""True when nobody wrote anything beyond the template."""
	words = " ".join(strip_html_tags(rca or "").split())
	return not words or words == " ".join(strip_html_tags(TEMPLATE).split())
