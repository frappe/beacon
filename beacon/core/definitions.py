from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model import table_fields
from frappe.model.document import Document
from frappe.utils import now_datetime

from beacon.core.alert.endpoint_check import ENDPOINT_KINDS
from beacon.core.alert.evaluation_schedule import EvaluationSchedule
from beacon.core.alert.rule_evaluator import RuleQuery
from beacon.core.alert.rules import AlertRules
from beacon.core.exceptions import ClickHouseError, DefinitionSourceError, RuleContractError
from beacon.core.github_client import GitHubClient
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.regions import RegionSelection
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_definition_source.beacon_definition_source import (
		BeaconDefinitionSource,
	)
	from beacon.beacon.doctype.beacon_settings.beacon_settings import BeaconSettings

HEADER_LINE = re.compile(r"^--\s*([a-z_]+):\s*(.*?)\s*$")
DEFINITION_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")

RULE_HEADER_FIELDS = {
	"title": str,
	"description": str,
	"severity": str,
	"resource_type": str,
	"runbook_url": str,
	"interval": int,
	"pending_for": int,
	"resolve_after": int,
	"scope_includes_descendants": int,
}
ENDPOINT_HEADER_FIELDS = {
	**{
		name: kind
		for name, kind in RULE_HEADER_FIELDS.items()
		if name not in ("interval", "resource_type", "scope_includes_descendants")
	},
	"timeout": int,
	"region": str,
}
HTTP_HEADER_FIELDS = {**ENDPOINT_HEADER_FIELDS, "url": str, "expected_status": int, "expected_text": str}
TLS_HEADER_FIELDS = {**ENDPOINT_HEADER_FIELDS, "host": str, "minimum_days_left": int}
# The region header of an HTTP or TLS file names one region, or all for every active region. A file can leave it out, and Desk sets the Regions.
ALL_REGIONS = "all"
# A rule that pages must tell the responder what to do.
PAGING_SEVERITIES = ("High", "Critical")
SERVICE_HEADER_FIELDS = {
	"title": str,
	"description": str,
	"scope": str,
	"query_region": str,
	"slo_target": float,
	"sla_target": float,
	"major_outage_below": float,
	"show_on_status_page": int,
	"sort_order": int,
	"page_on_burn": int,
	"measurement": str,
	"status_page_group": str,
}

LAYOUT_FIELD_TYPES = ("Tab Break", "Section Break", "Column Break", "HTML")
# The fields that Desk can change on a record from a file. The query region of a global service and the regions of an endpoint check depend on the site, so a file can leave them out.
DESK_FIELDS = {"Beacon Alert Rule": ("enabled",), "Beacon Service": ("enabled", "query_region")}
ENDPOINT_DESK_FIELDS = ("enabled", "regions")

BUILT_IN_RULES = (
	{
		"rule_key": "region-unreachable",
		"title": "Beacon cannot read metrics",
		"description": "Beacon cannot reach the region, so its hosts, VMs and sites are not watched.",
		"severity": "Critical",
		"resource_type": "Region",
		"pending_for": 0,
		"resolve_after": 0,
	},
)


class DefinitionFile:
	"""One .sql file of a source: a `-- key: value` header and the query."""

	def __init__(self, path: str, text: str, header_fields: dict[str, type]):
		self.path = path
		self.key = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
		self.query = text
		if not DEFINITION_KEY.match(self.key):
			raise DefinitionSourceError(f"{path}: use lowercase letters, digits and - in the file name.")

		self.values = {}
		for line in text.splitlines():
			match = HEADER_LINE.match(line)
			if not match:
				break
			name, value = match.groups()
			if name not in header_fields:
				raise DefinitionSourceError(f"{path}: unknown header {name}")
			try:
				self.values[name] = header_fields[name](value)
			except ValueError as error:
				raise DefinitionSourceError(f"{path}: bad value for {name}") from error


class DefinitionSync:
	"""Reads the rule and service files of every definition source and applies them. Runs only on demand."""

	def __init__(self, settings: BeaconSettings):
		self.settings = settings
		self.client = GitHubClient(settings.get_password("github_token", raise_exception=False))

	def run(self) -> list[dict]:
		lock = f"{frappe.db.cur_db_name}:beacon-definition-sync"
		with frappe.db.advisory_lock(lock, timeout=10):
			repositories = {row.repository for row in self.settings.definition_sources}
			results = [
				SourceSync(row, self.client, repositories).run() for row in self.settings.definition_sources
			]
			RecordTransaction("Beacon Settings", "Beacon Settings").retry(EvaluationSchedule().sync)
		return results


class SourceSync:
	"""Syncs one repository. A bad file stops the whole source before anything is written."""

	def __init__(self, source: BeaconDefinitionSource, client: GitHubClient, repositories: set[str]):
		self.source = source
		self.client = client
		self.repositories = repositories
		self.repository = source.repository
		self.path = source.path.strip("/")
		self.errors: list[str] = []

	def run(self) -> dict:
		RecordTransaction.start_new()  # no lock is held during the GitHub and ClickHouse calls
		try:
			commit = self.client.get_commit(self.repository, self.source.branch)
			rule_paths = self._list(commit, "rules")
			sql_rules = self._read(commit, rule_paths, ".sql", RULE_HEADER_FIELDS)
			http_rules = self._read(commit, rule_paths, ".http", HTTP_HEADER_FIELDS)
			tls_rules = self._read(commit, rule_paths, ".tls", TLS_HEADER_FIELDS)
			rules = sql_rules + http_rules + tls_rules
			services = self._read(commit, self._list(commit, "services"), ".sql", SERVICE_HEADER_FIELDS)
			if not rules and not services:
				# A wrong path or branch must not turn off every rule of the source.
				raise DefinitionSourceError(
					f"No rule or service files in {self.path or 'the root'}/rules or /services."
				)
			self._check_endpoint_rules(http_rules, "url")
			self._check_endpoint_rules(tls_rules, "host")
			self._check_owners("Beacon Alert Rule", rules)
			self._check_owners("Beacon Service", services)
			self._check_queries(sql_rules)
		except DefinitionSourceError as error:
			frappe.log_error(f"Beacon definition sync failed for {self.repository}")
			return self._record(None, str(error))

		for rule in sql_rules:
			self._apply(
				"Beacon Alert Rule",
				rule.key,
				{**rule.values, "kind": "SQL", "query": rule.query, "regions": []},
			)
		self._apply_endpoint_rules("HTTP", http_rules)
		self._apply_endpoint_rules("TLS", tls_rules)
		for service in services:
			self._apply(
				"Beacon Service",
				service.key,
				{
					"measurement": "Measured",
					"status_page_group": "",
					**service.values,
					"sli_query": service.query,
				},
			)
		removed = self._prune("Beacon Alert Rule", rules) + self._prune("Beacon Service", services)

		summary = f"{len(rules)} rules, {len(services)} services, {removed} removed"
		return self._record(commit, "\n".join([summary, *self._get_warnings(rules), *self.errors]))

	def _list(self, commit: str, folder: str) -> list[str]:
		directory = f"{self.path}/{folder}" if self.path else folder
		return sorted(self.client.list_files(self.repository, commit, directory))

	def _read(
		self, commit: str, paths: list[str], extension: str, header_fields: dict[str, type]
	) -> list[DefinitionFile]:
		return [
			DefinitionFile(path, self.client.read_file(self.repository, commit, path), header_fields)
			for path in paths
			if path.endswith(extension)
		]

	def _check_endpoint_rules(self, rules: list[DefinitionFile], target: str) -> None:
		for rule in rules:
			missing = [name for name in ("title", "severity", target) if not rule.values.get(name)]
			if missing:
				raise DefinitionSourceError(f"{rule.path}: add the header {', '.join(missing)}")

	def _get_warnings(self, rules: list[DefinitionFile]) -> list[str]:
		unmeasured = frappe.get_all(
			"Beacon Service",
			filters={
				"source": self.repository,
				"scope": "Global",
				"measurement": "Measured",
				"query_region": ("is", "not set"),
			},
			pluck="name",
		)
		unplaced = [
			rule.key
			for rule in rules
			if rule.path.endswith((".http", ".tls"))
			and "region" not in rule.values
			and not frappe.get_all(
				"Beacon Region Item", filters={"parent": rule.key, "parenttype": "Beacon Alert Rule"}, limit=1
			)
		]
		known_types = set(frappe.get_all("Beacon Resource", pluck="resource_type", distinct=True))
		return (
			[
				f"{rule.path}: no resource has the type {name}. Check resource_type: Beacon drops rows of other types."
				for rule in rules
				for name in sorted(
					AlertRules.get_resource_types(rule.values.get("resource_type")) - known_types
				)
			]
			+ [
				f"{rule.path}: a {rule.values['severity']} rule pages, so add a runbook_url."
				for rule in rules
				if rule.values.get("severity") in PAGING_SEVERITIES and not rule.values.get("runbook_url")
			]
			+ [f"{name}: a global service needs a Query Region. Set it in Desk." for name in unmeasured]
			+ [
				f"{name}: the file has no region. Set the Regions in Desk, then turn the rule on."
				for name in unplaced
			]
		)

	def _check_owners(self, doctype: str, files: list[DefinitionFile]) -> None:
		"""A name belongs to one source. Built-in and burn rules have no source, so a file cannot take them.

		A Custom rule has no owner yet. A source takes a name over when the old source removed its file or is no longer configured, so a rule can move between repositories.
		"""
		owners = {
			row.name: row.source
			for row in frappe.get_all(
				doctype,
				filters={"name": ("in", [file.key for file in files] or [""])},
				fields=["name", "source", "is_removed"],
			)
			if not row.source or (row.source in self.repositories and not row.is_removed)
		}
		custom = set(
			frappe.get_all(doctype, filters={"is_custom": 1}, pluck="name")
			if doctype == "Beacon Alert Rule"
			else []
		)
		keys = [file.key for file in files]
		for file in files:
			if keys.count(file.key) > 1:
				raise DefinitionSourceError(f"{file.path}: another file has the name {file.key}.")
			if file.key in owners and owners[file.key] != self.repository and file.key not in custom:
				owner = owners[file.key] or "Beacon"
				raise DefinitionSourceError(f"{file.path}: the name {file.key} belongs to {owner}.")

	def _check_queries(self, rules: list[DefinitionFile]) -> None:
		"""Run each new or changed rule query once, in the first active region."""
		saved = dict(
			frappe.get_all(
				"Beacon Alert Rule",
				filters={"name": ("in", [rule.key for rule in rules] or [""])},
				fields=["name", "query"],
				as_list=True,
			)
		)
		changed = [rule for rule in rules if saved.get(rule.key) != rule.query]
		regions = RegionSelection.get_active()
		if not changed or not regions:
			return

		clickhouse = ClickHouseClient(frappe.get_doc("Beacon Region", regions[0]))
		for rule in changed:
			try:
				RuleQuery(clickhouse, rule.query).run()
			except (ClickHouseError, RuleContractError) as error:
				raise DefinitionSourceError(
					f"{rule.path}: the query failed in {regions[0]}: {error}"
				) from error

	def _apply_endpoint_rules(self, kind: str, rules: list[DefinitionFile]) -> None:
		for rule in rules:
			values = {name: value for name, value in rule.values.items() if name != "region"}
			region = rule.values.get("region")
			if region:
				values["regions"] = [] if region == ALL_REGIONS else [{"region": region}]
			elif not frappe.db.exists("Beacon Alert Rule", rule.key):
				values["enabled"] = 0  # empty Regions means every region, so the check waits for Desk
			self._apply("Beacon Alert Rule", rule.key, {**values, "kind": kind})

	def _apply(self, doctype: str, name: str, values: dict) -> None:
		def save() -> None:
			if frappe.db.exists(doctype, name):
				doc = frappe.get_doc(doctype, name, for_update=True)
				if doc.is_removed:
					doc.enabled = 1
			else:
				doc = frappe.new_doc(doctype)
			if doctype == "Beacon Alert Rule":
				doc.update({"rule_key": name, "is_custom": 0})
			else:
				doc.service_key = name
			doc.update({**values, "source": self.repository, "is_removed": 0})
			doc.flags.loading_definitions = True
			doc.save(ignore_permissions=True)

		self._run(doctype, name, save)

	def _prune(self, doctype: str, files: list[DefinitionFile]) -> int:
		"""Turn off what this source no longer has. Keep the record for its alert history."""
		names = frappe.get_all(
			doctype,
			filters={
				"source": self.repository,
				"is_removed": 0,
				"name": ("not in", [file.key for file in files] or [""]),
			},
			pluck="name",
		)
		for name in names:

			def remove(name: str = name) -> None:
				doc = frappe.get_doc(doctype, name, for_update=True)
				doc.update({"is_removed": 1, "enabled": 0})
				doc.flags.loading_definitions = True
				doc.save(ignore_permissions=True)

			self._run(doctype, name, remove)
		return len(names)

	def _run(self, doctype: str, name: str, work: Callable[[], None]) -> None:
		"""Apply one record. A failure is logged and shown in the result; the other records go on."""
		try:
			RecordTransaction(doctype, name).retry(work)
		except Exception as error:
			frappe.db.rollback()
			frappe.log_error(
				f"Beacon definition sync failed for {self.repository}",
				reference_doctype=doctype,
				reference_name=name,
			)
			self.errors.append(f"{name}: {error}")

	def _record(self, commit: str | None, result: str) -> dict:
		values = {"last_synced_at": now_datetime(), "result": result}
		if commit:
			values["commit"] = commit[:12]
		RecordTransaction("Beacon Definition Source", self.source.name).retry(
			lambda: frappe.db.set_value("Beacon Definition Source", self.source.name, values)
		)
		return {"repository": self.repository, **values}


def create_built_in_rules() -> None:
	"""Run after install and after migrate. Built-in rules live in code, not in a definition source."""
	for rule in BUILT_IN_RULES:
		name = rule["rule_key"]
		doc = (
			frappe.get_doc("Beacon Alert Rule", name)
			if frappe.db.exists("Beacon Alert Rule", name)
			else frappe.new_doc("Beacon Alert Rule")
		)
		doc.update({**rule, "kind": "Built In"})
		doc.flags.loading_definitions = True
		doc.save(ignore_permissions=True)
	EvaluationSchedule().sync()


def check_desk_change(doc: Document) -> None:
	"""Rules and services come from definition sources, where Desk can change only DESK_FIELDS. A rule made in Desk is Custom."""
	if doc.flags.loading_definitions:
		return
	if doc.is_new() and doc.meta.has_field("is_custom"):
		doc.is_custom = 1
	if doc.get("is_custom"):
		return
	if doc.is_new():
		frappe.throw(_("Add a file to a definition source, then sync it in Beacon Settings."))
	editable = ENDPOINT_DESK_FIELDS if doc.get("kind") in ENDPOINT_KINDS else DESK_FIELDS[doc.doctype]
	if any(
		_has_value_changed(doc, df)
		for df in doc.meta.fields
		if df.fieldtype not in LAYOUT_FIELD_TYPES and df.fieldname not in editable
	):
		labels = ", ".join(_(doc.meta.get_label(field)) for field in editable)
		frappe.throw(
			_("This comes from {0}. You can only change {1} here.").format(doc.source or "Beacon", labels)
		)


def _has_value_changed(doc: Document, df) -> bool:
	"""Frappe compares table rows by identity, so compare their values."""
	if df.fieldtype not in table_fields:
		return doc.has_value_changed(df.fieldname)

	def rows(record: Document) -> list[dict]:
		return [
			row.as_dict(no_default_fields=True, no_child_table_fields=True)
			for row in record.get(df.fieldname)
		]

	return rows(doc) != rows(doc.get_doc_before_save())
