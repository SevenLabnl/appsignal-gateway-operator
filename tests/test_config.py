"""Bad configuration fails at startup, not at the first API call."""
import pytest

from gateway_uptime.config import ConfigError, from_env

BASE = {"APPSIGNAL_TOKEN": "t", "APPSIGNAL_APP_ID": "app", "CLUSTER_NAME": "c"}


def env(**kw):
    return {**BASE, **kw}


@pytest.mark.parametrize("missing", sorted(BASE))
def test_each_required_setting_is_required(missing):
    e = dict(BASE)
    del e[missing]
    with pytest.raises(ConfigError, match=missing):
        from_env(e)


def test_defaults():
    cfg = from_env(BASE)
    assert cfg.regions == ("europe",)
    assert cfg.warmup_minutes == 0
    assert cfg.ssl_expiration is None
    assert cfg.notifier_ids == ()
    assert cfg.dry_run is False


def test_unknown_region_is_refused():
    with pytest.raises(ConfigError, match="eu"):
        from_env(env(REGIONS="eu"))


def test_regions_are_read_as_a_list():
    assert from_env(env(REGIONS="europe, north-america")).regions == \
        ("europe", "north-america")


def test_ssl_expiration_must_be_a_yes_or_no():
    assert from_env(env(SSL_EXPIRATION="true")).ssl_expiration is True
    assert from_env(env(SSL_EXPIRATION="false")).ssl_expiration is False
    with pytest.raises(ConfigError):
        # a number of days is a plausible mistake; it must not read as "on"
        from_env(env(SSL_EXPIRATION="14"))


def test_negative_warmup_is_refused():
    with pytest.raises(ConfigError):
        from_env(env(WARMUP_MINUTES="-1"))


def test_the_marker_names_the_cluster():
    assert from_env(BASE).owner_marker.endswith("cluster c")
