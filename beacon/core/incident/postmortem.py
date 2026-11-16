from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_datetime, now_datetime, strip_html_tags

from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.incident.chat_message import ChatMessage

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

LONG_INCIDENT = timedelta(hours=1)
# Beacon reminds the team each day in this window after the resolve.
REMINDER_STARTS_AFTER = timedelta(days=3)
REMINDER_STOPS_AFTER = timedelta(days=30)

TEMPLATE = """<h3>Summary</h3><p></p>
<h3>Impact</h3><p>Who was affected, how much and for how long.</p>
<h3>Timeline</h3><p>Start, detection, first response, fix and resolve, in UTC.</p>
<h3>Root cause</h3><p></p>
<h3>Trigger</h3><p>The change or event that started the problem.</p>
<h3>Detection</h3><p>How we found the problem, and how we can find it sooner.</p>
<h3>Lessons</h3><p>What went well, and what went badly.</p>"""


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
		"""Scheduled daily: post to chat for each required postmortem that is still empty."""
		now = now_datetime()
		incidents = frappe.get_all(
			"Beacon Incident",
			filters={
				"postmortem_required": 1,
				"status": "Resolved",
				"resolved_at": ("between", [now - REMINDER_STOPS_AFTER, now - REMINDER_STARTS_AFTER]),
			},
			fields=["name", "title", "rca", "resolved_at"],
		)
		for incident in incidents:
			if is_empty(incident.rca):
				days = (now - get_datetime(incident.resolved_at)).days
				BeaconNotification.queue(
					"Chat",
					f"📝 **Postmortem needed for {ChatMessage.link(incident.name)}.**"
					f" Resolved {days} days ago. Write the root cause.",
					incident.name,
				)


def is_empty(rca: str | None) -> bool:
	"""True when nobody wrote anything beyond the template."""
	words = " ".join(strip_html_tags(rca or "").split())
	return not words or words == " ".join(strip_html_tags(TEMPLATE).split())
