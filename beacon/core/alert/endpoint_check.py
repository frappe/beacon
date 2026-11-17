from __future__ import annotations

import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlsplit

import frappe
import requests

from beacon.core.alert.reconciler import AlertReconciler, RuleFailure
from beacon.core.exceptions import EndpointVariableError
from beacon.core.resource.regions import RegionSelection
from beacon.core.resource.registry import ResourceRegistry
from beacon.core.transaction import RecordTransaction

# Checks run in parallel, so one slow endpoint delays the region run by its timeout at most.
MAXIMUM_PARALLEL_CHECKS = 8
DETAIL_CHARACTERS = 500
ENDPOINT_KINDS = ("HTTP", "TLS")
RULE_FIELDS = [
	"name",
	"kind",
	"url",
	"expected_status",
	"expected_text",
	"host",
	"minimum_days_left",
	"timeout",
]
VARIABLE = re.compile(r"\{([a-z_]+)\}")
VARIABLES = ("region", "wildcard_domain", "atlas_url", "central_url")
SECONDS_PER_DAY = 86400


class EndpointVariables:
	"""The values that the URL of an HTTP rule and the host of a TLS rule can use in one region."""

	def __init__(self, region: str):
		self.region = region
		atlas_url = frappe.db.get_value(
			"Beacon Resource Source", {"region": region, "source_key": "atlas"}, "base_url"
		)
		self.values = {
			"region": region,
			"wildcard_domain": frappe.db.get_value("Beacon Region", region, "wildcard_domain"),
			"atlas_url": get_origin(atlas_url) if atlas_url else None,
			"central_url": (frappe.db.get_single_value("Beacon Settings", "central_url") or "").rstrip("/"),
		}

	def fill(self, text: str) -> str:
		def replace(match: re.Match) -> str:
			if not self.values.get(match.group(1)):
				raise EndpointVariableError(f"{match.group(0)} has no value in {self.region}.")
			return self.values[match.group(1)]

		return VARIABLE.sub(replace, text)

	@staticmethod
	def get_unknown(text: str) -> list[str]:
		return [name for name in VARIABLE.findall(text or "") if name not in VARIABLES]


class EndpointChecks:
	"""The HTTP and TLS rules of one region. Beacon checks each endpoint, then raises or resolves its alert on the region resource."""

	def __init__(self, region: str):
		self.region = region
		selection = RegionSelection("Beacon Alert Rule")
		self.rules = [
			rule
			for rule in frappe.get_all(
				"Beacon Alert Rule",
				filters={"kind": ("in", ENDPOINT_KINDS), "enabled": 1},
				fields=RULE_FIELDS,
			)
			if region in selection.get_regions(rule.name)
		]
		self.variables = EndpointVariables(region)

	def call(self) -> dict[str, str | None]:
		"""Return the failure of each rule, or None when its endpoint answers as expected. Call it with no row lock.

		A rule with a variable that has no value is left out, so its alert keeps its state, as after a failed rule query.
		"""
		targets = {}
		for rule in self.rules:
			try:
				targets[rule.name] = self.variables.fill(get_target(rule))
			except EndpointVariableError:
				frappe.log_error(
					f"Beacon could not check an endpoint in {self.region}",
					reference_doctype="Beacon Alert Rule",
					reference_name=rule.name,
				)

		rules = [rule for rule in self.rules if rule.name in targets]
		with ThreadPoolExecutor(max_workers=MAXIMUM_PARALLEL_CHECKS) as pool:
			failures = pool.map(lambda rule: get_failure(rule, targets[rule.name]), rules)
			return dict(zip([rule.name for rule in rules], failures, strict=True))

	def reconcile(self, failures: dict[str, str | None], registry: ResourceRegistry, now: datetime) -> None:
		def apply(rule: str) -> None:
			failure = failures[rule]
			rows = [RuleFailure(registry.region_resource, detail=failure)] if failure else []
			AlertReconciler.for_rule(rule, registry, now).reconcile(rows)

		RecordTransaction.run_each(
			"Beacon Alert Rule",
			failures,
			f"Beacon could not update an endpoint alert in {self.region}",
			apply,
		)


def get_target(rule: frappe._dict) -> str:
	return rule.url if rule.kind == "HTTP" else rule.host


def get_failure(rule: frappe._dict, target: str) -> str | None:
	"""Runs in a worker thread, so it uses no Frappe call."""
	failure = get_http_failure(rule, target) if rule.kind == "HTTP" else get_tls_failure(rule, target)
	return failure[:DETAIL_CHARACTERS] if failure else None


def get_http_failure(rule: frappe._dict, url: str) -> str | None:
	try:
		response = requests.get(url, timeout=rule.timeout)
	except requests.RequestException as error:
		return f"{type(error).__name__}: {error}"

	if response.status_code != rule.expected_status:
		return f"HTTP {response.status_code}, expected {rule.expected_status}"
	if rule.expected_text and rule.expected_text not in response.text:
		return f"The response does not contain {rule.expected_text!r}"
	return None


def get_tls_failure(rule: frappe._dict, target: str) -> str | None:
	"""The host can be a host, host:port or URL. The handshake verifies the certificate chain and the host name."""
	address = urlsplit(target if "//" in target else f"//{target}")
	host, port = address.hostname, address.port or 443
	if not host:
		return f"{target!r} has no host name"
	try:
		with (
			socket.create_connection((host, port), timeout=rule.timeout) as connection,
			ssl.create_default_context().wrap_socket(connection, server_hostname=host) as tls,
		):
			expires_at = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
	except (OSError, ValueError) as error:
		return f"{type(error).__name__}: {error}"

	days_left = (expires_at - time.time()) / SECONDS_PER_DAY
	if days_left < rule.minimum_days_left:
		return f"The certificate of {host}:{port} expires in {days_left:.0f} days, under {rule.minimum_days_left}"
	return None


def get_origin(url: str) -> str:
	address = urlsplit(url)
	return f"{address.scheme}://{address.netloc}"
