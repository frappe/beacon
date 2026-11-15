from __future__ import annotations

import frappe
from frappe.model.document import Document


class BeaconNotification(Document):
	"""One call, email or chat post. Written as Queued and sent by a job after the commit."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		answered_at: DF.Datetime | None
		channel: DF.Literal["Call", "Email", "Chat"]
		error: DF.SmallText | None
		incident: DF.Link | None
		message: DF.SmallText | None
		page_round: DF.Int
		provider_ref: DF.Data | None
		responder: DF.Link | None
		sent_at: DF.Datetime | None
		status: DF.Literal[
			"Queued", "Sending", "Sent", "Answered", "No Answer", "Acknowledged", "Failed", "Cancelled"
		]
	# end: auto-generated types


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Notification", ["incident", "sent_at"])
	frappe.db.add_index("Beacon Notification", ["provider_ref"])
