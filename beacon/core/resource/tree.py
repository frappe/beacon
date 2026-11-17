from __future__ import annotations

import frappe

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.core.resource.registry import ResourceRegistry


class ResourceTree:
	"""Keeps path and is_checked of one region's resources in step with their parents."""

	def __init__(self, region: str):
		self.region = region

	def rebuild(self) -> None:
		"""Recompute path and is_checked, write changed rows, and resolve alerts of unchecked resources."""
		registry = ResourceRegistry(self.region)
		updates: dict[str, dict] = {}
		removed: list[str] = []
		disabled: list[str] = []

		for row in registry.by_name.values():
			chain = registry.get_ancestors(row)
			path = "/" + "/".join(item.resource_id for item in reversed(chain))
			is_checked = int(all(item.status == "Active" for item in chain) and not row.monitoring_disabled)

			if path != row.path or is_checked != row.is_checked:
				updates[row.name] = {"path": path, "is_checked": is_checked}
			if row.is_checked and not is_checked:
				(disabled if row.monitoring_disabled else removed).append(row.name)

		frappe.db.bulk_update("Beacon Resource", updates)
		if removed:
			BeaconAlert.resolve_open("Resource Removed", resource=("in", removed))
		if disabled:
			BeaconAlert.resolve_open("Disabled", resource=("in", disabled))
