"""Settings, read once from the environment.

Some are overridable per route by annotation; these are the defaults that apply when a
route says nothing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ANNOTATION_PREFIX = "appsignal-gateway-operator"

# AppSignal's own names, as its UI and MCP server spell them. The GraphQL enum is the
# same list in upper snake case; the provider translates.
VALID_REGIONS = ("europe", "north-america", "asia-pacific", "south-america")


def _csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


class ConfigError(ValueError):
    """Raised for a configuration that cannot work, so it fails at start rather than
    silently monitoring nothing."""


@dataclass(frozen=True)
class Config:
    token: str
    app_id: str
    cluster_name: str
    exclude_suffixes: tuple[str, ...] = ()
    regions: tuple[str, ...] = ("europe",)
    # Minutes a check must keep failing before AppSignal opens an alert. AppSignal has
    # no maintenance windows, so this is what absorbs a node drain.
    warmup_minutes: int = 0
    resync_seconds: int = 900
    dry_run: bool = False
    # Applied only when a monitor is created — see the provider.
    notifier_ids: tuple[str, ...] = ()
    # None leaves the field unmanaged.
    ssl_expiration: bool | None = None

    @property
    def owner_marker(self) -> str:
        return "managed by appsignal-gateway-operator, cluster %s" % self.cluster_name

    def excluded(self, hostname: str) -> bool:
        return any(hostname == s or hostname.endswith("." + s.lstrip("."))
                   for s in self.exclude_suffixes)


def _bool(raw: str, name: str) -> bool | None:
    raw = raw.strip().lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError("%s must be true or false, got %r" % (name, raw))


def from_env(env: dict[str, str] | None = None) -> Config:
    e = os.environ if env is None else env

    token = e.get("APPSIGNAL_TOKEN", "").strip()
    if not token:
        raise ConfigError("APPSIGNAL_TOKEN is required")

    app_id = e.get("APPSIGNAL_APP_ID", "").strip()
    if not app_id:
        raise ConfigError("APPSIGNAL_APP_ID is required; uptime monitors belong to an "
                          "app, and this is the one the operator writes into")

    cluster = e.get("CLUSTER_NAME", "").strip()
    if not cluster:
        # Without this, two clusters writing into one app would claim each other's
        # monitors and delete them on the first resync. Refuse to start.
        raise ConfigError("CLUSTER_NAME is required; it marks which monitors belong "
                          "to this cluster")

    regions = tuple(r.lower() for r in _csv(e.get("REGIONS", "europe")))
    if not regions:
        raise ConfigError("REGIONS needs at least one of %s" % list(VALID_REGIONS))
    unknown = set(regions) - set(VALID_REGIONS)
    if unknown:
        raise ConfigError("unknown region(s) %s; allowed: %s"
                          % (sorted(unknown), list(VALID_REGIONS)))

    raw_warmup = e.get("WARMUP_MINUTES", "0").strip() or "0"
    try:
        warmup = int(raw_warmup)
    except ValueError:
        raise ConfigError("WARMUP_MINUTES must be a number of minutes, got %r"
                          % raw_warmup) from None
    if warmup < 0:
        raise ConfigError("WARMUP_MINUTES cannot be negative, got %d" % warmup)

    return Config(
        token=token,
        app_id=app_id,
        cluster_name=cluster,
        exclude_suffixes=tuple(_csv(e.get("EXCLUDE_SUFFIXES", ""))),
        regions=regions,
        warmup_minutes=warmup,
        resync_seconds=int(e.get("RESYNC_SECONDS", "900")),
        notifier_ids=tuple(_csv(e.get("NOTIFIER_IDS", ""))),
        ssl_expiration=_bool(e.get("SSL_EXPIRATION", ""), "SSL_EXPIRATION"),
        dry_run=_bool(e.get("DRY_RUN", ""), "DRY_RUN") or False,
    )
