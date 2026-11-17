from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import frappe


@dataclass
class ServiceSample:
	"""The summed SLI rows of one minute."""

	good: int = 0
	valid: int = 0
	reporters: int = 0
	bad_resources: list[str] = field(default_factory=list)

	@property
	def bad_reporters(self) -> int:
		return len(self.bad_resources)

	@property
	def ratio(self) -> float | None:
		return 100 * self.good / self.valid if self.valid else None


class ServiceHistory:
	"""Writes the minute rows of one service in one region."""

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
