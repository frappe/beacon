from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import frappe

OUTAGE_STATUSES = ("Partial Outage", "Major Outage")


@dataclass
class ServiceSample:
	"""The summed SLI rows of one minute."""

	good: int = 0
	valid: int = 0
	reporters: int = 0
	bad_reporters: int = 0

	@property
	def ratio(self) -> float | None:
		return 100 * self.good / self.valid if self.valid else None


class ServiceHistory:
	"""Writes the minute rows and the daily totals of one service in one region."""

	def __init__(self, service: str, region: str):
		self.service = service
		self.region = region

	def record(self, minute: datetime, sample: ServiceSample, status: str) -> None:
		frappe.get_doc(
			{
				"doctype": "Beacon Service Minute",
				"service": self.service,
				"region": self.region,
				"minute": minute,
				"status": status,
				"good": sample.good,
				"valid": sample.valid,
				"reporters": sample.reporters,
				"bad_reporters": sample.bad_reporters,
			}
		).insert(ignore_permissions=True)

		day = frappe.qb.DocType("Beacon Service Day")
		(
			frappe.qb.update(day)
			.set(day.good, day.good + sample.good)
			.set(day.valid, day.valid + sample.valid)
			.set(day.observed_minutes, day.observed_minutes + 1)
			.set(day.degraded_minutes, day.degraded_minutes + int(status == "Degraded"))
			.set(day.outage_minutes, day.outage_minutes + int(status in OUTAGE_STATUSES))
			.set(day.maintenance_minutes, day.maintenance_minutes + int(status == "Maintenance"))
			.where(day.name == self._get_day(minute))
		).run()

	def _get_day(self, minute: datetime) -> str:
		filters = {"service": self.service, "region": self.region, "date": minute.date()}
		return (
			frappe.db.get_value("Beacon Service Day", filters)
			or frappe.get_doc({"doctype": "Beacon Service Day", **filters})
			.insert(ignore_permissions=True)
			.name
		)
