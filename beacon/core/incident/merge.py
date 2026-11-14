from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _

from beacon.core.alert.states import OPEN_ALERT_STATUSES
from beacon.core.exceptions import IncidentActionError
from beacon.core.incident.outbox import NotificationOutbox

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

SCOPE_FIELDS = ("region", "resource_type", "resource_id", "include_descendants")


class IncidentMerge:
	"""Moves one incident into another: its open alerts go to the target, and it closes as Merged."""

	def __init__(self, source: BeaconIncident, target: str):
		self.source = source
		self.target = frappe.get_doc("Beacon Incident", target, for_update=True)

	@staticmethod
	def find_final_incident(incident: str) -> str:
		"""Follow merged_into to the incident that now holds the work. A merge loop stops where it repeats."""
		seen: set[str] = set()
		while incident not in seen:
			seen.add(incident)
			merged_into = frappe.db.get_value("Beacon Incident", incident, "merged_into")
			if not merged_into:
				return incident
			incident = merged_into
		return incident

	def merge(self) -> None:
		"""The staff action: also copy the scope and group keys to the target."""
		self._check_target()
		for row in self.source.scope:
			self.target.append("scope", {field: row.get(field) for field in SCOPE_FIELDS})
		for key in (self.source.group_keys or "").split():
			self.target.add_group_key(key)
		self.target.save()
		self.close_as_merged()

	def close_as_merged(self) -> None:
		frappe.db.set_value(
			"Beacon Alert",
			{"incident": self.source.name, "status": ("in", OPEN_ALERT_STATUSES)},
			"incident",
			self.target.name,
			update_modified=False,
		)
		self.source.merged_into = self.target.name
		self.source.lifecycle.move_to("Merged")
		self.source.save(ignore_permissions=True)
		NotificationOutbox().announce(self.target, f"{self.source.name} merged into {self.target.name}.")

	def _check_target(self) -> None:
		target = self.target
		if target.name == self.source.name or not target.lifecycle.is_open or target.type != "Incident":
			frappe.throw(_("Choose a different incident that is still open."), IncidentActionError)
		if self.source.visibility != "Internal" and target.visibility == "Internal":
			frappe.throw(
				_("Customers can see this incident, so it cannot merge into an Internal incident."),
				IncidentActionError,
			)
