from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import frappe

# A new or reactivated resource is not checked for this long, so it can finish booting.
NEW_RESOURCE_GRACE = timedelta(minutes=10)

RESOURCE_FIELDS = (
	"name",
	"title",
	"region",
	"resource_id",
	"resource_type",
	"parent_resource",
	"path",
	"status",
	"is_checked",
	"active_since",
	"source",
)


@dataclass(frozen=True)
class ResourceMatch:
	"""A region, resource type and resource filter. Blank fields match anything."""

	region: str | None = None
	resource_type: str | None = None
	resource_id: str | None = None
	include_descendants: bool = False

	@classmethod
	def from_row(cls, row: frappe._dict) -> ResourceMatch:
		"""Build from a scope or silence row: region, resource_type, resource_id and include_descendants."""
		return cls(row.region, row.resource_type, row.resource_id, bool(row.include_descendants))

	def covers(self, resource: frappe._dict) -> bool:
		"""True when the resource is this match, or is under it when descendants are included."""
		if self.region and self.region != resource.region:
			return False

		if not self.resource_id or self.resource_id == resource.resource_id:
			return not self.resource_type or self.resource_type == resource.resource_type

		return self.include_descendants and f"/{self.resource_id}/" in f"{resource.path}/"


class ResourceRegistry:
	"""The registry rows of one region, loaded once for one job."""

	def __init__(self, region: str):
		self.region = region
		rows = frappe.get_all("Beacon Resource", filters={"region": region}, fields=RESOURCE_FIELDS)
		self.by_name = {row.name: row for row in rows}
		self.by_resource_id = {row.resource_id: row for row in rows}

	@staticmethod
	def make_name(region: str, resource_id: str) -> str:
		return f"{region}:{resource_id}"

	def find(self, resource_id: str) -> frappe._dict | None:
		"""Find a resource by the ID that telemetry reports."""
		return self.by_resource_id.get(resource_id)

	def get(self, name: str) -> frappe._dict | None:
		return self.by_name.get(name)

	@property
	def region_resource(self) -> frappe._dict:
		return self.by_name[self.make_name(self.region, self.region)]

	def is_evaluated(self, resource: frappe._dict, now: datetime) -> bool:
		"""True when rule and SLI rows for this resource count: checked, and a listed resource past its grace."""
		is_new = resource.source and resource.active_since > now - NEW_RESOURCE_GRACE
		return bool(resource.is_checked) and not is_new

	def get_ancestors(self, resource: frappe._dict) -> list[frappe._dict]:
		"""Return the resource and each parent up to its root."""
		chain = []
		while resource:
			chain.append(resource)
			resource = self.by_name.get(resource.parent_resource)
		return chain
