"""Sends a real owner payout via the Revolut Business API - the live counterpart to the manual
"Mark as paid" attestation in finance/services.py. Kept as its own module rather than folded into
services.py: this is a distinct kind of side-effect (an external bank transfer with its own
idempotency key, failure modes, and two-phase "accepted vs settled" state) from the pure-Decimal
payout math (compute_regular_owner_payout) and the Sage-dispatch code services.py already owns.

Only ever called from staff/views.py::StaffFinancePayoutMarkPaidView, and only for a booking whose
owner has bank details on file (Owner.has_bank_details) - see that view for the "one button,
auto-fallback" branching. v1 is EUR-only throughout, matching compute_regular_owner_payout's own
existing EUR-only scope (no currency field in its output; staff/templates/staff/finance_payouts.html
hardcodes the € symbol) - see the currency guard below.
"""
from dataclasses import dataclass

import requests
from django.utils import timezone

import env_settings
from finance.models import PayoutRecord


@dataclass
class PayoutResult:
    ok: bool
    record: 'PayoutRecord | None'
    error_message: 'str | None'


def send_owner_payout_via_revolut(booking, payout, paid_by) -> PayoutResult:
    """Creates (or reuses, via Owner.revolut_counterparty_id) a Revolut Business counterparty for
    the booking's owner, then creates a Transfer for payout['owner_balance']. A PayoutRecord is
    created ONLY if Revolut actually accepts the transfer (transfer.id present after .create()) -
    an outright rejection or a network error returns a clean failure with no record created, so the
    "Send payment" button on the Payouts tab stays live for staff to retry. An accepted-but-not-yet-
    settled transfer (transfer.state != 'completed') still creates the record, with
    status='in_progress' - real settlement is confirmed later via the klt-hooks webhook flipping
    status to 'paid'/'failed' (see klt-hooks' postgres_business_payouts.py)."""
    from libraries.banking.revolut_business import get_revolut_business_connection

    owner = booking.property.owner
    if owner is None or not owner.has_bank_details:
        return PayoutResult(ok=False, record=None, error_message="Owner has no bank details on file.")

    if owner.currency != 'EUR':
        return PayoutResult(
            ok=False, record=None,
            error_message="Live Revolut payouts only support EUR owners right now - use Mark as paid instead.",
        )

    connection = get_revolut_business_connection()
    if connection is None:
        return PayoutResult(ok=False, record=None, error_message="Could not connect to Revolut Business API - check credentials.")

    try:
        counterparty = _get_or_create_counterparty(connection, owner)
        if counterparty is None:
            return PayoutResult(ok=False, record=None, error_message="Could not set up a Revolut counterparty for this owner.")

        transfer = connection.transfer
        transfer.requestId = f"payout-{booking.reference}"
        transfer.account.id = env_settings.REVOLUT_BUSINESS_PAYOUT_ACCOUNT_ID
        transfer.counterparty = counterparty
        transfer.amount = int(payout['owner_balance'] * 100)
        transfer.currency = 'EUR'
        transfer.reference = f"Rental payout {booking.reference}"
        transfer.create()
    except requests.RequestException as error:
        # A timeout/connection error here doesn't prove the transfer wasn't accepted server-side -
        # the deterministic request_id above makes a same-request retry safe at Revolut's own
        # layer, but no PayoutRecord is created either way, so staff should check Revolut's
        # dashboard before manually retrying rather than assume this failed cleanly.
        return PayoutResult(ok=False, record=None, error_message=f"Revolut API request failed: {error}")

    if not transfer.id:
        return PayoutResult(ok=False, record=None, error_message="Revolut rejected the transfer - check the owner's bank details.")

    record = PayoutRecord.objects.create(
        booking=booking, amount=payout['owner_balance'], paid_by=paid_by,
        provider='revolut', status='paid' if transfer.state == 'completed' else 'in_progress',
        revolut_transfer_id=transfer.id, last_event_type=transfer.state, in_progress_at=timezone.now(),
    )
    return PayoutResult(ok=True, record=record, error_message=None)


def _get_or_create_counterparty(connection, owner):
    """Reuses owner.revolut_counterparty_id directly if cached - no verification round-trip, since
    Counterparty.get() (libraries/banking/revolut_business.py) doesn't return a clean
    success/failure signal to check (its return value is None either way; only self._values
    changes on success). If the cached counterparty was deleted directly in Revolut's own
    dashboard, the Transfer.create() call below simply fails cleanly instead (no PayoutRecord
    created, same as any other rejection) - one wasted API round-trip in that rare case, not a
    masked bug. Creates and caches a fresh counterparty otherwise; a valid counterparty is reusable
    across every future payout for this owner regardless of any individual transfer's own outcome,
    so it's saved immediately on creation."""
    if owner.revolut_counterparty_id:
        counterparty = connection.counterparty
        counterparty.id = owner.revolut_counterparty_id
        return counterparty

    counterparty = connection.counterparty
    counterparty.individualName = owner.bank_account_holder_name
    counterparty.account.iban = owner.bank_iban
    counterparty.account.currency = 'EUR'
    counterparty.account.country = owner.bank_iban[:2]
    counterparty.create()
    if not counterparty.id:
        return None

    owner.revolut_counterparty_id = counterparty.id
    owner.save(update_fields=['revolut_counterparty_id'])
    return counterparty
