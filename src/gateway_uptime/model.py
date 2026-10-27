"""What we want to exist, expressed independently of any provider.

A DesiredMonitor is derived purely from an HTTPRoute plus configuration. The provider's
job is to make its own world match a set of these; it never reasons about Kubernetes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesiredMonitor:
    """One hostname we expect to be up."""

    hostname: str
    url: str
    regions: tuple[str, ...]
    warmup_minutes: int
    # where it came from, for log lines
    namespace: str
    route: str
    # Text the response body must contain. Empty means no body check.
    check_body_content: str = ""
    # Applied when the monitor is first created and never afterwards, so that whoever
    # tunes alerting in AppSignal keeps their change.
    notifier_ids: tuple[str, ...] = ()
    # None when unmanaged.
    ssl_expiration: bool | None = None

    @property
    def key(self) -> str:
        """Identity of a monitor. The URL, not the hostname: two routes may serve the
        same host on different paths and both deserve a check."""
        return self.url

    @property
    def display_name(self) -> str:
        """The hostname, plus the path when it is not the root, so two checks on one
        host stay distinguishable in a list that shows names only."""
        path = self.url.split(self.hostname, 1)[-1]
        return self.hostname if path in ("", "/") else self.hostname + path


@dataclass(frozen=True)
class ExistingMonitor:
    """A monitor the provider already has, reduced to the fields we manage plus the
    ones an update has to carry back unchanged."""

    id: str
    url: str
    display_name: str
    regions: tuple[str, ...]
    warmup_minutes: int
    check_body_content: str = ""
    ssl_expiration: bool | None = None
    # not managed, read so an update can send them back as they were
    description: str = ""
    notifier_ids: tuple[str, ...] = ()
    headers: tuple[tuple[str, str], ...] = ()

    def differs_from(self, want: DesiredMonitor) -> list[str]:
        """Which managed fields disagree. Returns names so a log line can say what is
        being changed instead of just that something is."""
        out = []
        if self.display_name != want.display_name:
            out.append("name")
        if sorted(self.regions) != sorted(want.regions):
            out.append("regions")
        if self.warmup_minutes != want.warmup_minutes:
            out.append("warmup")
        if self.check_body_content != want.check_body_content:
            out.append("check_body_content")
        if want.ssl_expiration is not None \
                and self.ssl_expiration != want.ssl_expiration:
            out.append("ssl_expiration")
        return out


@dataclass
class Plan:
    """The difference between what should exist and what does. Computed before anything
    is called, so a dry run can print exactly what a real run would do."""

    create: list[DesiredMonitor]
    update: list[tuple[ExistingMonitor, DesiredMonitor, list[str]]]
    delete: list[ExistingMonitor]
    unchanged: int = 0

    @property
    def empty(self) -> bool:
        return not (self.create or self.update or self.delete)

    def summary(self) -> str:
        return ("%d to create, %d to update, %d to delete, %d unchanged"
                % (len(self.create), len(self.update), len(self.delete),
                   self.unchanged))


def plan(desired: list[DesiredMonitor], existing: list[ExistingMonitor]) -> Plan:
    """Pure: no I/O, so the interesting logic is testable without a cluster or an API."""
    by_url = {m.url: m for m in existing}
    seen: set[str] = set()
    p = Plan(create=[], update=[], delete=[])

    for want in desired:
        have = by_url.get(want.key)
        if have is None:
            p.create.append(want)
            continue
        seen.add(have.id)
        diff = have.differs_from(want)
        if diff:
            p.update.append((have, want, diff))
        else:
            p.unchanged += 1

    for have in existing:
        if have.id not in seen:
            p.delete.append(have)

    return p
