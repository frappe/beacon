from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import frappe
import requests

from beacon.core.alert.reconciler import AlertReconciler, RuleFailure
from beacon.core.incident.recovery import IncidentRecovery
from beacon.core.resource.registry import ResourceRegistry
from beacon.core.transaction import RecordTransaction

# Checks run in parallel, so one slow URL delays the region run by its timeout at most.
MAXIMUM_PARALLEL_CHECKS = 8
DETAIL_CHARACTERS = 500


class HttpChecks:
	"""The HTTP rules of one region. Beacon calls each URL, then raises or resolves its alert on the region resource."""

	def __init__(self, region: str):
		self.region = region
		names = frappe.get_all(
			"Beacon Region Item",
			filters={"parenttype": "Beacon Alert Rule", "region": region},
			pluck="parent",
		)
		self.rules = frappe.get_all(
			"Beacon Alert Rule",
			filters={"kind": "HTTP", "enabled": 1, "name": ("in", names or [""])},
			fields=["name", "url", "expected_status", "expected_text", "timeout"],
		)

	def call(self) -> dict[str, str | None]:
		"""Return the failure of each rule, or None when its URL answers as expected. Call it with no row lock."""
		with ThreadPoolExecutor(max_workers=MAXIMUM_PARALLEL_CHECKS) as pool:
			failures = pool.map(self.get_failure, self.rules)
			return dict(zip([rule.name for rule in self.rules], failures, strict=True))

	def reconcile(self, failures: dict[str, str | None], registry: ResourceRegistry, now: datetime) -> None:
		def apply(rule: str) -> None:
			failure = failures[rule]
			rows = [RuleFailure(registry.region_resource, detail=failure)] if failure else []
			reconciler = AlertReconciler.for_rule(rule, registry, now)
			IncidentRecovery.queue(reconciler.reconcile(rows).recovered_incidents)

		RecordTransaction.run_each(
			"Beacon Alert Rule", failures, f"Beacon could not update an HTTP alert in {self.region}", apply
		)

	@staticmethod
	def get_failure(rule: frappe._dict) -> str | None:
		"""Runs in a worker thread, so it uses no Frappe call."""
		try:
			response = requests.get(rule.url, timeout=rule.timeout)
		except requests.RequestException as error:
			return f"{type(error).__name__}: {error}"[:DETAIL_CHARACTERS]

		if response.status_code != rule.expected_status:
			return f"HTTP {response.status_code}, expected {rule.expected_status}"
		if rule.expected_text and rule.expected_text not in response.text:
			return f"The response does not contain {rule.expected_text!r}"[:DETAIL_CHARACTERS]
		return None
