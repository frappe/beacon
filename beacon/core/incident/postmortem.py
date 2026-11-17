from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_datetime, now_datetime, strip_html_tags

from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.alert.states import PAGED_SEVERITIES
from beacon.core.incident.chat_message import ChatMessage

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

LONG_INCIDENT = timedelta(hours=1)
# Beacon reminds the team once at 3 days and once at 7 days after the resolve.
REMINDER_DAYS = (3, 7)

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
		"""The triggers that the Google SRE book suggests: customer impact, high severity, a long outage, or late detection.
		Only a paged incident counts as a long outage, so a Low or Medium incident that stays open is not one."""
		incident = self.incident
		duration = get_datetime(incident.resolved_at or now_datetime()) - get_datetime(incident.creation)
		return (
			incident.visibility != "Internal"
			or incident.severity == "Critical"
			or incident.detected_by == "Customer"
			or (incident.severity in PAGED_SEVERITIES and duration > LONG_INCIDENT)
		)

	@staticmethod
	def remind_missing() -> None:
		"""Scheduled daily: post to chat at 3 and 7 days for each required postmortem that is still empty."""
		now = now_datetime()
		names = frappe.get_all(
			"Beacon Incident",
			filters={
				"postmortem_required": 1,
				"status": "Resolved",
				"resolved_at": ("between", [now - timedelta(days=8), now - timedelta(days=3)]),
			},
			pluck="name",
		)
		for name in names:
			incident = frappe.get_doc("Beacon Incident", name)
			days = (now - get_datetime(incident.resolved_at)).days
			if days in REMINDER_DAYS and is_empty(incident.rca):
				BeaconNotification.queue(
					"Chat",
					f"📝 **Postmortem needed for {ChatMessage.link(name)}.**"
					f" Resolved {days} days ago. Write the root cause.",
					name,
				)


def is_empty(rca: str | None) -> bool:
	"""True when nobody wrote anything beyond the template."""
	words = " ".join(strip_html_tags(rca or "").split())
	return not words or words == " ".join(strip_html_tags(TEMPLATE).split())
