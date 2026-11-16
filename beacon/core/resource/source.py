from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import frappe
from frappe.utils import now_datetime

from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.exceptions import SourceFetchError
from beacon.core.resource.registry import ResourceRegistry
from beacon.core.resource.tree import ResourceTree

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_resource_source.beacon_resource_source import BeaconResourceSource

RESOURCE_STATUSES = ("Pending", "Active", "Suspended", "Terminated")

# A list that would uncheck more than this share of a source's checked resources is held for an admin.
HELD_REMOVAL_RATIO = 0.1

# A resource missing from this many good fetches in a row is Terminated.
MISSING_FETCHES_TO_TERMINATE = 2


@dataclass(frozen=True)
class SourceRow:
	"""One resource as a Resource Source lists it."""

	resource_id: str
	resource_type: str
	parent_id: str
	title: str
	status: str

	@classmethod
	def parse(cls, payload: Any) -> SourceRow:
		if not isinstance(payload, dict):
			raise SourceFetchError(f"A resource row is not an object: {payload!r}")

		values = {name: payload.get(name) or "" for name in cls.__dataclass_fields__}
		if not all(isinstance(value, str) for value in values.values()):
			raise SourceFetchError(f"A resource row has a field that is not a string: {payload!r}")

		row = cls(**values)
		if not row.resource_id or not row.resource_type:
			raise SourceFetchError(f"A resource row has no resource_id or resource_type: {payload!r}")
		if row.status not in RESOURCE_STATUSES:
			raise SourceFetchError(f"Resource {row.resource_id} has an unknown status {row.status!r}")
		return row

	@property
	def row_hash(self) -> str:
		return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass
class SyncResult:
	added: int = 0
	changed: int = 0
	removed: int = 0
	held: int = 0
	conflicts: int = 0


class ResourceSync:
	"""Applies the resource list of one source to the registry of its region."""

	def __init__(self, source: BeaconResourceSource):
		self.source = source
		self.region = source.region
		self.now = now_datetime()

	def apply(self, payload: dict, allow_removals: bool = False) -> SyncResult:
		"""Write added and changed rows, terminate missing rows, and hold large removals."""
		rows = self._parse(payload)
		existing = {
			row.name: row
			for row in frappe.get_all(
				"Beacon Resource",
				filters={"region": self.region, "resource_type": ("!=", "Region")},
				fields=["name", "source", "status", "row_hash", "missing_fetches", "parent_resource"],
			)
		}
		result = SyncResult()
		listed, inserts, updates = set(), [], {}

		for row in rows:
			name = ResourceRegistry.make_name(self.region, row.resource_id)
			current = existing.get(name)
			if current and current.source != self.source.name:
				result.conflicts += 1
				continue

			listed.add(name)
			if not current:
				inserts.append((name, row))
			elif current.row_hash != row.row_hash or current.missing_fetches:
				updates[name] = self._get_changes(row, current)

		missing = {
			name: current
			for name, current in existing.items()
			if current.source == self.source.name and name not in listed and current.status != "Terminated"
		}
		removals = self._get_removals(updates, missing, existing)

		if removals and not allow_removals and self._is_removal_large(removals, rows):
			self._hold(removals, updates, missing)
			result.held = len(removals)
		else:
			self._release_holds()
			self._terminate_missing(missing, updates)

		self._insert(inserts, existing)
		self._set_parents(updates, rows, existing)
		frappe.db.bulk_update("Beacon Resource", updates)

		result.added, result.changed = len(inserts), len(updates)
		result.removed = sum(1 for changes in updates.values() if changes.get("status") == "Terminated")
		self.source.conflict_count = result.conflicts
		self.source.row_count = len(rows)

		ResourceTree(self.region).rebuild()
		return result

	def _parse(self, payload: dict) -> list[SourceRow]:
		resources = payload.get("resources") if isinstance(payload, dict) else None
		if not isinstance(resources, list):
			raise SourceFetchError("The response has no resources list.")

		rows = [SourceRow.parse(item) for item in resources]
		if len({row.resource_id for row in rows}) != len(rows):
			raise SourceFetchError("The response lists one resource_id more than once.")
		return rows

	def _get_changes(self, row: SourceRow, current: frappe._dict) -> dict:
		changes = {
			"resource_type": row.resource_type,
			"title": row.title,
			"status": row.status,
			"row_hash": row.row_hash,
			"missing_fetches": 0,
		}
		if row.status == "Active" and current.status != "Active":
			changes["active_since"] = self.now
		if row.status == "Terminated" and current.status != "Terminated":
			changes["terminated_at"] = self.now
		return changes

	def _get_removals(self, updates: dict, missing: dict, existing: dict) -> set[str]:
		"""Names of checked resources that this list would terminate. A Suspended or Pending status is a state
		that the source reports on purpose, such as a sleeping VM, so it is not a removal."""
		leaving = {name for name, changes in updates.items() if changes["status"] == "Terminated"}
		at_limit = {
			name
			for name, current in missing.items()
			if current.missing_fetches + 1 >= MISSING_FETCHES_TO_TERMINATE
		}
		return {name for name in leaving | at_limit if existing[name].status == "Active"}

	def _is_removal_large(self, removals: set[str], rows: list[SourceRow]) -> bool:
		if not rows:
			return True

		checked = dict(
			frappe.get_all(
				"Beacon Resource",
				filters={"source": self.source.name, "is_checked": 1},
				fields=["name", "path"],
				as_list=True,
			)
		)
		removed_paths = [checked[name] for name in removals if name in checked]
		unchecked = [
			path
			for path in checked.values()
			if any(f"{path}/".startswith(f"{removed}/") for removed in removed_paths)
		]
		return len(unchecked) > HELD_REMOVAL_RATIO * max(len(checked), 1)

	def _hold(self, removals: set[str], updates: dict, missing: dict) -> None:
		"""Keep held resources as they are, so the next fetch sees the same removal again."""
		for name in removals:
			updates[name] = {"removal_held": 1}
			missing.pop(name, None)

		for name, current in missing.items():
			updates.setdefault(name, {})["missing_fetches"] = current.missing_fetches + 1

		if not self.source.held_removals:
			self.source.held_since = self.now
			count = f"{len(removals)} resource" if len(removals) == 1 else f"{len(removals)} resources"
			BeaconNotification.post_to_chat(
				f"⚠️ **Resource removals need review.** {self.source.title or self.source.name}"
				f" wants to remove {count}. Beacon keeps watching them."
				" Check the list in the Resource Source, then apply it."
			)
		self.source.held_removals = len(removals)

	def _release_holds(self) -> None:
		held = frappe.get_all(
			"Beacon Resource", filters={"source": self.source.name, "removal_held": 1}, pluck="name"
		)
		frappe.db.bulk_update("Beacon Resource", {name: {"removal_held": 0} for name in held})
		self.source.held_removals = 0
		self.source.held_since = None

	def _terminate_missing(self, missing: dict, updates: dict) -> None:
		for name, current in missing.items():
			fetches = current.missing_fetches + 1
			changes = updates.setdefault(name, {})
			changes["missing_fetches"] = fetches
			if fetches >= MISSING_FETCHES_TO_TERMINATE:
				changes.update(status="Terminated", terminated_at=self.now, row_hash="")

	def _insert(self, inserts: list[tuple[str, SourceRow]], existing: dict) -> None:
		rows = [
			{
				"name": name,
				"creation": self.now,
				"modified": self.now,
				"owner": "Administrator",
				"modified_by": "Administrator",
				"region": self.region,
				"source": self.source.name,
				"resource_id": row.resource_id,
				"resource_type": row.resource_type,
				"title": row.title,
				"status": row.status,
				"row_hash": row.row_hash,
				"active_since": self.now if row.status == "Active" else None,
				"terminated_at": self.now if row.status == "Terminated" else None,
				"missing_fetches": 0,
			}
			for name, row in inserts
		]
		if rows:
			frappe.db.bulk_insert("Beacon Resource", list(rows[0]), [tuple(row.values()) for row in rows])
		existing.update({name: frappe._dict(parent_resource=None) for name, _ in inserts})

	def _set_parents(self, updates: dict, rows: list[SourceRow], existing: dict) -> None:
		"""Point each row at its listed parent. An unknown parent keeps the last known parent."""
		orphans = 0
		for row in rows:
			name = ResourceRegistry.make_name(self.region, row.resource_id)
			if name not in existing:
				continue

			parent = ResourceRegistry.make_name(self.region, row.parent_id) if row.parent_id else None
			if parent and parent not in existing:
				orphans += 1
				continue
			if existing[name].parent_resource != parent:
				updates.setdefault(name, {})["parent_resource"] = parent

		self.source.orphan_count = orphans
