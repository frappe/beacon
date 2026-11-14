from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from beacon.core.alert.states import OPEN_ALERT_STATUSES
from beacon.core.exceptions import IncidentActionError
from beacon.core.incident.escalation import IncidentEscalation

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

OPEN_STATUSES = ("Triggered", "Investigating", "Identified", "Monitoring")
CLOSED_STATUSES = ("Resolved", "Merged")
NEXT_STATUSES = {
	**{
		status: ("Investigating", "Identified", "Monitoring", "Resolved", "Merged")
		for status in OPEN_STATUSES
	},
	"Resolved": ("Triggered", "Investigating"),
	"Scheduled": ("In Progress", "Cancelled"),
	"In Progress": ("Completed",),
}


class IncidentLifecycle:
	"""The status machine of one incident or maintenance. Every status change goes through move_to."""

	def __init__(self, incident: BeaconIncident):
		self.incident = incident

	@property
	def is_open(self) -> bool:
		return self.incident.status in OPEN_STATUSES

	def move_to(self, status: str) -> None:
		if status not in NEXT_STATUSES.get(self.incident.status, ()):
			frappe.throw(
				_("An incident that is {0} cannot become {1}.").format(self.incident.status, status),
				IncidentActionError,
			)

		self.incident.status = status
		if status in CLOSED_STATUSES:
			self._close()
		elif status == "In Progress":
			self.incident.scheduled_start = min(get_datetime(self.incident.scheduled_start), now_datetime())
		elif status == "Completed":
			self.incident.resolved_at = now_datetime()

	def require_open(self) -> None:
		if not self.is_open:
			frappe.throw(
				_("You cannot do this because the incident is {0}.").format(self.incident.status),
				IncidentActionError,
			)

	def require(self, *statuses: str) -> None:
		if self.incident.status not in statuses:
			frappe.throw(
				_("You can only do this when it is {0}.").format(_(" or ").join(statuses)),
				IncidentActionError,
			)

	def _close(self) -> None:
		"""Stop notifications and release the open alerts for a new incident."""
		self.incident.resolved_at = now_datetime()
		IncidentEscalation(self.incident).stop()
		frappe.db.set_value(
			"Beacon Alert",
			{"incident": self.incident.name, "status": ("in", OPEN_ALERT_STATUSES)},
			"incident",
			None,
			update_modified=False,
		)
