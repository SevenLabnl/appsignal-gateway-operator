"""AppSignal uptime monitoring, over its GraphQL API.

AppSignal has no monitor groups. Monitors belong to an app, and inside that app the
first line of a monitor's description marks it as ours — so the record of ownership
still lives in AppSignal and the cluster never has to store anything.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from ..model import DesiredMonitor, ExistingMonitor
from .base import ProviderError

log = logging.getLogger(__name__)

API = "https://appsignal.com/graphql"

_FIELDS = """
  id name url description warmupDuration notifierIds
  headers { key value }
  checkBodyContent monitorSslExpiration regions
"""

LIST = """
query($appId: String!) {
  app(id: $appId) { uptimeMonitors { %s } }
}""" % _FIELDS

CREATE = """
mutation($appId: String!, $monitor: UptimeMonitorInput!) {
  createUptimeMonitor(appId: $appId, uptimeMonitor: $monitor) { id }
}"""

UPDATE = """
mutation($appId: String!, $id: String!, $monitor: UptimeMonitorInput!) {
  updateUptimeMonitor(appId: $appId, id: $id, uptimeMonitor: $monitor) { id }
}"""

DELETE = """
mutation($appId: String!, $id: String!) {
  deleteUptimeMonitor(appId: $appId, id: $id) { id }
}"""


def _to_enum(region: str) -> str:
    return region.upper().replace("-", "_")


def _from_enum(region: str) -> str:
    return region.lower().replace("_", "-")


class AppSignal:
    name = "appsignal"

    def __init__(self, token: str, app_id: str, owner_marker: str, timeout: int = 30,
                 opener=None) -> None:
        self._token = token
        self._app_id = app_id
        self._marker = owner_marker
        self._timeout = timeout
        # injectable so tests never touch the network
        self._open = opener or urllib.request.urlopen

    # ------------------------------------------------------------------ transport
    def _call(self, query: str, variables: dict) -> dict:
        # The API only takes the token as a query parameter, so every error message
        # below names API rather than the URL that was actually requested.
        url = API + "?" + urllib.parse.urlencode({"token": self._token})
        body = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with self._open(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise ProviderError("POST %s -> %s %s" % (API, e.code, detail)) from None
        except urllib.error.URLError as e:
            raise ProviderError("POST %s unreachable: %s" % (API, e.reason)) from None

        payload = json.loads(raw) if raw else {}
        # GraphQL reports failure in the body with a 200, so a status check alone
        # would treat a refused mutation as success.
        errors = payload.get("errors")
        if errors:
            msgs = "; ".join(str(err.get("message", err)) for err in errors)
            raise ProviderError("AppSignal refused the call: %s" % msgs[:500])
        return payload.get("data") or {}

    # ------------------------------------------------------------------ mapping
    def _owned(self, item: dict) -> bool:
        lines = (item.get("description") or "").splitlines()
        return bool(lines) and lines[0].strip() == self._marker

    @staticmethod
    def _to_existing(item: dict) -> ExistingMonitor:
        ssl = item.get("monitorSslExpiration")
        return ExistingMonitor(
            id=str(item["id"]),
            url=item.get("url") or "",
            display_name=item.get("name") or "",
            regions=tuple(_from_enum(r) for r in item.get("regions") or []),
            warmup_minutes=int(item.get("warmupDuration") or 0),
            check_body_content=item.get("checkBodyContent") or "",
            ssl_expiration=bool(ssl) if ssl is not None else None,
            description=item.get("description") or "",
            notifier_ids=tuple(str(n) for n in item.get("notifierIds") or []),
            headers=tuple((h["key"], h.get("value") or "")
                          for h in item.get("headers") or []),
        )

    def _input(self, want: DesiredMonitor) -> dict:
        body = {
            "name": want.display_name,
            "url": want.url,
            "description": self._marker,
            "regions": [_to_enum(r) for r in want.regions],
            "warmupDuration": want.warmup_minutes,
            # null rather than omitted, so that removing the annotation removes the
            # check instead of leaving the last value in place
            "checkBodyContent": want.check_body_content or None,
        }
        if want.ssl_expiration is not None:
            body["monitorSslExpiration"] = want.ssl_expiration
        return body

    # ------------------------------------------------------------------ interface
    def list_managed(self) -> list[ExistingMonitor]:
        data = self._call(LIST, {"appId": self._app_id})
        app = data.get("app")
        if app is None:
            # An app id the token cannot see. Treating it as an empty list would plan
            # a create for every hostname into an app that does not exist.
            raise ProviderError("app %s not found, or not visible to this token"
                                % self._app_id)
        items = app.get("uptimeMonitors") or []
        return [self._to_existing(i) for i in items if self._owned(i)]

    def create(self, want: DesiredMonitor) -> str:
        monitor = self._input(want)
        # Notifiers are set when the monitor is created and never on an update. A new
        # monitor should not start out silent, but who gets alerted after that is a
        # decision for the people on call, and a resync that reset it every fifteen
        # minutes would be worse than useless.
        monitor["notifierIds"] = list(want.notifier_ids)
        res = self._call(CREATE, {"appId": self._app_id, "monitor": monitor})
        return str((res.get("createUptimeMonitor") or {})["id"])

    def update(self, existing: ExistingMonitor, want: DesiredMonitor) -> None:
        monitor = self._input(want)
        # Carried back as they are, whether the API replaces the whole monitor on an
        # update or only the fields given: either way nobody's change is lost.
        monitor["url"] = existing.url
        monitor["description"] = existing.description
        monitor["notifierIds"] = list(existing.notifier_ids)
        monitor["headers"] = [{"key": k, "value": v} for k, v in existing.headers]
        self._call(UPDATE, {"appId": self._app_id, "id": existing.id,
                            "monitor": monitor})

    def delete(self, existing: ExistingMonitor) -> None:
        self._call(DELETE, {"appId": self._app_id, "id": existing.id})
