from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from beacon.core.exceptions import StateError


class StateMachine:
	"""The states of one DocType field and the moves between them. Only `move` changes the field."""

	def __init__(self, field: str, moves: dict[str, tuple[str, ...]]):
		self.field = field
		self.moves = moves

	def can_move(self, document: Document, state: str) -> bool:
		"""True when the current state allows the move."""
		return state in self.moves.get(document.get(self.field), ())

	def move(self, document: Document, state: str) -> None:
		"""Set the state. A move to the current state changes nothing."""
		current = document.get(self.field)
		if state == current:
			return

		if not self.can_move(document, state):
			frappe.throw(
				_("{0} cannot go from {1} to {2}.").format(_(document.doctype), current, state), StateError
			)
		document.set(self.field, state)
