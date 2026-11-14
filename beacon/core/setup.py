from __future__ import annotations

import click
import frappe
from frappe.query_builder.functions import Coalesce
from frappe.utils import now_datetime

TIME_ZONE = "UTC"


class SiteSetup:
	"""Keeps the site on UTC, the time zone of Datum, so stored times compare with telemetry times."""

	def configure(self) -> None:
		"""Run after install and after every migrate."""
		self.use_utc()
		self.realign_scheduled_job_baselines()

	def complete_setup_wizard(self) -> None:
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		setup_complete({})
		self.configure()

	def use_utc(self) -> None:
		settings = frappe.get_single("System Settings")
		if settings.time_zone != TIME_ZONE:
			settings.time_zone = TIME_ZONE
			settings.save()
			click.echo(f"Set the site time zone to {TIME_ZONE}.")

	def realign_scheduled_job_baselines(self) -> int:
		"""Bring back jobs whose baseline sits in the future, and return how many were moved.

		A new site stamps its job types in Frappe's Asia/Kolkata fallback before the site moves to UTC,
		so those stamps sit ahead of the clock and the scheduler finds nothing due until time catches up.
		"""
		now = now_datetime()
		table = frappe.qb.DocType("Scheduled Job Type")
		stale = (
			frappe.qb.from_(table)
			.select(table.name)
			.where(Coalesce(table.last_execution, table.creation) > now)
		).run(pluck=True)
		if not stale:
			return 0

		frappe.qb.update(table).set(table.last_execution, now).where(table.name.isin(stale)).run()
		click.echo(f"Realigned {len(stale)} scheduled job baselines left ahead of the clock.")
		return len(stale)


def after_install() -> None:
	SiteSetup().complete_setup_wizard()


def after_migrate() -> None:
	SiteSetup().configure()
