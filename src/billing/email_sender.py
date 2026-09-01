"""
SMTP delivery for billing statements (Hamilton Labs, 2026-09-01).

Real network I/O -- unlike src/maestro/adapters/whatsapp.py and
telegram.py, which are explicit stubs ("no live API calls... reports
what would have been sent instead"), this module actually dials out
via smtplib (stdlib, no new dependency). A billing statement is a live
financial document being sent to a real company, not a demo-stage
placeholder -- per this pass's own instruction: "real SMTP send...
not a stub." The WhatsApp/Telegram stub adapters are a separate
concern and are untouched by this module.

Fail-closed on missing configuration (src/config.py's
smtp_host/smtp_port/billing_statement_sender/
billing_statement_recipient): raises BillingConfigIncompleteError
rather than silently skipping the send or silently reporting success
with nothing actually sent. Mirrors this codebase's existing
fail-closed convention (CLAUDE.md's Fail-Closed Doctrine;
src/telemetry/trust.py's DeviceNotRegisteredError/
TelemetrySignatureInvalidError; src/airlock/profile_check.py's
ProfileIdMissingError/ProfileIdUnresolvableError) -- "missing
configuration" is exactly the kind of malformed precondition this
codebase always makes visible and never quietly absorbs.
smtp_username/smtp_password are deliberately NOT in the required set:
some SMTP relays genuinely require no auth, so their absence is not by
itself a misconfiguration.

send_statement_email() never lets smtplib's own exceptions propagate
uncaught -- every failure mode (auth failure, connection refused,
recipient refused, timeout, etc.) is caught and returned as a
EmailDeliveryResult(delivered=False, ...), same as the success case,
so src/billing/service.py can emit an evidence record for the attempt
either way, per this pass's "every send... including failed-send
attempts" instruction. BillingConfigIncompleteError is the one
exception this module still raises (not swallowed into a
DeliveryResult) -- a missing-config attempt never even reaches
smtplib, so there is no "send" to report a DeliveryResult for; the
caller is expected to catch it and record its own evidence entry (see
src/billing/service.py's generate_and_send_if_due()).
"""
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from src.billing.schemas import BillingStatement
from src.config import Settings

REQUIRED_SETTINGS = (
    "smtp_host",
    "smtp_port",
    "billing_statement_sender",
    "billing_statement_recipient",
)


class BillingConfigIncompleteError(RuntimeError):
    """One or more of the SMTP/recipient settings required to send a billing statement is missing."""


@dataclass(frozen=True)
class EmailDeliveryResult:
    delivered: bool
    detail: str


def _render_statement_text(statement: BillingStatement) -> str:
    lines = [
        "Synapse Statement of Accounts",
        f"Recipient: {statement.recipient}",
        f"Period: {statement.period_start.isoformat()} to {statement.period_end.isoformat()}",
        "",
        f"Claims processed: {statement.claims_processed}",
        f"GO: {statement.go_count}",
        f"NO_GO: {statement.no_go_count}",
        "NO_GO rate: "
        + (f"{statement.no_go_rate:.4f}" if statement.no_go_rate is not None else "n/a (no claims this period)"),
        "",
        "NO_GO breakdown by reason code:",
    ]
    if statement.no_go_breakdown_by_reason_code:
        for code, count in sorted(statement.no_go_breakdown_by_reason_code.items()):
            lines.append(f"  {code}: {count}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Generated at: {statement.generated_at.isoformat()}")
    return "\n".join(lines)


def send_statement_email(statement: BillingStatement, settings: Settings) -> EmailDeliveryResult:
    missing = [name for name in REQUIRED_SETTINGS if getattr(settings, name) in (None, "")]
    if missing:
        raise BillingConfigIncompleteError(
            f"Cannot send billing statement: missing required config value(s): {', '.join(missing)}."
        )

    message = EmailMessage()
    message["Subject"] = (
        f"Synapse Statement of Accounts — {statement.period_start.date()} to {statement.period_end.date()}"
    )
    message["From"] = settings.billing_statement_sender
    message["To"] = settings.billing_statement_recipient
    message.set_content(_render_statement_text(statement))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        return EmailDeliveryResult(delivered=False, detail=f"SMTP send failed: {exc}")

    return EmailDeliveryResult(delivered=True, detail=f"Sent to {settings.billing_statement_recipient}.")
