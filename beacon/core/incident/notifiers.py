from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar
from xml.sax.saxutils import escape

import frappe
import requests
from frappe.utils import get_url
from frappe.utils.password import get_encryption_key

from beacon.core.incident.emails import IncidentEmail

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident
	from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
	from beacon.beacon.doctype.beacon_settings.beacon_settings import BeaconSettings

REQUEST_TIMEOUT_SECONDS = 10
# A neural voice. Twilio's basic voice spells out names such as INC-00008.
CALL_VOICE = "Polly.Joanna-Neural"


class Notifier(ABC):
	"""Sends one Beacon Notification through one channel."""

	channel: ClassVar[str]

	def __init__(self, settings: BeaconSettings):
		self.settings = settings

	@abstractmethod
	def is_enabled(self) -> bool: ...

	@abstractmethod
	def send(self, notification: BeaconNotification) -> str | None:
		"""Send the message and return the provider's reference, if it has one."""


class ChatNotifier(Notifier):
	channel = "Chat"

	def is_enabled(self) -> bool:
		return bool(self.settings.chat_enabled)

	def send(self, notification: BeaconNotification) -> None:
		url = self.settings.get_password("chat_webhook_url")
		requests.post(
			url, json={"text": notification.message}, timeout=REQUEST_TIMEOUT_SECONDS
		).raise_for_status()


class EmailNotifier(Notifier):
	channel = "Email"

	def is_enabled(self) -> bool:
		return bool(self.settings.email_enabled) and not self.settings.paging_paused

	def send(self, notification: BeaconNotification) -> None:
		email = frappe.db.get_value("User", notification.responder, "email")
		sender = frappe.db.get_value("Email Account", self.settings.email_account, "email_id")
		if not notification.incident:
			frappe.sendmail(
				recipients=[email], sender=sender, subject=notification.message, message=notification.message
			)
			return

		subject, args = IncidentEmail(notification.incident).get_responder_email(
			notification.page_round, self.settings
		)
		frappe.sendmail(
			recipients=[email],
			sender=sender,
			subject=subject,
			template="beacon_incident_responder",
			args=args,
			reference_doctype="Beacon Incident",
			reference_name=notification.incident,
		)


class CallNotifier(Notifier):
	channel = "Call"

	def is_enabled(self) -> bool:
		return bool(self.settings.phone_calls_enabled) and not self.settings.paging_paused

	def send(self, notification: BeaconNotification) -> str:
		phone = frappe.db.get_value("Beacon Responder", {"user": notification.responder}, "phone")
		token = self.get_callback_token(notification.name)
		response = requests.post(
			f"https://api.twilio.com/2010-04-01/Accounts/{self.settings.twilio_account_sid}/Calls.json",
			auth=(self.settings.twilio_api_key_sid, self.settings.get_password("twilio_api_key_secret")),
			data={
				"To": phone,
				"From": self.settings.twilio_from_number,
				"Twiml": self.get_twiml(notification),
				"StatusCallback": get_url(
					f"/api/method/beacon.api.twilio.status?notification={notification.name}&token={token}"
				),
				"StatusCallbackEvent": ["answered", "completed"],
			},
			timeout=REQUEST_TIMEOUT_SECONDS,
		)
		response.raise_for_status()
		return response.json()["sid"]

	def get_twiml(self, notification: BeaconNotification) -> str:
		"""Say the severity, the problem and the region twice, so the responder hears it after picking up."""
		incident = frappe.db.get_value(
			"Beacon Incident", notification.incident, ["severity", "title"], as_dict=True
		)
		if incident:
			problem = incident.title.rsplit(" on ", 1)[0]
			region = self._get_region_title(notification.incident)
			if region and f" in {region}" not in problem:
				problem = f"{problem} in {region}"
			speech = f"Beacon. {incident.severity} incident. {problem}. Answering this call acknowledges it."
		else:
			speech = notification.message

		say = f'<Say voice="{CALL_VOICE}">{escape(speech)}</Say>'
		return f'<Response>{say}<Pause length="1"/>{say}</Response>'

	@staticmethod
	def _get_region_title(incident: str) -> str | None:
		region = frappe.db.get_value(
			"Beacon Incident Scope",
			{"parenttype": "Beacon Incident", "parent": incident},
			"region",
		) or frappe.db.get_value("Beacon Alert", {"incident": incident}, "region")
		return frappe.db.get_value("Beacon Region", region, "title") if region else None

	@staticmethod
	def get_callback_token(notification: str) -> str:
		"""Twilio signs callbacks only with the account Auth Token, which Beacon does not keep. So the
		callback URL of each call carries this token."""
		return hmac.new(get_encryption_key().encode(), notification.encode(), hashlib.sha256).hexdigest()


NOTIFIERS = {notifier.channel: notifier for notifier in (ChatNotifier, EmailNotifier, CallNotifier)}
