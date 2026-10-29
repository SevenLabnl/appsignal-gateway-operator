"""The rules for which hostnames get a monitor.

These are the decisions people will disagree with, so they are the ones worth pinning
down.
"""
from gateway_uptime.config import Config
from gateway_uptime.selector import monitors_for, monitors_for_all

CFG = Config(
    token="t",
    app_id="app",
    cluster_name="test-cluster",
    exclude_suffixes=("dev.example.com", "staging.example.com"),
    warmup_minutes=3,
    notifier_ids=("n1",),
)


def route(name="app", ns="team", hosts=(), annotations=None):
    return {
        "metadata": {"name": name, "namespace": ns,
                     "annotations": annotations or {}},
        "spec": {"hostnames": list(hosts)},
    }


def urls(ms):
    return [m.url for m in ms]


def test_production_hostname_is_monitored():
    ms = monitors_for(route(hosts=["shop.example.com"]), CFG)
    assert urls(ms) == ["https://shop.example.com/"]


def test_excluded_suffix_is_skipped():
    assert monitors_for(route(hosts=["app.dev.example.com"]), CFG) == []


def test_exclusion_matches_the_domain_itself_not_just_subdomains():
    assert monitors_for(route(hosts=["dev.example.com"]), CFG) == []


def test_exclusion_does_not_match_a_lookalike_domain():
    # notdev.example.com must not be caught by the dev.example.com rule
    ms = monitors_for(route(hosts=["notdev.example.com"]), CFG)
    assert urls(ms) == ["https://notdev.example.com/"]


def test_wildcard_hostnames_are_never_monitored():
    assert monitors_for(route(hosts=["*.example.com"]), CFG) == []


def test_opt_out_wins_over_everything():
    r = route(hosts=["shop.example.com"],
              annotations={"appsignal-gateway-operator/enabled": "false"})
    assert monitors_for(r, CFG) == []


def test_opt_in_overrides_an_excluded_suffix():
    r = route(hosts=["demo.dev.example.com"],
              annotations={"appsignal-gateway-operator/enabled": "true"})
    assert urls(monitors_for(r, CFG)) == ["https://demo.dev.example.com/"]


def test_path_annotation_is_used_and_normalised():
    r = route(hosts=["api.example.com"],
              annotations={"appsignal-gateway-operator/path": "healthz"})
    assert urls(monitors_for(r, CFG)) == ["https://api.example.com/healthz"]


def test_body_check_and_warmup_can_be_set_per_route():
    r = route(hosts=["api.example.com"], annotations={
        "appsignal-gateway-operator/check-body-content": "ok",
        "appsignal-gateway-operator/warmup": "10",
    })
    m = monitors_for(r, CFG)[0]
    assert m.check_body_content == "ok"
    assert m.warmup_minutes == 10


def test_a_bad_warmup_falls_back_to_the_default():
    for bad in ("5m", "-1", "soon"):
        r = route(hosts=["api.example.com"], annotations={"appsignal-gateway-operator/warmup": bad})
        assert monitors_for(r, CFG)[0].warmup_minutes == 3


def test_defaults_come_from_config():
    m = monitors_for(route(hosts=["a.example.com"]), CFG)[0]
    assert m.warmup_minutes == 3
    assert m.regions == CFG.regions
    assert m.notifier_ids == ("n1",)
    assert m.check_body_content == ""


def test_the_monitor_is_named_after_the_hostname():
    # the monitor list shows the name and not the URL, so the name has to say which
    # hostname this is
    m = monitors_for(route(name="shop", ns="acme", hosts=["a.example.com"]), CFG)[0]
    assert m.display_name == "a.example.com"


def test_a_non_root_path_is_part_of_the_name():
    # two checks on one host must stay distinguishable in that list
    m = monitors_for(route(hosts=["a.example.com"],
                           annotations={"appsignal-gateway-operator/path": "/healthz"}),
                     CFG)[0]
    assert m.display_name == "a.example.com/healthz"


def test_hostnames_are_lowercased():
    m = monitors_for(route(hosts=["Shop.Example.COM"]), CFG)[0]
    assert m.url == "https://shop.example.com/"


def test_one_route_can_produce_several_monitors():
    ms = monitors_for(route(hosts=["a.example.com", "b.example.com"]), CFG)
    assert len(ms) == 2


def test_duplicate_hostnames_across_routes_collapse_to_one_monitor():
    # the http redirect route and the https route both claim the hostname; monitoring
    # it twice would double the noise and the bill
    routes = [route(name="web", hosts=["shop.example.com"]),
              route(name="web-redirect", hosts=["shop.example.com"])]
    assert len(monitors_for_all(routes, CFG)) == 1


def test_same_host_different_paths_are_separate_monitors():
    routes = [route(name="web", hosts=["shop.example.com"]),
              route(name="api", hosts=["shop.example.com"],
                    annotations={"appsignal-gateway-operator/path": "/api"})]
    assert len(monitors_for_all(routes, CFG)) == 2


def test_route_without_hostnames_produces_nothing():
    assert monitors_for(route(hosts=[]), CFG) == []


# ------------------------------------------------------------------ redirect pairs
# A hostname is normally served by two routes: the real one on :443 and a companion on
# :80 that only redirects. Both claim the hostname, which is where the next rules come
# from.

def redirect_route(name, ns="team", hosts=(), annotations=None):
    return {
        "metadata": {"name": name, "namespace": ns,
                     "annotations": annotations or {}},
        "spec": {"hostnames": list(hosts),
                 "rules": [{"filters": [{"type": "RequestRedirect"}]}]},
    }


def backend_route(name, ns="team", hosts=(), annotations=None):
    return {
        "metadata": {"name": name, "namespace": ns,
                     "annotations": annotations or {}},
        "spec": {"hostnames": list(hosts),
                 "rules": [{"backendRefs": [{"name": "svc", "port": 80}]}]},
    }


def test_the_serving_route_wins_over_the_redirect():
    # both produce the same monitor, and the winner decides what the log line says —
    # "created monitor for X (team/web)" is more use than naming the redirect
    ms = monitors_for_all([redirect_route("web-redirect", hosts=["shop.example.com"]),
                           backend_route("web", hosts=["shop.example.com"])], CFG)
    assert len(ms) == 1
    assert ms[0].route == "web"


def test_order_of_the_pair_does_not_matter():
    ms = monitors_for_all([backend_route("web", hosts=["shop.example.com"]),
                           redirect_route("web-redirect",
                                          hosts=["shop.example.com"])], CFG)
    assert ms[0].route == "web"


def test_opting_out_one_route_drops_the_hostname_entirely():
    # otherwise the monitor stays under the redirect route's name, which looks exactly
    # like the annotation being ignored
    ms = monitors_for_all([
        backend_route("web", hosts=["shop.example.com"],
                      annotations={"appsignal-gateway-operator/enabled": "false"}),
        redirect_route("web-redirect", hosts=["shop.example.com"]),
    ], CFG)
    assert ms == []


def test_opting_out_the_redirect_also_drops_the_hostname():
    ms = monitors_for_all([
        backend_route("web", hosts=["shop.example.com"]),
        redirect_route("web-redirect", hosts=["shop.example.com"],
                       annotations={"appsignal-gateway-operator/enabled": "false"}),
    ], CFG)
    assert ms == []


def test_opting_out_one_hostname_leaves_the_others_alone():
    ms = monitors_for_all([
        backend_route("a", hosts=["a.example.com"],
                      annotations={"appsignal-gateway-operator/enabled": "false"}),
        backend_route("b", hosts=["b.example.com"]),
    ], CFG)
    assert urls(ms) == ["https://b.example.com/"]


def test_recognising_a_redirect_only_route():
    from gateway_uptime.selector import is_redirect_only
    assert not is_redirect_only(backend_route("web", hosts=["a.example.com"]))
    assert is_redirect_only(redirect_route("web-r", hosts=["a.example.com"]))
    # no rules at all is not a redirect either
    assert not is_redirect_only(route(hosts=["a.example.com"]))


def test_a_path_on_the_serving_route_does_not_leave_the_redirect_behind():
    # the serving route checks /healthz; its :80 companion must not go on claiming the
    # root, or one hostname gets two monitors
    ms = monitors_for_all([
        backend_route("api", hosts=["api.example.com"],
                      annotations={"appsignal-gateway-operator/path": "/healthz"}),
        redirect_route("api-redirect", hosts=["api.example.com"]),
    ], CFG)
    assert urls(ms) == ["https://api.example.com/healthz"]


def test_a_hostname_served_only_by_a_redirect_still_gets_one():
    # a www host answering 308 to the apex serves nothing else; the redirect is the
    # whole product there
    ms = monitors_for_all([
        redirect_route("www", hosts=["www.example.com"]),
        backend_route("web", hosts=["example.com"]),
    ], CFG)
    assert urls(ms) == ["https://example.com/", "https://www.example.com/"]


def test_the_redirect_is_ignored_even_without_annotations():
    ms = monitors_for_all([
        backend_route("web", hosts=["shop.example.com"]),
        redirect_route("web-redirect", hosts=["shop.example.com"]),
    ], CFG)
    assert len(ms) == 1
    assert ms[0].route == "web"
