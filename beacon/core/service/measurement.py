from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_datetime

from beacon.core.alert.reconciler import AlertReconciler
from beacon.core.alert.rules import AlertRules
from beacon.core.exceptions import ClickHouseError
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.registry import ResourceMatch, ResourceRegistry
from beacon.core.service.burn import BurnPolicy
from beacon.core.service.history import ServiceHistory, ServiceSample
from beacon.core.service.status import STATUS_ORDER, ServiceStatus
from beacon.core.status_page import CLOSED_STATUSES
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_region.beacon_region import BeaconRegion
	from beacon.beacon.doctype.beacon_service_state.beacon_service_state import BeaconServiceState

# A reporter with no sample for this long counts as bad only under a firing cause alert.
SILENT_REPORTER = timedelta(minutes=3)


class RegionMeasurementContext:
	"""What every service measurement of one region needs, loaded once for one minute."""

	def __init__(self, region: BeaconRegion, now: datetime):
		self.region = region
		self.now = now
		self.registry = ResourceRegistry(region.name)
		self.clickhouse = ClickHouseClient(region)
		self.cause_alert_resources = set(
			frappe.get_all(
				"Beacon Alert",
				filters={
					"region": region.name,
					"status": "Firing",
					"rule": ("not in", AlertRules.get_burn_rule_names() or {""}),
				},
				pluck="resource",
			)
		)
		self.excluded_matches = self._load_excluded_matches()

	def is_under_cause_alert(self, resource: frappe._dict) -> bool:
		return any(item.name in self.cause_alert_resources for item in self.registry.get_ancestors(resource))

	def is_excluded(self, resource: frappe._dict) -> bool:
		"""True inside a Public maintenance that was announced before it started."""
		return any(match.covers(resource) for match in self.excluded_matches)

	def _load_excluded_matches(self) -> list[ResourceMatch]:
		incidents = frappe.get_all(
			"Beacon Incident",
			filters={
				"type": "Maintenance",
				"status": "In Progress",
				"visibility": "Public",
				"excluded_from_slo": 1,
			},
			pluck="name",
		)
		if not incidents:
			return []

		rows = frappe.get_all(
			"Beacon Incident Scope",
			filters={
				"parent": ("in", incidents),
				"parenttype": "Beacon Incident",
				"region": self.region.name,
			},
			fields=["region", "resource_type", "resource_id", "include_descendants"],
		)
		return [ResourceMatch.from_row(row) for row in rows]


class ServiceMeasurement:
	"""Measures one service in one region for one minute and keeps its status, budget and burn alerts."""

	def __init__(self, service: frappe._dict, context: RegionMeasurementContext):
		self.service = service
		self.context = context
		self.region = context.region.name
		self.now = context.now
		self.minute = self.now.replace(second=0, microsecond=0)
		self.filters = {"service": service.name, "region": self.region}

	def measure(self) -> None:
		"""Query ClickHouse first, then lock the Service State and write. A minute is counted once."""
		if self._is_counted():
			return

		sample = self._take_sample()
		RecordTransaction.start_new()
		self.state = self._load_state()
		if self._is_counted():
			return

		impact = self._get_published_impact()
		ServiceStatus(self.state, self.minute).apply(self._compute_status(sample, impact))
		if sample and sample.valid:
			# The day history counts a declared outage too. The SLO budget counts only good and valid.
			status = ServiceStatus.with_impact(self.state.status, impact)
			ServiceHistory(self.service.name, self.region).record(self.minute, sample, status)

		self.state.checked_at = self.now
		self.state.last_counted_minute = self.minute
		self.state.last_ratio = sample.ratio if sample else None
		# Incident matching reads them to pair a burn alert with the incident of its cause.
		self.state.bad_reporters = "\n".join(sample.bad_resources) if sample else ""
		self._reconcile_burn_alerts()
		self.state.save(ignore_permissions=True)

	def _is_counted(self) -> bool:
		counted = frappe.db.get_value("Beacon Service State", self.filters, "last_counted_minute")
		return bool(counted) and get_datetime(counted) >= self.minute

	def _take_sample(self) -> ServiceSample | None:
		"""Sum the SLI rows of checked reporters. None when the query fails or returns a bad row."""
		try:
			return self._sum_rows(self.context.clickhouse.query(self.service.sli_query))
		except ClickHouseError, KeyError, TypeError, ValueError:
			frappe.log_error(
				f"Beacon SLI query failed in {self.region}",
				reference_doctype="Beacon Alert Rule",
				reference_name=self.service.name,
			)
			return None

	def _sum_rows(self, rows: list[dict]) -> ServiceSample:
		registry = self.context.registry
		sample = ServiceSample()
		for row in rows:
			resource = registry.find(str(row["resource_id"]))
			if (
				not resource
				or not registry.is_evaluated(resource, self.now)
				or self.context.is_excluded(resource)
			):
				continue

			good, valid = int(row["good"] or 0), int(row["valid"] or 0)
			if get_datetime(row["last_ts"]) < self.now - SILENT_REPORTER:
				if not self.context.is_under_cause_alert(resource):
					continue
				good = 0

			sample.good += good
			sample.valid += valid
			sample.reporters += 1
			if good < valid:
				sample.bad_resources.append(resource.name)
		return sample

	def _compute_status(self, sample: ServiceSample | None, impact: str) -> str:
		if impact == "Maintenance":
			return "Maintenance"
		if self.context.region.telemetry_state != "OK" or not sample or sample.ratio is None:
			return "Unknown"
		return ServiceStatus.from_ratio(sample.ratio, self.service.major_outage_below)

	def _reconcile_burn_alerts(self) -> None:
		policy = BurnPolicy(self.service, self.region, self.minute)
		self.state.burn_rates = policy.burn_rates_label
		self.state.budget_left = policy.get_budget_left()

		registry = self.context.registry
		reconciler = AlertReconciler.for_rule(self.service.name, registry, self.now)
		reconciler.reconcile(policy.get_failures(registry.region_resource))

	def _get_published_impact(self) -> str:
		"""The worst impact on this service in this region of the open Public incidents and maintenances."""
		incident = frappe.qb.DocType("Beacon Incident")
		impact = frappe.qb.DocType("Beacon Incident Service")
		impacts = (
			frappe.qb.from_(incident)
			.join(impact)
			.on(impact.parent == incident.name)
			.select(impact.impact)
			.distinct()
			.where(
				(incident.visibility == "Public")
				& incident.status.notin((*CLOSED_STATUSES, "Scheduled"))
				& (impact.service == self.service.name)
				& ((impact.region == self.region) | impact.region.isnull() | (impact.region == ""))
			)
			.run(pluck=True)
		)
		return max(impacts, key=STATUS_ORDER.index, default="Operational")

	def _load_state(self) -> BeaconServiceState:
		name = frappe.db.get_value("Beacon Service State", self.filters, for_update=True)
		if name:
			return frappe.get_doc("Beacon Service State", name, for_update=True)
		return frappe.get_doc({"doctype": "Beacon Service State", **self.filters, "status": "Unknown"})
