from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import frappe

QUEUE = "long"
# Slow region runs fill the long queue. A page must not wait behind them.
NOTIFICATION_QUEUE = "short"


def run_as_admin[Result](function: Callable[..., Result]) -> Callable[..., Result]:
	"""Run one scheduled or queued job as Administrator."""

	@functools.wraps(function)
	def wrapped(*args: Any, **kwargs: Any) -> Result:
		if getattr(frappe.local, "request", None) is not None:
			raise RuntimeError("Background jobs cannot run during an HTTP request.")
		previous_user = frappe.session.user
		frappe.set_user("Administrator")  # nosemgrep
		try:
			return function(*args, **kwargs)
		finally:
			frappe.set_user(previous_user)  # nosemgrep

	return wrapped


def queue_region_evaluation(region: str, evaluation: str | None = None) -> None:
	frappe.enqueue(
		"beacon.core.jobs.evaluate_region",
		queue=QUEUE,
		job_id=f"beacon-evaluate:{region}:{evaluation or 'due'}",
		deduplicate=True,
		region=region,
		evaluation=evaluation,
	)


def queue_region_schedule(region: str) -> None:
	frappe.enqueue(
		"beacon.core.jobs.run_region_schedule",
		queue=QUEUE,
		job_id=f"beacon-region-minute:{region}",
		deduplicate=True,
		region=region,
	)


def queue_source_sync(source: str) -> None:
	frappe.enqueue_doc(
		"Beacon Resource Source",
		source,
		"sync",
		queue=QUEUE,
		job_id=f"beacon-sync-source:{source}",
		deduplicate=True,
		use_etag=True,
	)


def queue_notification(notification: str) -> None:
	frappe.enqueue(
		"beacon.core.jobs.deliver_notification",
		queue=NOTIFICATION_QUEUE,
		job_id=f"beacon-notification:{notification}",
		deduplicate=True,
		enqueue_after_commit=True,
		notification=notification,
	)


def queue_severity_raise(alert: str) -> None:
	frappe.enqueue(
		"beacon.core.jobs.raise_incident_severity",
		queue=NOTIFICATION_QUEUE,
		enqueue_after_commit=True,
		alert=alert,
	)


def queue_subscriber_update(incident: str, update: str) -> None:
	frappe.enqueue(
		"beacon.core.jobs.notify_subscribers",
		queue=QUEUE,
		enqueue_after_commit=True,
		incident=incident,
		update=update,
	)


@run_as_admin
def run_schedule() -> None:
	from beacon.core.scheduler import BeaconScheduler

	BeaconScheduler().run()


@run_as_admin
def run_region_schedule(region: str) -> None:
	from beacon.core.scheduler import RegionSchedule

	RegionSchedule(region).run()


@run_as_admin
def evaluate_region(region: str, evaluation: str | None = None) -> None:
	from beacon.core.alert.rule_evaluator import RegionEvaluation

	RegionEvaluation(region).run(evaluation)


@run_as_admin
def delete_old_rows() -> None:
	from beacon.core.scheduler import RecordRetention

	RecordRetention().apply()


@run_as_admin
def remind_postmortems() -> None:
	from beacon.core.incident.postmortem import Postmortem

	Postmortem.remind_missing()


@run_as_admin
def notify_subscribers(incident: str, update: str) -> None:
	from beacon.core.incident.subscription import IncidentSubscription

	IncidentSubscription(incident).notify(update)


@run_as_admin
def raise_incident_severity(alert: str) -> None:
	from beacon.core.incident.coordinator import IncidentCoordinator

	IncidentCoordinator.raise_severity(alert)


@run_as_admin
def deliver_notification(notification: str) -> None:
	from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification

	frappe.get_doc("Beacon Notification", notification).send()


@run_as_admin
def sync_sources() -> None:
	from beacon.beacon.doctype.beacon_resource_source.beacon_resource_source import BeaconResourceSource

	BeaconResourceSource.queue_due_syncs()


@run_as_admin
def evaluate_due_rules() -> None:
	from beacon.core.alert.rule_evaluator import RegionEvaluation

	RegionEvaluation.queue_due()
