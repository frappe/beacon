from __future__ import annotations

import frappe

from beacon.core.alert.reconciler import AlertReconciler
from beacon.core.incident.recovery import IncidentRecovery


class ResourceTree:
	"""Keeps path and is_checked of one region's resources in step with their parents."""

	def __init__(self, region: str):
		self.region = region

	def rebuild(self) -> None:
		"""Recompute path and is_checked, write changed rows, and resolve alerts of unchecked resources."""
		rows = {
			row.name: row
			for row in frappe.get_all(
				"Beacon Resource",
				filters={"region": self.region},
				fields=[
					"name",
					"resource_id",
					"parent_resource",
					"status",
					"monitoring_disabled",
					"path",
					"is_checked",
				],
			)
		}
		updates: dict[str, dict] = {}
		removed: list[str] = []
		disabled: list[str] = []

		for row in rows.values():
			chain = self._get_chain(row, rows)
			path = "/" + "/".join(item.resource_id for item in reversed(chain))
			is_checked = int(all(item.status == "Active" for item in chain) and not row.monitoring_disabled)

			if path != row.path or is_checked != row.is_checked:
				updates[row.name] = {"path": path, "is_checked": is_checked}
			if row.is_checked and not is_checked:
				(disabled if row.monitoring_disabled else removed).append(row.name)

		frappe.db.bulk_update("Beacon Resource", updates)
		incidents: set[str] = set()
		for resources, resolution in ((removed, "Resource Removed"), (disabled, "Disabled")):
			if resources:
				incidents |= AlertReconciler.resolve_matching({"resource": ("in", resources)}, resolution)
		IncidentRecovery.queue(incidents)

	def _get_chain(self, row: frappe._dict, rows: dict[str, frappe._dict]) -> list[frappe._dict]:
		"""The row and its ancestors. A parent loop ends the chain where it repeats."""
		chain: list[frappe._dict] = []
		seen: set[str] = set()
		while row and row.name not in seen:
			chain.append(row)
			seen.add(row.name)
			row = rows.get(row.parent_resource)
		return chain
