from __future__ import annotations

import frappe


class ClickHouseError(frappe.ValidationError):
	"""A ClickHouse request failed or returned an error."""


class RuleContractError(frappe.ValidationError):
	"""A rule or SLI query returned rows that break its contract."""


class SourceFetchError(frappe.ValidationError):
	"""A Resource Source request failed or returned a bad list."""


class IncidentActionError(frappe.ValidationError):
	"""An incident action is not allowed in the current state."""


class StateError(frappe.ValidationError):
	"""A state machine does not allow the move."""


class DefinitionSourceError(frappe.ValidationError):
	"""A definition source could not be read, or one of its files is invalid."""


class EndpointVariableError(frappe.ValidationError):
	"""An HTTP or TLS rule uses a variable that is unknown or has no value."""
