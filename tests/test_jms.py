"""Connection config for the SWIM transport."""

from pathlib import Path

import pytest

from avwx_swift.jms import JmsConfig, JmsConfigError, JmsMessage

REQUIRED = {
    "SWIM_USERNAME": "someone",
    "SWIM_PASSWORD": "secret",
    "SWIM_QUEUE": "someone.AIM_FNS.abc.OUT",
    "SWIM_URL": "tcps://ems1.swim.faa.gov:55443",
    "SWIM_VPN": "AIM_FNS",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*REQUIRED, "SWIM_CONNECTION_FACTORY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_env")
def test_from_env_reads_the_scds_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    config = JmsConfig.from_env()

    assert config.username == "someone"
    assert config.queue_name == REQUIRED["SWIM_QUEUE"]
    assert config.message_vpn == "AIM_FNS"


@pytest.mark.usefixtures("clean_env")
def test_from_env_names_every_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reporting only the first would mean five round trips to configure one connection."""
    monkeypatch.setenv("SWIM_USERNAME", "someone")

    with pytest.raises(JmsConfigError) as caught:
        JmsConfig.from_env()

    message = str(caught.value)
    assert "SWIM_USERNAME" not in message
    for name in ("SWIM_PASSWORD", "SWIM_QUEUE", "SWIM_URL", "SWIM_VPN"):
        assert name in message


@pytest.mark.usefixtures("clean_env")
def test_from_env_accepts_a_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name.replace("SWIM_", "FNS_"), value)

    assert JmsConfig.from_env(prefix="FNS_").username == "someone"


@pytest.mark.usefixtures("clean_env")
def test_overrides_beat_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    assert JmsConfig.from_env(username="other").username == "other"


def test_the_default_trust_store_exists() -> None:
    """Solace wants a directory of CA certificates, and gets one without extra setup."""
    config = JmsConfig(username="u", password="p", queue_name="q", url="tcps://h:1", message_vpn="V")
    assert config.trust_store_path.is_dir()
    assert any(config.trust_store_path.glob("*.pem"))


def test_certificate_validation_is_on_by_default() -> None:
    config = JmsConfig(username="u", password="p", queue_name="q", url="tcps://h:1", message_vpn="V")
    assert config.validate_certificate is True


def test_properties_carry_the_credentials_and_host() -> None:
    config = JmsConfig(
        username="u",
        password="p",
        queue_name="q",
        url="tcps://h:1",
        message_vpn="V",
        trust_store_path=Path("/tmp"),
    )

    properties = config.as_properties()

    assert "u" in properties.values()
    assert "tcps://h:1" in properties.values()
    assert "/tmp" in properties.values()


def test_message_property_lookup() -> None:
    message = JmsMessage(payload="<xml/>", properties={"a": "1"})
    assert message.property("a") == "1"
    assert message.property("missing", "fallback") == "fallback"
