from __future__ import annotations

from typing import TYPE_CHECKING

import requests

from beacon.core.exceptions import SourceFetchError

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_resource_source.beacon_resource_source import BeaconResourceSource

FETCH_TIMEOUT_SECONDS = 30
RESOURCES_PATH = "/beacon/resources"


class ResourceSourceClient:
	"""Fetches a source response without changing resource or source records."""

	def __init__(self, source: BeaconResourceSource):
		self.source = source

	def fetch(self, use_etag: bool) -> tuple[dict | None, str | None]:
		"""Return the parsed body and its ETag, or None when the list did not change."""
		headers = {"Accept": "application/json"}
		token = self.source.get_password("token", raise_exception=False)
		if token:
			headers["Authorization"] = f"token {token}"
		if use_etag and self.source.etag:
			headers["If-None-Match"] = self.source.etag

		try:
			response = requests.get(
				self.source.base_url.rstrip("/") + RESOURCES_PATH,
				headers=headers,
				timeout=FETCH_TIMEOUT_SECONDS,
			)
		except requests.RequestException as error:
			raise SourceFetchError(str(error)) from error

		if response.status_code == 304:
			return None, self.source.etag
		if response.status_code != 200:
			raise SourceFetchError(f"HTTP {response.status_code}: {response.text[:500]}")

		try:
			return response.json(), response.headers.get("ETag")
		except ValueError as error:
			raise SourceFetchError(f"The response is not JSON: {error}") from error
