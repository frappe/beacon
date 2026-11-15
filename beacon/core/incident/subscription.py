from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import now_datetime, validate_email_address

from beacon.core.incident.emails import IncidentEmail
from beacon.core.jobs import queue_subscriber_update
from beacon.core.status_page import PUBLISHED_VISIBILITIES

if TYPE_CHECKING:
	from frappe.model.document import Document

	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident


class IncidentSubscription:
	"""Email subscriptions of status page visitors to one published incident, with a confirmation email first."""

	def __init__(self, incident: str):
		self.incident = frappe.db.get_value(
			"Beacon Incident", incident, ["name", "title", "visibility", "public_id"], as_dict=True
		)
		if not self.incident or self.incident.visibility not in PUBLISHED_VISIBILITIES:
			raise frappe.DoesNotExistError

	@property
	def page_url(self) -> str:
		return frappe.get_cached_doc("Beacon Settings").get_status_page_url(
			f"/incidents/{self.incident.public_id}"
		)

	def subscribe(self, email: str) -> None:
		"""Send a confirmation email. A repeated request sends it again and does not tell whether the address is known."""
		email = validate_email_address(email.strip().lower(), throw=True)
		subscriber = self._lock_subscriber(email)
		if subscriber.status == "Confirmed":
			return

		subscriber.update({"status": "Pending", "token": frappe.generate_hash(length=32)})
		subscriber.save(ignore_permissions=True)
		self._send(
			email,
			_("Confirm updates: {0}").format(self.incident.title),
			"beacon_subscription_confirm",
			{
				"brand": frappe.get_cached_doc("Beacon Settings").status_page_title,
				"center": True,
				"title": self.incident.title,
				"url": self._get_subscription_url(subscriber.token, "confirm"),
			},
		)

	def notify(self, update_name: str) -> None:
		"""Email one public update to each confirmed subscriber, once. The update row lock makes a repeated job skip it."""
		update = frappe.db.get_value(
			"Beacon Incident Update",
			{"name": update_name, "parent": self.incident.name, "is_public": 1},
			["name", "status", "message", "posted_at", "subscribers_notified_at"],
			as_dict=True,
			for_update=True,
		)
		if not update or update.subscribers_notified_at:
			return

		frappe.db.set_value(
			"Beacon Incident Update",
			update.name,
			"subscribers_notified_at",
			now_datetime(),
			update_modified=False,
		)
		subscribers = frappe.get_all(
			"Beacon Incident Subscriber",
			filters={"incident": self.incident.name, "status": "Confirmed"},
			fields=["email", "token"],
		)
		email = IncidentEmail(self.incident.name)
		for subscriber in subscribers:
			subject, args = email.get_update_email(
				update, self.page_url, self._get_subscription_url(subscriber.token, "unsubscribe")
			)
			self._send(subscriber.email, subject, "beacon_incident_update", args)

	@staticmethod
	def change(token: str, action: str) -> str:
		"""Confirm or end a subscription from the link in its email. Returns the incident page URL."""
		name = frappe.db.get_value("Beacon Incident Subscriber", {"token": token}) if token else None
		if not name or action not in ("confirm", "unsubscribe"):
			raise frappe.DoesNotExistError

		subscriber = frappe.get_doc("Beacon Incident Subscriber", name, for_update=True)
		if subscriber.token != token:
			raise frappe.DoesNotExistError
		if action == "confirm" and subscriber.status == "Pending":
			subscriber.update({"status": "Confirmed", "confirmed_at": now_datetime()})
		elif action == "unsubscribe":
			subscriber.status = "Unsubscribed"
		subscriber.save(ignore_permissions=True)
		return IncidentSubscription(subscriber.incident).page_url

	@staticmethod
	def queue_new_updates(incident: BeaconIncident) -> None:
		"""Queue an email job for each update that this save added as public or made public."""
		before = incident.get_doc_before_save()
		public_before = {row.name for row in before.updates if row.is_public} if before else set()
		for row in incident.updates:
			if row.is_public and row.name not in public_before:
				queue_subscriber_update(incident.name, row.name)

	def _lock_subscriber(self, email: str) -> Document:
		"""Insert the row first, so two requests for one address wait on the unique key instead of racing."""
		filters = {"incident": self.incident.name, "email": email}
		if not frappe.db.exists("Beacon Incident Subscriber", filters):
			try:
				frappe.get_doc(
					{"doctype": "Beacon Incident Subscriber", "status": "Pending", **filters}
				).insert(ignore_permissions=True)
			except frappe.DuplicateEntryError:
				pass
		name = frappe.db.get_value("Beacon Incident Subscriber", filters)
		return frappe.get_doc("Beacon Incident Subscriber", name, for_update=True)

	def _get_subscription_url(self, token: str, action: str) -> str:
		return frappe.get_cached_doc("Beacon Settings").get_status_page_url(
			f"/subscription/{token}?action={action}"
		)

	def _send(self, email: str, subject: str, template: str, args: dict) -> None:
		frappe.sendmail(
			recipients=[email],
			subject=subject,
			template=template,
			args=args,
			reference_doctype="Beacon Incident",
			reference_name=self.incident.name,
		)
