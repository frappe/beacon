from __future__ import annotations

import time
from datetime import datetime, timedelta

import frappe
from frappe.utils import now_datetime

from beacon.core.alert.reconciler import AlertChanges, AlertReconciler, RuleFailure
from beacon.core.alert.rules import AlertRules
from beacon.core.alert.states import SEVERITIES
from beacon.core.exceptions import ClickHouseError, RuleContractError
from beacon.core.incident.coordinator import IncidentCoordinator
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.incident.recovery import IncidentRecovery
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.regions import RegionSelection
from beacon.core.resource.registry import ResourceRegistry
from beacon.core.transaction import RecordTransaction

MAX_ROWS = 50_000
BROKEN_AFTER_ERRORS = 3
ROW_COLUMNS = ("resource_id", "alert_key", "value", "detail")
RULE_FIELDS = (
	"name",
	"title",
	"severity",
	"resource_type",
	"query",
	"interval",
	"pending_for",
	"resolve_after",
)


class RegionEvaluation:
	"""Runs every due rule of one region, one at a time, so a slow region cannot hold every worker."""

	def __init__(self, region: str):
		self.region = frappe.get_doc("Beacon Region", region)
		self.now = now_datetime()

	@staticmethod
	def queue_due() -> None:
		"""Queue one job for each active region with due evaluations."""
		from beacon.core.jobs import queue_region_evaluation

		now = now_datetime()
		for region in RegionSelection.get_active():
			if RegionEvaluation.get_due_evaluations(region, now):
				queue_region_evaluation(region)

	@staticmethod
	def get_due_evaluations(region: str, now: datetime) -> list[str]:
		evaluation = frappe.qb.DocType("Beacon Rule Evaluation")
		rule = frappe.qb.DocType("Beacon Alert Rule")
		return (
			frappe.qb.from_(evaluation)
			.join(rule)
			.on(rule.name == evaluation.rule)
			.select(evaluation.name)
			.where((evaluation.region == region) & (rule.enabled == 1) & (evaluation.next_run_at <= now))
			.orderby(evaluation.next_run_at)
			.run(pluck=True)
		)

	def run(self, evaluation: str | None = None) -> None:
		names = [evaluation] if evaluation else self.get_due_evaluations(self.region.name, self.now)
		if self.region.telemetry_state == "Unreachable":
			self._postpone(names)
			return

		registry = ResourceRegistry(self.region.name)
		clickhouse = ClickHouseClient(self.region)
		changes = AlertChanges()

		def evaluate(name: str) -> None:
			# Run Now and the scheduled run can both reach one evaluation. One runs at a time.
			with frappe.db.advisory_lock(f"{frappe.db.cur_db_name}:beacon-evaluation:{name}", timeout=60):
				result = RuleEvaluator(name, registry, clickhouse).run()
				frappe.db.commit()  # nosemgrep: commit before the next run can take the lock
			if result:
				changes.firing_changes += result.firing_changes
				changes.recovered_incidents |= result.recovered_incidents

		RecordTransaction.run_each(
			"Beacon Rule Evaluation", names, f"Beacon evaluation failed in {self.region.name}", evaluate
		)
		IncidentRecovery.queue(changes.recovered_incidents)
		if changes.firing_changes:
			IncidentCoordinator(self.region.name).reconcile()

	def _postpone(self, names: list[str]) -> None:
		"""The region alert covers an Unreachable region. A postponed evaluation counts as a run."""

		def postpone(name: str) -> None:
			rule = frappe.db.get_value("Beacon Rule Evaluation", name, "rule")
			interval = frappe.db.get_value("Beacon Alert Rule", rule, "interval")
			frappe.db.set_value(
				"Beacon Rule Evaluation",
				name,
				{"last_run_at": self.now, "next_run_at": self.now + timedelta(seconds=interval)},
			)

		RecordTransaction.run_each(
			"Beacon Rule Evaluation",
			names,
			f"Beacon could not postpone an evaluation in {self.region.name}",
			postpone,
		)


class RuleEvaluator:
	"""Runs one rule in one region and records the result on its Rule Evaluation."""

	def __init__(self, evaluation: str, registry: ResourceRegistry, clickhouse: ClickHouseClient):
		self.evaluation_name = evaluation
		self.registry = registry
		self.clickhouse = clickhouse
		rule = frappe.db.get_value("Beacon Rule Evaluation", evaluation, "rule")
		self.rule = frappe.db.get_value("Beacon Alert Rule", rule, RULE_FIELDS, as_dict=True)
		self.now = now_datetime()

	def run(self) -> AlertChanges | None:
		"""Evaluate once. A query or contract error changes no alert. The query runs before any lock is taken."""
		started = time.monotonic()
		try:
			rows = RuleQuery(self.clickhouse, self.rule.query).run()
			error = None
		except (ClickHouseError, RuleContractError) as query_error:
			rows, error = [], str(query_error)

		RecordTransaction.start_new()
		self.evaluation = frappe.get_doc("Beacon Rule Evaluation", self.evaluation_name, for_update=True)
		self.evaluation.last_run_at = self.now
		self.evaluation.next_run_at = self.now + timedelta(seconds=self.rule.interval)
		if error:
			self._record_error(error)
			return None

		reconciler = AlertReconciler(self.rule, self.registry, self.now)
		if self._has_gap():
			reconciler.reset_timers()

		failing, unknown = self._get_failing_rows(rows)
		changes = reconciler.reconcile(failing)

		self.evaluation.update(
			{
				"state": "Healthy",
				"consecutive_errors": 0,
				"last_error": None,
				"last_success_at": self.now,
				"last_duration_ms": int((time.monotonic() - started) * 1000),
				"last_row_count": len(rows),
				"unknown_rows": unknown,
			}
		)
		self.evaluation.save(ignore_permissions=True)
		return changes

	def _get_failing_rows(self, rows: list[dict]) -> tuple[list[RuleFailure], int]:
		failing: list[RuleFailure] = []
		unknown = 0
		seen: set[tuple[str, str]] = set()
		# Any token in the region can send any metric, so a row for a resource of another type is not trusted.
		resource_types = AlertRules.get_resource_types(self.rule.resource_type)
		for row in rows:
			resource = self.registry.find(row["resource_id"])
			if not resource or (resource_types and resource.resource_type not in resource_types):
				unknown += 1
				continue

			key = (resource.name, row["alert_key"])
			if key in seen or not self.registry.is_evaluated(resource, self.now):
				continue

			seen.add(key)
			failing.append(RuleFailure(resource, key[1], row["value"], row["detail"], row["severity"]))
		return failing, unknown

	def _has_gap(self) -> bool:
		last_success = self.evaluation.last_success_at
		return bool(last_success) and self.now - last_success > timedelta(seconds=3 * self.rule.interval)

	def _record_error(self, error: str) -> None:
		"""Log and post only when the state changes, so a rule that keeps failing does not flood the logs."""
		evaluation = self.evaluation
		previous_state = evaluation.state
		evaluation.consecutive_errors += 1
		evaluation.last_error = error[:5000]
		evaluation.state = "Broken" if evaluation.consecutive_errors >= BROKEN_AFTER_ERRORS else "Erroring"
		evaluation.save(ignore_permissions=True)
		if evaluation.state == previous_state:
			return

		frappe.log_error(
			f"Beacon rule {self.rule.name} is {evaluation.state} in {self.registry.region}",
			error,
			reference_doctype="Beacon Rule Evaluation",
			reference_name=evaluation.name,
		)
		if evaluation.state == "Broken":
			NotificationOutbox().post_to_chat(
				f"Rule {self.rule.name} is broken in {self.registry.region}: {error[:500]}"
			)


class RuleQuery:
	"""Runs a rule query and checks its result contract."""

	def __init__(self, clickhouse: ClickHouseClient, query: str):
		self.clickhouse = clickhouse
		self.query = query

	def run(self) -> list[dict]:
		"""Check the row limit, the columns and types of every row, and that no resource_id and alert_key repeats.
		A bad row fails the whole rule, so it can never resolve an alert."""
		rows = self.clickhouse.query(self.query)
		if len(rows) > MAX_ROWS:
			raise RuleContractError(f"The query returned {len(rows)} rows. The limit is {MAX_ROWS}.")

		checked = [self._check_row(row) for row in rows]
		keys = [(row["resource_id"], row["alert_key"]) for row in checked]
		if len(set(keys)) != len(keys):
			raise RuleContractError("The query returned one resource_id and alert_key more than once.")
		return checked

	@staticmethod
	def _check_row(row: dict) -> dict:
		if not set(ROW_COLUMNS) <= row.keys():
			raise RuleContractError(
				f"The query must return {', '.join(ROW_COLUMNS)}. It returned {', '.join(row)}."
			)
		try:
			value = float(row["value"] or 0)
		except (TypeError, ValueError) as error:
			raise RuleContractError(f"value must be a number. It was {row['value']!r}.") from error
		severity = row.get("severity") or None
		if severity is not None and severity not in SEVERITIES:
			raise RuleContractError(f"severity must be one of {', '.join(SEVERITIES)}. It was {severity!r}.")
		return {
			"resource_id": str(row["resource_id"]),
			"alert_key": str(row["alert_key"] or ""),
			"value": value,
			"detail": str(row["detail"] or ""),
			"severity": severity,
		}
