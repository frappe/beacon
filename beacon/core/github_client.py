from __future__ import annotations

from urllib.parse import quote

import requests

from beacon.core.exceptions import DefinitionSourceError

API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 30


class GitHubClient:
	"""Reads the files of a public or private GitHub repository at one commit."""

	def __init__(self, token: str | None = None):
		self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
		if token:
			self.headers["Authorization"] = f"Bearer {token}"

	def get_commit(self, repository: str, branch: str) -> str:
		return self._get(f"/repos/{repository}/commits/{quote(branch, safe='')}").json()["sha"]

	def list_files(self, repository: str, commit: str, directory: str) -> list[str]:
		"""The file paths directly in the directory. A missing directory has no files."""
		response = self._get(f"/repos/{repository}/contents/{quote(directory)}?ref={commit}", missing_ok=True)
		return (
			[item["path"] for item in response.json() if item["type"] == "file"]
			if response is not None
			else []
		)

	def read_file(self, repository: str, commit: str, path: str) -> str:
		return self._get(
			f"/repos/{repository}/contents/{quote(path)}?ref={commit}",
			accept="application/vnd.github.raw+json",
		).text

	def _get(
		self, path: str, accept: str | None = None, missing_ok: bool = False
	) -> requests.Response | None:
		headers = {**self.headers, "Accept": accept} if accept else self.headers
		try:
			response = requests.get(API_URL + path, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
		except requests.RequestException as error:
			raise DefinitionSourceError(f"GitHub request failed: {error}") from error

		if response.status_code == 404 and missing_ok:
			return None
		if response.status_code == 404:
			raise DefinitionSourceError(
				"GitHub found no such repository, branch or file. A private repository needs a GitHub Token."
			)
		if response.status_code != 200:
			raise DefinitionSourceError(f"GitHub returned HTTP {response.status_code}: {response.text[:300]}")
		return response
