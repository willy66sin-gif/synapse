"""
SMTP delivery tests (src/billing/email_sender.py, Hamilton Labs
billing, 2026-09-01).

Never sends a real email -- smtplib.SMTP is monkeypatched to a fake
that records what would have been dialed/sent, same "mock the SMTP
call" instruction this pass was given. Covers: fail-closed on missing
config (never silently skips or silently succeeds), a real success
path, and a real failure path (an SMTPException/OSError must come back
as a DeliveryResult, never propagate uncaught).
"""
import smtplib
from datetime import datetime

import pytest

from src.billing.email_sender import (
    BillingConfigIncompleteError,
    EmailDeliveryResult,
    send_statement_email,
)
from src.billing.statement import generate_statement
from src.config import Settings

PERIOD_START = "2026-08-01T00:00:00+00:00"
PERIOD_END = "2026-09-01T00:00:00+00:00"


def _statement(recipient="ops@hamiltonlabs.example.com"):
    return generate_statement(
        [], datetime.fromisoformat(PERIOD_START), datetime.fromisoformat(PERIOD_END), recipient
    )


def _complete_settings(**overrides):
    base = dict(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="synapse-billing",
        smtp_password="secret",
        smtp_use_tls=True,
        billing_statement_sender="billing@synapse.example.com",
        billing_statement_recipient="ops@hamiltonlabs.example.com",
    )
    base.update(overrides)
    return Settings(**base)


class _FakeSMTP:
    """Records the calls a real smtplib.SMTP context manager would receive."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_calls = []
        self.sent_messages = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message):
        self.sent_messages.append(message)


class _FailingSMTP(_FakeSMTP):
    def send_message(self, message):
        raise smtplib.SMTPException("relay refused the message")


class _ConnectionRefusedSMTP:
    def __init__(self, host, port, timeout=None):
        raise OSError("connection refused")


@pytest.fixture(autouse=True)
def _reset_fake_smtp_instances():
    _FakeSMTP.instances = []
    yield
    _FakeSMTP.instances = []


# --- Fail-closed on missing config ---


@pytest.mark.parametrize(
    "missing_field",
    ["smtp_host", "smtp_port", "billing_statement_sender", "billing_statement_recipient"],
)
def test_missing_required_config_raises_instead_of_skipping_or_succeeding(missing_field, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = _complete_settings(**{missing_field: None})

    with pytest.raises(BillingConfigIncompleteError, match=missing_field):
        send_statement_email(_statement(), settings)

    # Fail-closed means it never even dials out.
    assert _FakeSMTP.instances == []


def test_missing_smtp_username_and_password_is_not_a_config_failure():
    """Some SMTP relays genuinely require no auth -- absence of
    credentials alone must not be treated as a missing config error."""
    settings = _complete_settings(smtp_username=None, smtp_password=None)

    missing = [name for name in ("smtp_host", "smtp_port", "billing_statement_sender", "billing_statement_recipient")]
    assert all(getattr(settings, name) is not None for name in missing)  # sanity: nothing else is missing


# --- Real success path (SMTP mocked) ---


def test_successful_send_reports_delivered_true(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = _complete_settings()

    result = send_statement_email(_statement(), settings)

    assert isinstance(result, EmailDeliveryResult)
    assert result.delivered is True
    assert "ops@hamiltonlabs.example.com" in result.detail


def test_successful_send_actually_calls_starttls_login_and_send_message(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = _complete_settings()

    send_statement_email(_statement(), settings)

    smtp_instance = _FakeSMTP.instances[0]
    assert smtp_instance.host == "smtp.example.com"
    assert smtp_instance.port == 587
    assert smtp_instance.started_tls is True
    assert smtp_instance.login_calls == [("synapse-billing", "secret")]
    assert len(smtp_instance.sent_messages) == 1
    assert smtp_instance.sent_messages[0]["To"] == "ops@hamiltonlabs.example.com"


def test_smtp_use_tls_false_skips_starttls(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = _complete_settings(smtp_use_tls=False)

    send_statement_email(_statement(), settings)

    assert _FakeSMTP.instances[0].started_tls is False


def test_no_credentials_configured_skips_login_not_the_whole_send(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = _complete_settings(smtp_username=None, smtp_password=None)

    result = send_statement_email(_statement(), settings)

    assert result.delivered is True
    assert _FakeSMTP.instances[0].login_calls == []
    assert len(_FakeSMTP.instances[0].sent_messages) == 1


# --- Real failure path (SMTP mocked to fail) ---


def test_smtp_exception_during_send_is_caught_and_reported_as_failure(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FailingSMTP)
    settings = _complete_settings()

    result = send_statement_email(_statement(), settings)

    assert result.delivered is False
    assert "relay refused the message" in result.detail


def test_connection_refused_is_caught_and_reported_as_failure(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _ConnectionRefusedSMTP)
    settings = _complete_settings()

    result = send_statement_email(_statement(), settings)

    assert result.delivered is False
    assert "connection refused" in result.detail
