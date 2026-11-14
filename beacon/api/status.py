import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import get_url
from werkzeug.wrappers import Response

from beacon.core.incident.subscription import IncidentSubscription
from beacon.core.status_page import IncidentFeed, StatusCache, StatusPage


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_status() -> dict:
	return StatusCache.get_or_build(StatusCache.STATUS, StatusPage().get_status)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_history() -> dict:
	return StatusCache.get_or_build(StatusCache.HISTORY, StatusPage().get_history)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_incident(name: str) -> dict:
	return _found(
		StatusCache.get_or_build(
			StatusCache.incident_key(name), lambda: StatusPage().get_incident(name) or {}
		)
	)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_unlisted_incident(token: str) -> dict:
	return _found(
		StatusCache.get_or_build(
			StatusCache.unlisted_key(token), lambda: StatusPage().get_unlisted_incident(token) or {}
		)
	)


def _found(incident: dict) -> dict:
	"""An empty result is cached too, so unknown names and tokens cannot reach the database every time."""
	if not incident:
		raise frappe.DoesNotExistError
	return incident


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600)
def subscribe(email: str, name: str | None = None, token: str | None = None) -> None:
	IncidentSubscription(_get_published_incident(name, token)).subscribe(email)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=3600)
def change_subscription(token: str, action: str) -> str:
	"""The email links open a status page route, which posts here. A mail client that opens links ahead does not change anything."""
	return IncidentSubscription.change(token, action)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_incident_feed(name: str | None = None, token: str | None = None) -> Response:
	incident = get_unlisted_incident(token) if token else get_incident(name)
	path = f"/i/{token}" if token else f"/incidents/{incident['name']}"
	return Response(IncidentFeed(incident, get_url(path)).build(), mimetype="application/rss+xml")


def _get_published_incident(name: str | None, token: str | None) -> str:
	incident = get_unlisted_incident(token) if token else get_incident(name)
	return incident["name"]
