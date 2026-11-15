from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from beacon.core.exceptions import IncidentActionError
from beacon.core.status_page import PUBLISHED_VISIBILITIES, StatusCache

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident


class IncidentPublication:
	"""What customers see of one incident: its visibility, impact and link. Public and Unlisted incidents open
	at the same link. Only Public ones are listed."""

	def __init__(self, incident: BeaconIncident):
		self.incident = incident

	def publish(self, visibility: str, message: str, affected_services: list[dict] | None) -> None:
		incident = self.incident
		if incident.visibility != "Internal" or visibility not in PUBLISHED_VISIBILITIES:
			frappe.throw(_("Choose Public or Unlisted."), IncidentActionError)

		incident.visibility = visibility
		if affected_services is not None:
			incident.set("affected_services", affected_services)
		incident.public_id = self.make_public_id()
		if (
			incident.type == "Maintenance"
			and visibility == "Public"
			and now_datetime() < get_datetime(incident.scheduled_start)
		):
			incident.excluded_from_slo = 1
		incident.add_update(message, is_public=True)
		incident.save()

	def change_visibility(self, visibility: str) -> None:
		"""The link stays the same, so a link that support already sent keeps working."""
		incident = self.incident
		if incident.visibility == "Internal":
			frappe.throw(
				_("This incident is Internal. Use Publish to show it to customers."), IncidentActionError
			)
		if visibility not in PUBLISHED_VISIBILITIES or visibility == incident.visibility:
			frappe.throw(_("Choose Public or Unlisted."), IncidentActionError)

		incident.visibility = visibility
		incident.save()

	def regenerate_public_link(self) -> None:
		self._require_published()
		old_id = self.incident.public_id
		self.incident.public_id = self.make_public_id()
		self.incident.save()
		StatusCache.clear(old_id)

	def get_public_link(self) -> str:
		self._require_published()
		return frappe.get_cached_doc("Beacon Settings").get_status_page_url(
			f"/incidents/{self.incident.public_id}"
		)

	def _require_published(self) -> None:
		if self.incident.visibility not in PUBLISHED_VISIBILITIES or not self.incident.public_id:
			frappe.throw(_("Only a Public or Unlisted incident has a link."), IncidentActionError)

	@staticmethod
	def make_public_id() -> str:
		"""Random, so nobody can guess the link of an Unlisted incident or count the incidents."""
		return frappe.generate_hash(length=32)
