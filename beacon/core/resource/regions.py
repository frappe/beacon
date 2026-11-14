from __future__ import annotations

import frappe


class RegionSelection:
	"""The active regions that each rule or service runs in. No chosen region means every active region."""

	def __init__(self, parenttype: str):
		self.active = self.get_active()
		self.chosen: dict[str, set[str]] = {}
		for row in frappe.get_all(
			"Beacon Region Item", filters={"parenttype": parenttype}, fields=["parent", "region"]
		):
			self.chosen.setdefault(row.parent, set()).add(row.region)

	def get_regions(self, parent: str) -> list[str]:
		chosen = self.chosen.get(parent)
		return [region for region in self.active if not chosen or region in chosen]

	@staticmethod
	def get_active() -> list[str]:
		return frappe.get_all("Beacon Region", filters={"status": "Active"}, pluck="name", order_by="name")
