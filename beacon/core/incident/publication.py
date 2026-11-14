from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from beacon.core.exceptions import IncidentActionError
from beacon.core.status_page import StatusCache

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

PUBLISHED_VISIBILITIES = ("Public", "Unlisted")


class IncidentPublication:
	"""What customers see of one incident: its visibility, impact and private link."""

	def __init__(self, incident: BeaconIncident):
		self.incident = incident

	def publish(self, visibility: str, message: str, affected_services: list[dict] | None) -> None:
		incident = self.incident
		if incident.visibility != "Internal" or visibility not in PUBLISHED_VISIBILITIES:
			frappe.throw(_("Choose Public or Unlisted."), IncidentActionError)

		incident.visibility = visibility
		if affected_services is not None:
			incident.set("affected_services", affected_services)
		incident.share_token = self._make_token() if visibility == "Unlisted" else None
		if (
			incident.type == "Maintenance"
			and visibility == "Public"
			and now_datetime() < get_datetime(incident.scheduled_start)
		):
			incident.excluded_from_slo = 1
		incident.add_update(message, is_public=True)
		incident.save()

	def change_visibility(self, visibility: str) -> None:
		incident = self.incident
		if incident.visibility == "Internal":
			frappe.throw(
				_("This incident is Internal. Use Publish to show it to customers."), IncidentActionError
			)
		if visibility not in PUBLISHED_VISIBILITIES or visibility == incident.visibility:
			frappe.throw(_("Choose Public or Unlisted."), IncidentActionError)

		old_token = incident.share_token
		incident.visibility = visibility
		incident.share_token = self._make_token() if visibility == "Unlisted" else None
		incident.save()
		StatusCache.clear(incident.name, old_token)

	def regenerate_private_link(self) -> None:
		self._require_unlisted()
		old_token = self.incident.share_token
		self.incident.share_token = self._make_token()
		self.incident.save()
		StatusCache.clear(self.incident.name, old_token)

	def get_private_link(self) -> str:
		self._require_unlisted()
		return frappe.get_cached_doc("Beacon Settings").get_status_page_url(f"/i/{self.incident.share_token}")

	def _require_unlisted(self) -> None:
		if self.incident.visibility != "Unlisted" or not self.incident.share_token:
			frappe.throw(_("Only an Unlisted incident has a private link."), IncidentActionError)

	@staticmethod
	def _make_token() -> str:
		return frappe.generate_hash(length=32)
