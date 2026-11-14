from __future__ import annotations

import functools
from collections.abc import Callable, Iterable

import frappe
from frappe.model.document import Document

# A lock conflict (deadlock, MariaDB snapshot isolation error 1020 or lock wait timeout) is retried this often.
ATTEMPTS = 3
LOCK_CONFLICTS = (frappe.QueryDeadlockError, frappe.QueryTimeoutError, frappe.TimestampMismatchError)


class RecordTransaction:
	"""Runs the writes of one record in its own transaction.

	MariaDB 11.8 rejects a write to a row that changed after the transaction first read anything. So each
	record starts a new transaction, locks its rows before it reads them, and is retried on a lock conflict.
	A retry is safe because the work reads its state again and nothing was committed.
	"""

	def __init__(self, doctype: str, name: str):
		self.doctype = doctype
		self.name = name

	@staticmethod
	def start_new() -> None:
		"""Commit the reads and locks so far. Call it only before the first write: before a remote call, so no
		lock is held during it, and after one, so the next reads see current rows."""
		frappe.db.commit()  # nosemgrep: nothing is written yet; this only ends the read view and releases locks

	@classmethod
	def run_each(cls, doctype: str, names: Iterable[str], title: str, work: Callable[[str], object]) -> None:
		"""Run work for each record. A failure rolls back and logs that record only."""
		for name in names:
			cls(doctype, name).run(lambda name=name: work(name), title)

	def run(self, work: Callable[[], object], title: str) -> bool:
		"""Like retry, but log a failure instead of raising it. Return True on success."""
		try:
			self.retry(work)
			return True
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title, reference_doctype=self.doctype, reference_name=self.name)
			return False

	def retry[Result](self, work: Callable[[], Result]) -> Result:
		"""Run work and commit. On a lock conflict, roll back and run it again."""
		self.start_new()
		for attempt in range(1, ATTEMPTS + 1):
			try:
				result = work()
				frappe.db.commit()  # nosemgrep: one record per transaction
				return result
			except LOCK_CONFLICTS:
				frappe.db.rollback()
				if attempt == ATTEMPTS:
					raise
		raise AssertionError("unreachable")


def document_action[**Params, Result](
	method: Callable[Params, Result],
) -> Callable[Params, Result]:
	"""Whitelist a Desk action that only writes to the database. On a lock conflict, roll back, reload the
	document and run the action again, so the user does not see "Please try again"."""

	@functools.wraps(method)
	def run(document: Document, *args, **kwargs) -> Result:
		for attempt in range(1, ATTEMPTS + 1):
			try:
				return method(document, *args, **kwargs)
			except LOCK_CONFLICTS:
				if attempt == ATTEMPTS:
					raise
				frappe.db.rollback()
				document.reload()
		raise AssertionError("unreachable")

	return frappe.whitelist(methods=["POST"])(run)
