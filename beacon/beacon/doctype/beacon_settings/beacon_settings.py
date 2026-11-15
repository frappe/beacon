from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, get_url, now_datetime

from beacon.core.definitions import DefinitionSync
from beacon.core.incident.notifiers import NOTIFIERS
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.transaction import document_action

ADMIN_ROLES = ("Beacon Admin", "System Manager")
REPOSITORY = re.compile(r"^[\w.-]+/[\w.-]+$")
SOURCE_PATH = re.compile(r"^(?!.*\.\.)[\w./-]*$")


class BeaconSettings(Document):
	"""On-call responders, channels, the status page and safety switches."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from beacon.beacon.doctype.beacon_definition_source.beacon_definition_source import (
			BeaconDefinitionSource,
		)
		from beacon.beacon.doctype.beacon_responder.beacon_responder import BeaconResponder

		backup_after_minutes_critical: DF.Int
		backup_after_minutes_high: DF.Int
		central_url: DF.Data | None
		chat_enabled: DF.Check
		chat_webhook_url: DF.Password | None
		definition_sources: DF.Table[BeaconDefinitionSource]
		email_account: DF.Link | None
		email_enabled: DF.Check
		github_token: DF.Password | None
		heartbeat_url: DF.Password | None
		max_rounds: DF.Int
		override_until: DF.Datetime | None
		page_delay_seconds: DF.Int
		paging_paused: DF.Check
		paused_reason: DF.SmallText | None
		phone_calls_enabled: DF.Check
		primary_override: DF.Link | None
		reopen_window_minutes: DF.Int
		repeat_every_minutes: DF.Int
		responders: DF.Table[BeaconResponder]
		status_page_domain: DF.Data | None
		status_page_title: DF.Data | None
		twilio_account_sid: DF.Data | None
		twilio_api_key_secret: DF.Password | None
		twilio_api_key_sid: DF.Data | None
		twilio_from_number: DF.Data | None
	# end: auto-generated types

	def validate(self) -> None:
		if self.phone_calls_enabled and not any(row.active and row.phone for row in self.responders):
			frappe.throw(_("Phone calls need at least one active responder with a phone number."))
		for source in self.definition_sources:
			source.path = (source.path or "").strip("/")
			if not REPOSITORY.match(source.repository) or not SOURCE_PATH.match(source.path):
				frappe.throw(
					_("Row {0}: enter the repository as owner/name and a plain folder path.").format(
						source.idx
					)
				)

	@document_action
	def set_primary_override(self, user: str, until: str) -> None:
		self.check_permission("read")
		frappe.only_for(("Beacon Responder", *ADMIN_ROLES))
		if not any(row.active and row.user == user for row in self.responders):
			frappe.throw(_("Choose an active responder."))
		if get_datetime(until) <= now_datetime():
			frappe.throw(_("Until must be a time in the future."))
		self.primary_override = user
		self.override_until = until
		self.save(ignore_permissions=True)

	@document_action
	def pause_paging(self, reason: str) -> None:
		frappe.only_for(ADMIN_ROLES)
		self.paging_paused = 1
		self.paused_reason = reason
		self.save()
		NotificationOutbox().post_to_chat(f"Paging paused by {frappe.session.user}: {reason}")

	@document_action
	def resume_paging(self) -> None:
		frappe.only_for(ADMIN_ROLES)
		self.paging_paused = 0
		self.paused_reason = None
		self.save()
		NotificationOutbox().post_to_chat(f"Paging resumed by {frappe.session.user}.")

	@document_action
	def send_test_page(self, user: str, channel: str) -> None:
		frappe.only_for(ADMIN_ROLES)
		outbox = NotificationOutbox()
		if channel not in NOTIFIERS:
			frappe.throw(_("Choose Call, Email or Chat."))
		if not outbox.is_enabled(channel):
			frappe.throw(_("The {0} channel is off or not set up.").format(channel))
		if channel != "Chat":
			responder = next((row for row in self.responders if row.active and row.user == user), None)
			if not responder or not frappe.db.get_value("User", user, "enabled"):
				frappe.throw(_("Choose an active responder whose user account is enabled."))
			if channel == "Call" and not responder.phone:
				frappe.throw(_("This responder has no phone number."))
			if channel == "Email" and not frappe.db.get_value("User", user, "email"):
				frappe.throw(_("This responder's user account has no email address."))
		outbox.queue(
			channel, "Beacon test page. No action is needed.", responder=None if channel == "Chat" else user
		)

	def get_active_responders(self) -> list[BeaconResponder]:
		"""Active responders in call order. A current primary override is called first."""
		responders = [row for row in self.responders if row.active]
		if (
			self.primary_override
			and self.override_until
			and get_datetime(self.override_until) > now_datetime()
		):
			primary = [row for row in responders if row.user == self.primary_override]
			responders = primary + [row for row in responders if row.user != self.primary_override]
		return responders

	def get_status_page_url(self, path: str) -> str:
		"""A link that Beacon sends to customers. It never comes from the request host, which a guest controls."""
		if self.status_page_domain:
			return f"https://{self.status_page_domain}{path}"
		if frappe.conf.host_name:
			return get_url(path)
		frappe.throw(_("Set the status page domain in Beacon Settings."))

	def get_backup_after_minutes(self, severity: str) -> int:
		return (
			self.backup_after_minutes_critical if severity == "Critical" else self.backup_after_minutes_high
		)

	@frappe.whitelist(methods=["POST"])
	def sync_definitions(self) -> list[dict]:
		"""Read the rule and service files of every source and apply them."""
		frappe.only_for(ADMIN_ROLES)
		return DefinitionSync(self).run()
