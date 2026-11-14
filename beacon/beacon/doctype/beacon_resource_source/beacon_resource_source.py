from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from beacon.core.exceptions import SourceFetchError
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.resource.source import (
	TELEMETRY_ID_FULL_FETCH_MINUTES,
	ResourceSync,
	SyncResult,
	TelemetryIdSync,
)
from beacon.core.resource.source_client import ResourceSourceClient
from beacon.core.transaction import RecordTransaction

# Sources sync every minute. A short outage of a source is normal and needs no chat post.
FAILED_FETCHES_TO_REPORT = 5


class BeaconResourceSource(Document):
	"""A service that lists the resources of a region, or Central's map of server IDs."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		base_url: DF.Data
		conflict_count: DF.Int
		enabled: DF.Check
		etag: DF.Data | None
		failed_fetches: DF.Int
		held_removals: DF.Int
		held_since: DF.Datetime | None
		kind: DF.Literal["Resources", "Telemetry IDs"]
		last_attempt_at: DF.Datetime | None
		last_error: DF.SmallText | None
		last_success_at: DF.Datetime | None
		orphan_count: DF.Int
		region: DF.Link | None
		row_count: DF.Int
		source_key: DF.Data
		title: DF.Data
		token: DF.Password | None
	# end: auto-generated types

	def autoname(self) -> None:
		self.name = f"{self.region}-{self.source_key}" if self.region else self.source_key

	def validate(self) -> None:
		if self.kind == "Telemetry IDs" and self.region:
			frappe.throw(_("A Telemetry IDs source serves every region. Leave Region empty."))

	@frappe.whitelist(methods=["POST"])
	def sync(self, use_etag: bool = False, allow_removals: bool = False) -> dict:
		"""Fetch the list and apply it, and return the counts. A failed fetch keeps the last list and records the error.

		One sync of a source runs at a time. The fetch runs with no row lock, then the source is locked and updated.
		"""
		self.check_permission("write")
		attempt_at = now_datetime()
		with frappe.db.advisory_lock(f"{frappe.db.cur_db_name}:beacon-source-sync:{self.name}", timeout=60):
			RecordTransaction.start_new()
			try:
				payload, etag = ResourceSourceClient(self).fetch(use_etag)
				error = None
			except SourceFetchError as fetch_error:
				payload, etag, error = None, None, str(fetch_error)

			return RecordTransaction(self.doctype, self.name).retry(
				lambda: self._apply(payload, etag, error, attempt_at, allow_removals)
			)

	@frappe.whitelist(methods=["POST"])
	def apply_held_removals(self, expected_count: int | None = None) -> dict:
		self.check_permission("write")
		if not self.held_removals:
			frappe.throw(_("There are no held removals."))
		if expected_count is not None and self.get_held_removals()["total"] != int(expected_count):
			frappe.throw(_("The list of resources to remove changed. Reload the page and check it again."))
		return self.sync(allow_removals=True)

	@frappe.whitelist()
	@frappe.read_only()
	def get_held_removals(self) -> dict:
		"""The count of held removals and the first ones, for the confirmation dialog."""
		self.check_permission("read")
		filters = {"source": self.name, "removal_held": 1}
		total = frappe.db.count("Beacon Resource", filters)
		sample = frappe.get_list(
			"Beacon Resource",
			filters=filters,
			fields=["name", "resource_id", "resource_type", "region", "parent_resource"],
			order_by="resource_id asc",
			limit_page_length=20,
		)
		return {"total": total, "sample": sample, "truncated": total > len(sample)}

	@staticmethod
	def queue_due_syncs() -> None:
		"""Queue one sync for each enabled source. A Telemetry IDs source reads its full list every few minutes."""
		from beacon.core.jobs import queue_source_sync

		is_full_fetch_due = now_datetime().minute % TELEMETRY_ID_FULL_FETCH_MINUTES == 0
		for source in frappe.get_all(
			"Beacon Resource Source", filters={"enabled": 1}, fields=["name", "kind"]
		):
			queue_source_sync(
				source.name, use_etag=not (source.kind == "Telemetry IDs" and is_full_fetch_due)
			)

	def _apply(
		self,
		payload: dict | None,
		etag: str | None,
		error: str | None,
		attempt_at: datetime,
		allow_removals: bool,
	) -> dict:
		source = frappe.get_doc(self.doctype, self.name, for_update=True)
		source.last_attempt_at = attempt_at
		result = SyncResult()
		try:
			if error:
				raise SourceFetchError(error)
			if payload is not None:
				result = (
					ResourceSync(source).apply(payload, allow_removals)
					if source.kind == "Resources"
					else TelemetryIdSync(source).apply(payload)
				)
				# A missed resource is removed only when the next fetch also misses it, so that fetch must read the full list.
				is_removal_waiting = frappe.db.exists(
					"Beacon Resource", {"source": source.name, "missing_fetches": (">", 0)}
				)
				source.etag = None if is_removal_waiting else etag
			source.last_success_at = attempt_at
			source.last_error = None
			source.failed_fetches = 0
		except SourceFetchError as sync_error:
			# Every SourceFetchError is raised before the first write, so nothing needs a rollback.
			source.last_error = str(sync_error)[:2000]
			source.failed_fetches += 1
			self._report_failure(source)

		source.save()
		return asdict(result)

	@staticmethod
	def _report_failure(source: BeaconResourceSource) -> None:
		"""Log the first failed fetch, and post to chat once when the source keeps failing."""
		if source.failed_fetches == 1:
			frappe.log_error(
				f"Beacon could not fetch resource source {source.name}",
				reference_doctype=source.doctype,
				reference_name=source.name,
			)
		if source.failed_fetches == FAILED_FETCHES_TO_REPORT:
			NotificationOutbox().post_to_chat(
				f"Resource source {source.name} failed {FAILED_FETCHES_TO_REPORT} fetches in a row."
				f" Beacon does not see new or removed resources from it: {source.last_error[:500]}"
			)
