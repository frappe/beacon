from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident
from beacon.core.alert.endpoint_check import EndpointChecks
from beacon.core.alert.reconciler import AlertReconciler, RuleFailure
from beacon.core.alert.suppression import AlertSuppression
from beacon.core.heartbeat import Heartbeat
from beacon.core.incident.coordinator import IncidentCoordinator
from beacon.core.incident.escalation import IncidentEscalation
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.jobs import queue_region_schedule
from beacon.core.resource.region_probe import RegionProbe
from beacon.core.resource.regions import RegionSelection
from beacon.core.service.measurement import RegionMeasurementContext, ServiceMeasurement
from beacon.core.transaction import RecordTransaction

REGION_UNREACHABLE_RULE = "region-unreachable"

# Retention of the rows Beacon writes.
RESOLVED_ALERT_DAYS = 90
CLEARED_ALERT_DAYS = 7
NOTIFICATION_DAYS = 180
SERVICE_MINUTE_DAYS = 3
TERMINATED_RESOURCE_DAYS = 90


class RegionSchedule:
	"""The minute work of one region: probe, SLOs, suppression and the incident invariant."""

	def __init__(self, region: str):
		self.region = frappe.get_doc("Beacon Region", region)
		self.now = now_datetime()

	def run(self) -> None:
		"""Each step runs alone, so a failed step is logged and the next step still runs."""
		region = RecordTransaction("Beacon Region", self.region.name)
		region.run(lambda: RegionProbe(self.region).check(), "Beacon region probe failed")

		context = RegionMeasurementContext(self.region, self.now)
		region.run(
			lambda: self._reconcile_unreachable_alert(context), "Beacon could not update the region alert"
		)
		region.run(lambda: self._run_endpoint_checks(context), "Beacon could not run the HTTP and TLS checks")
		services = {service.name: service for service in self._get_services()}
		RecordTransaction.run_each(
			"Beacon Service",
			services,
			f"Beacon SLO failed in {self.region.name}",
			lambda name: ServiceMeasurement(services[name], context).measure(),
		)
		region.run(
			lambda: AlertSuppression(context.registry, self.now).refresh_open_alerts(),
			"Beacon could not refresh alert suppression",
		)
		region.run(
			lambda: IncidentCoordinator(self.region.name).reconcile(), "Beacon could not coordinate incidents"
		)

	def _reconcile_unreachable_alert(self, context: RegionMeasurementContext) -> None:
		rows = []
		if self.region.telemetry_state == "Unreachable":
			rows = [RuleFailure(context.registry.region_resource, detail="ClickHouse probe failed")]
		AlertReconciler.for_rule(REGION_UNREACHABLE_RULE, context.registry, self.now).reconcile(rows)

	def _run_endpoint_checks(self, context: RegionMeasurementContext) -> None:
		"""The run starts a new transaction first, so no lock is held during the HTTP and TLS calls."""
		checks = EndpointChecks(self.region.name)
		checks.reconcile(checks.call(), context.registry, self.now)

	def _get_services(self) -> list[frappe._dict]:
		services = frappe.get_all(
			"Beacon Service",
			filters={"enabled": 1, "measurement": "Measured"},
			fields=[
				"name",
				"scope",
				"query_region",
				"sli_query",
				"slo_target",
				"sla_target",
				"page_on_burn",
				"major_outage_below",
			],
		)
		selection = RegionSelection("Beacon Service")

		def is_measured_here(service: frappe._dict) -> bool:
			if service.scope == "Global":
				return service.query_region == self.region.name
			return self.region.name in selection.get_regions(service.name)

		return [service for service in services if is_measured_here(service)]


class BeaconScheduler:
	"""Coordinates the work due across regions."""

	def run(self) -> None:
		"""Scheduled every minute: time-based work, then one job for each region. Each step runs alone."""
		BeaconIncident.advance_maintenances()
		settings = RecordTransaction("Beacon Settings", "Beacon Settings")
		settings.run(self._resolve_disabled_alerts, "Beacon could not resolve disabled alerts")
		for region in RegionSelection.get_active():
			queue_region_schedule(region)

		BeaconIncident.close_recovered()
		IncidentEscalation.step_due()
		NotificationOutbox().requeue_waiting()
		Heartbeat().send()

	def _resolve_disabled_alerts(self) -> None:
		"""Close open alerts of disabled rules and disabled regions."""
		disabled_rules = frappe.get_all("Beacon Alert Rule", filters={"enabled": 0}, pluck="name")
		if disabled_rules:
			BeaconAlert.resolve_open("Disabled", rule=("in", disabled_rules))

		disabled_regions = frappe.get_all("Beacon Region", filters={"status": "Disabled"}, pluck="name")
		if disabled_regions:
			BeaconAlert.resolve_open("Disabled", region=("in", disabled_regions))


class RecordRetention:
	"""Removes records after their configured retention periods."""

	def apply(self) -> None:
		"""Scheduled daily: apply the retention of the rows Beacon writes."""
		now = now_datetime()
		frappe.db.delete(
			"Beacon Alert",
			{
				"status": "Resolved",
				"resolution": "Cleared",
				"resolved_at": ("<", now - timedelta(days=CLEARED_ALERT_DAYS)),
			},
		)
		frappe.db.delete(
			"Beacon Alert",
			{"status": "Resolved", "resolved_at": ("<", now - timedelta(days=RESOLVED_ALERT_DAYS))},
		)
		frappe.db.delete("Beacon Notification", {"creation": ("<", now - timedelta(days=NOTIFICATION_DAYS))})
		frappe.db.delete(
			"Beacon Service Minute", {"minute": ("<", now - timedelta(days=SERVICE_MINUTE_DAYS))}
		)
		frappe.db.delete(
			"Beacon Resource",
			{"status": "Terminated", "terminated_at": ("<", now - timedelta(days=TERMINATED_RESOURCE_DAYS))},
		)
