"""The AppSignal client, against a fake transport.

The things most likely to be wrong here are invisible until they bite: a GraphQL error
that arrives with a 200, a monitor someone made by hand being read as ours, and an
update that quietly drops a field a human set.
"""
import io
import json
import urllib.error

import pytest

from gateway_uptime.model import DesiredMonitor, ExistingMonitor
from gateway_uptime.providers.appsignal import AppSignal
from gateway_uptime.providers.base import ProviderError

MARKER = "managed by appsignal-gateway-operator, cluster test-cluster"


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGraphQL:
    """Records each call and answers from the first word of the operation's field."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data)
        self.calls.append((req.full_url, body["query"], body["variables"]))
        for op, payload in self.replies.items():
            if op in body["query"]:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError("unexpected operation %s" % body["query"])


def client(http):
    return AppSignal("tok", "app-1", MARKER, opener=http)


def monitor(mid, url, description=MARKER, **extra):
    item = {"id": mid, "name": url.split("/")[2], "url": url,
            "description": description, "warmupDuration": 0, "notifierIds": [],
            "headers": [], "checkBodyContent": None, "monitorSslExpiration": False,
            "regions": ["EUROPE"]}
    item.update(extra)
    return item


def listing(*items):
    return {"data": {"app": {"uptimeMonitors": list(items)}}}


def want(url="https://shop.example.com/", **kw):
    return DesiredMonitor(hostname="shop.example.com", url=url,
                          regions=("europe", "north-america"), warmup_minutes=2,
                          namespace="acme", route="shop", **kw)


# ------------------------------------------------------------------ ownership
def test_only_monitors_carrying_our_marker_are_ours():
    http = FakeGraphQL({"uptimeMonitors": listing(
        monitor("1", "https://ours.example.com/"),
        monitor("2", "https://by-hand.example.com/", description="made in the UI"),
        monitor("3", "https://no-description.example.com/", description=None),
    )})
    assert [m.id for m in client(http).list_managed()] == ["1"]


def test_another_cluster_with_a_longer_name_is_not_ours():
    # cluster "test-cluster" must not claim the monitors of "test-cluster-2"
    http = FakeGraphQL({"uptimeMonitors": listing(
        monitor("1", "https://a.example.com/", description=MARKER + "-2"),
    )})
    assert client(http).list_managed() == []


def test_text_added_below_the_marker_keeps_the_monitor_ours():
    http = FakeGraphQL({"uptimeMonitors": listing(
        monitor("1", "https://a.example.com/", description=MARKER + "\nrunbook: x"),
    )})
    assert [m.id for m in client(http).list_managed()] == ["1"]


def test_an_app_the_token_cannot_see_is_an_error_not_an_empty_list():
    # an empty list would plan a create for every hostname in the cluster
    http = FakeGraphQL({"uptimeMonitors": {"data": {"app": None}}})
    with pytest.raises(ProviderError, match="app-1"):
        client(http).list_managed()


def test_reading_maps_enum_regions_back_to_config_names():
    http = FakeGraphQL({"uptimeMonitors": listing(
        monitor("1", "https://a.example.com/", regions=["EUROPE", "ASIA_PACIFIC"],
                warmupDuration=3, checkBodyContent="ok", monitorSslExpiration=True,
                notifierIds=["n1"], headers=[{"key": "X-A", "value": "1"}]),
    )})
    m = client(http).list_managed()[0]
    assert m.regions == ("europe", "asia-pacific")
    assert (m.warmup_minutes, m.check_body_content, m.ssl_expiration) == (3, "ok", True)
    assert m.notifier_ids == ("n1",)
    assert m.headers == (("X-A", "1"),)


# ------------------------------------------------------------------ writing
def test_create_sends_the_marker_and_the_notifiers():
    http = FakeGraphQL({"createUptimeMonitor":
                        {"data": {"createUptimeMonitor": {"id": "9"}}}})
    mid = client(http).create(want(notifier_ids=("n1", "n2")))
    _, query, variables = http.calls[0]
    m = variables["monitor"]
    assert mid == "9"
    assert variables["appId"] == "app-1"
    assert m["name"] == "shop.example.com"
    assert m["url"] == "https://shop.example.com/"
    assert m["description"] == MARKER
    assert m["regions"] == ["EUROPE", "NORTH_AMERICA"]
    assert m["warmupDuration"] == 2
    assert m["notifierIds"] == ["n1", "n2"]
    assert m["checkBodyContent"] is None


def test_ssl_expiration_is_only_sent_when_managed():
    http = FakeGraphQL({"createUptimeMonitor":
                        {"data": {"createUptimeMonitor": {"id": "9"}}}})
    client(http).create(want())
    client(http).create(want(ssl_expiration=True))
    assert "monitorSslExpiration" not in http.calls[0][2]["monitor"]
    assert http.calls[1][2]["monitor"]["monitorSslExpiration"] is True


def test_update_carries_back_what_a_human_set():
    # notifiers, headers and any text below the marker belong to whoever set them
    existing = ExistingMonitor(
        id="4", url="https://shop.example.com/", display_name="old name",
        regions=("europe",), warmup_minutes=0,
        description=MARKER + "\nrunbook: x", notifier_ids=("pager",),
        headers=(("Authorization", "Bearer s"),))
    http = FakeGraphQL({"updateUptimeMonitor":
                        {"data": {"updateUptimeMonitor": {"id": "4"}}}})
    client(http).update(existing, want(notifier_ids=("n1",)))
    _, _, variables = http.calls[0]
    m = variables["monitor"]
    assert variables["id"] == "4"
    assert m["name"] == "shop.example.com"
    assert m["notifierIds"] == ["pager"]
    assert m["headers"] == [{"key": "Authorization", "value": "Bearer s"}]
    assert m["description"] == MARKER + "\nrunbook: x"


def test_delete_names_the_monitor_and_the_app():
    http = FakeGraphQL({"deleteUptimeMonitor":
                        {"data": {"deleteUptimeMonitor": {"id": "4"}}}})
    existing = ExistingMonitor(id="4", url="https://x/", display_name="x",
                               regions=(), warmup_minutes=0)
    client(http).delete(existing)
    assert http.calls[0][2] == {"appId": "app-1", "id": "4"}


# ------------------------------------------------------------------ transport
def test_a_graphql_error_with_a_200_is_still_an_error():
    http = FakeGraphQL({"createUptimeMonitor": {
        "data": {"createUptimeMonitor": None},
        "errors": [{"message": "Url is invalid"}]}})
    with pytest.raises(ProviderError, match="Url is invalid"):
        client(http).create(want())


def test_the_token_goes_in_the_query_string():
    http = FakeGraphQL({"uptimeMonitors": listing()})
    client(http).list_managed()
    assert http.calls[0][0] == "https://appsignal.com/graphql?token=tok"


def test_the_token_never_appears_in_an_error():
    err = urllib.error.HTTPError("https://appsignal.com/graphql?token=tok", 401,
                                 "Unauthorized", {}, io.BytesIO(b"bad token"))
    http = FakeGraphQL({"uptimeMonitors": err})
    with pytest.raises(ProviderError) as exc:
        client(http).list_managed()
    assert "tok" not in str(exc.value).replace("token", "")
    assert exc.value.__cause__ is None
