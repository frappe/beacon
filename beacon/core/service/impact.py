from __future__ import annotations

import frappe

from beacon.core.service.status import STATUS_ORDER

CLOSED_STATUSES = ("Resolved", "Merged", "Completed", "Cancelled")


class PublishedImpact:
	"""The worst published impact of each service in each region, from open incidents and maintenances."""

	def __init__(self):
		self.impacts = self._load()

	def get(self, service: str, region: str) -> str:
		"""A blank region on the incident applies to every region."""
		return max(
			self.impacts.get((service, region), "Operational"),
			self.impacts.get((service, ""), "Operational"),
			key=STATUS_ORDER.index,
		)

	@staticmethod
	def _load() -> dict[tuple[str, str], str]:
		incidents = frappe.get_all(
			"Beacon Incident",
			filters={"visibility": "Public", "status": ("not in", (*CLOSED_STATUSES, "Scheduled"))},
			pluck="name",
		)
		rows = (
			frappe.get_all(
				"Beacon Incident Service",
				filters={"parent": ("in", incidents)},
				fields=["service", "region", "impact"],
			)
			if incidents
			else []
		)
		impacts: dict[tuple[str, str], str] = {}
		for row in rows:
			key = (row.service, row.region or "")
			impacts[key] = max(impacts.get(key, "Operational"), row.impact, key=STATUS_ORDER.index)
		return impacts
