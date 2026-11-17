from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import frappe
from frappe.query_builder.functions import Count, Sum
from pypika.terms import Case

from beacon.core.alert.reconciler import RuleFailure

# A window with fewer valid events than this never alerts.
MIN_VALID_EVENTS = 100

# A window alerts only when measured minutes cover this share of it, so a new service does not page on its first bad minutes.
MIN_WINDOW_COVERAGE = 0.5

BUDGET_PERIOD = timedelta(days=30)

# A burn alert pages only when, in one minute of its short window, this many reporters were bad, or all of them when fewer reported.
MIN_BAD_REPORTERS_TO_PAGE = 3


@dataclass(frozen=True)
class BurnAlert:
	"""One multiwindow burn-rate alert from the Google SRE workbook."""

	alert_key: str
	long_window: timedelta
	short_window: timedelta
	burn_rate: float
	can_page: bool


BURN_ALERTS = (
	BurnAlert("fast", timedelta(hours=1), timedelta(minutes=5), 14.4, can_page=True),
	BurnAlert("medium", timedelta(hours=6), timedelta(minutes=30), 6.0, can_page=True),
	BurnAlert("slow", timedelta(days=3), timedelta(hours=6), 1.0, can_page=False),
)
# The SLA alert fires when the error rate is above what the SLA Target allows in both windows.
SLA_LONG_WINDOW = timedelta(hours=1)
SLA_SHORT_WINDOW = timedelta(minutes=15)

BURN_WINDOWS = sorted(
	{SLA_LONG_WINDOW, SLA_SHORT_WINDOW}
	| {window for alert in BURN_ALERTS for window in (alert.long_window, alert.short_window)}
)


@dataclass(frozen=True)
class WindowSums:
	good: int
	valid: int
	paging_minutes: int
	minutes: int

	def get_burn_rate(self, window: timedelta, error_budget: float) -> float | None:
		if self.valid < MIN_VALID_EVENTS or self.minutes < MIN_WINDOW_COVERAGE * window / timedelta(
			minutes=1
		):
			return None
		return (1 - self.good / self.valid) / error_budget


class BurnPolicy:
	"""Reads the stored minutes of one service in one region and finds the burn alerts that fail."""

	def __init__(self, service: frappe._dict, region: str, minute: datetime):
		self.service = service
		self.region = region
		self.minute = minute
		self.error_budget = 1 - service.slo_target / 100
		self.windows = self._load_window_sums()
		self.rates = {
			window: sums.get_burn_rate(window, self.error_budget) for window, sums in self.windows.items()
		}

	@property
	def burn_rates_label(self) -> str:
		"""The 1 hour and 6 hour burn rates, as shown on the Service State."""
		return " / ".join(
			"n/a" if self.rates[window] is None else f"{self.rates[window]:.1f}"
			for window in (timedelta(hours=1), timedelta(hours=6))
		)

	@property
	def alerts(self) -> tuple[BurnAlert, ...]:
		"""The burn alerts, and the SLA alert when the service has an SLA Target."""
		if not self.service.sla_target:
			return BURN_ALERTS

		sla_burn_rate = (1 - self.service.sla_target / 100) / self.error_budget
		return (
			*BURN_ALERTS,
			BurnAlert("sla", SLA_LONG_WINDOW, SLA_SHORT_WINDOW, sla_burn_rate, can_page=True),
		)

	def get_failures(self, resource: frappe._dict) -> list[RuleFailure]:
		failures = []
		for alert in self.alerts:
			long_rate, short_rate = self.rates[alert.long_window], self.rates[alert.short_window]
			if long_rate is None or short_rate is None or min(long_rate, short_rate) < alert.burn_rate:
				continue

			detail = (
				f"Error rate {self._get_error_rate(long_rate)} over the last"
				f" {format_window(alert.long_window)}, {self._get_error_rate(short_rate)} over the last"
				f" {format_window(alert.short_window)}."
			)
			failures.append(
				RuleFailure(resource, alert.alert_key, long_rate, detail, self._get_severity(alert))
			)
		return failures

	def get_budget_left(self) -> float | None:
		"""Share of the 30-day error budget left. Before 30 days are measured, valid events are projected to 30 days."""
		minute = frappe.qb.DocType("Beacon Service Minute")
		good, valid, observed = (
			int(value or 0)
			for value in (
				frappe.qb.from_(minute)
				.select(Sum(minute.good), Sum(minute.valid), Count(minute.name))
				.where(
					(minute.service == self.service.name)
					& (minute.region == self.region)
					& (minute.minute > self.minute - BUDGET_PERIOD)
					& (minute.minute <= self.minute)
				)
			).run()[0]
		)
		if not valid:
			return None

		period_valid = valid * max(1, BUDGET_PERIOD / timedelta(minutes=observed))
		return 100 * (1 - (valid - good) / (self.error_budget * period_valid))

	def _get_error_rate(self, burn_rate: float) -> str:
		return f"{100 * burn_rate * self.error_budget:.2f}%"

	def _get_severity(self, alert: BurnAlert) -> str:
		if not alert.can_page or not self.windows[alert.short_window].paging_minutes:
			return "Low"
		return "High" if self.service.page_on_burn else "Medium"

	def _load_window_sums(self) -> dict[timedelta, WindowSums]:
		minute = frappe.qb.DocType("Beacon Service Minute")
		# A minute stored before Beacon counted reporters has 0, so it needs the full count.
		is_paging_minute = (minute.bad_reporters >= MIN_BAD_REPORTERS_TO_PAGE) | (
			(minute.reporters > 0) & (minute.bad_reporters >= minute.reporters)
		)
		columns = []
		for window in BURN_WINDOWS:
			since = minute.minute > self.minute - window
			columns += [
				Sum(Case().when(since, minute.good).else_(0)),
				Sum(Case().when(since, minute.valid).else_(0)),
				Sum(Case().when(since & is_paging_minute, 1).else_(0)),
				Count(Case().when(since, 1)),
			]
		row = (
			frappe.qb.from_(minute)
			.select(*columns)
			.where(
				(minute.service == self.service.name)
				& (minute.region == self.region)
				& (minute.minute > self.minute - BURN_WINDOWS[-1])
			)
		).run()[0]
		values = [int(value or 0) for value in row]
		return {
			window: WindowSums(*values[4 * index : 4 * index + 4])
			for index, window in enumerate(BURN_WINDOWS)
		}


def format_window(window: timedelta) -> str:
	minutes = int(window / timedelta(minutes=1))
	if minutes % (24 * 60) == 0:
		count, unit = minutes // (24 * 60), "day"
	elif minutes % 60 == 0:
		count, unit = minutes // 60, "hour"
	else:
		count, unit = minutes, "minute"
	return f"{count} {unit}" if count == 1 else f"{count} {unit}s"
