"""Sends a real owner payout via the Revolut Business API - the live counterpart to the manual
"Mark as paid" attestation in finance/services.py. Kept as its own module rather than folded into
services.py: this is a distinct kind of side-effect (an external bank transfer with its own
idempotency key, failure modes, and two-phase "accepted vs settled" state) from the pure-Decimal
payout math (compute_regular_owner_payout) and the Sage-dispatch code services.py already owns.

Only ever called from staff/views.py::StaffFinancePayoutMarkPaidView, and only for a booking whose
owner has a EUR OwnerBankAccount on file (Owner.has_eur_bank_account) - see that view for the "one
button, auto-fallback" branching. compute_regular_owner_payout (finance/services.py) is EUR-only
already (no currency field in its output), so this only ever looks for a EUR account regardless of
the owner's own `currency` preference - a GBP-only OwnerBankAccount can be stored (ready for when
payouts themselves become currency-aware) but isn't usable by this flow yet.
"""
from dataclasses import dataclass

import requests
from django.utils import timezone

import env_settings
from finance.models import PayoutRecord
from properties.models import OwnerBankAccount


@dataclass
class PayoutResult:
    ok: bool
    record: 'PayoutRecord | None'
    error_message: 'str | None'


def send_owner_payout_via_revolut(booking, payout, paid_by) -> PayoutResult:
    """Creates (or reuses, via OwnerBankAccount.revolut_counterparty_id) a Revolut Business
    counterparty for the booking owner's EUR bank account, then creates a Transfer for
    payout['owner_balance']. A PayoutRecord is created ONLY if Revolut actually accepts the
    transfer (transfer.id present after .create()) - an outright rejection or a network error
    returns a clean failure with no record created, so the "Send payment" button on the Payouts
    tab stays live for staff to retry. An accepted-but-not-yet-settled transfer (transfer.state !=
    'completed') still creates the record, with status='in_progress' - real settlement is
    confirmed later via the klt-hooks webhook flipping status to 'paid'/'failed' (see klt-hooks'
    postgres_business_payouts.py)."""
    from libraries.banking.revolut_business import get_revolut_business_connection

    owner = booking.property.owner
    if owner is None:
        return PayoutResult(ok=False, record=None, error_message="Booking has no owner.")

    account = owner.bank_accounts.filter(currency=OwnerBankAccount.Currency.EUR).first()
    if account is None:
        return PayoutResult(ok=False, record=None, error_message="Owner has no EUR bank account on file.")

    connection = get_revolut_business_connection()
    if connection is None:
        return PayoutResult(ok=False, record=None, error_message="Could not connect to Revolut Business API - check credentials.")

    try:
        counterparty = _get_or_create_counterparty(connection, account)
        if counterparty is None:
            return PayoutResult(ok=False, record=None, error_message="Could not set up a Revolut counterparty for this account.")

        transfer = connection.transfer
        transfer.requestId = f"payout-{booking.reference}"
        transfer.account.id = env_settings.REVOLUT_BUSINESS_PAYOUT_ACCOUNT_ID
        transfer.counterparty = counterparty
        # NOT minor units (cents) - confirmed live, 2026-09-12: sending 100 here moved a real
        # €100.00 in the sandbox, not €1.00. POST /1.0/pay takes whole-currency amounts, unlike the
        # unrelated Merchant/checkout API (revolut.py) this project also uses, which IS cents-based
        # - the two products don't share a convention. A previous `int(x * 100)` here would have
        # sent every real owner payout at 100x the intended amount.
        transfer.amount = float(payout['owner_balance'])
        transfer.currency = account.currency
        transfer.reference = f"Rental payout {booking.reference}"
        transfer.create()
    except requests.RequestException as error:
        # A timeout/connection error here doesn't prove the transfer wasn't accepted server-side -
        # the deterministic request_id above makes a same-request retry safe at Revolut's layer,
        # but no PayoutRecord is created either way, so staff should check Revolut's dashboard
        # before manually retrying rather than assume this failed cleanly.
        return PayoutResult(ok=False, record=None, error_message=f"Revolut API request failed: {error}")

    if not transfer.id:
        return PayoutResult(ok=False, record=None, error_message="Revolut rejected the transfer - check the owner's bank details.")

    record = PayoutRecord.objects.create(
        booking=booking, amount=payout['owner_balance'], paid_by=paid_by,
        provider='revolut', status='paid' if transfer.state == 'completed' else 'in_progress',
        revolut_transfer_id=transfer.id, last_event_type=transfer.state, in_progress_at=timezone.now(),
    )
    return PayoutResult(ok=True, record=record, error_message=None)


def _get_or_create_counterparty(connection, account: OwnerBankAccount):
    """Reuses account.revolut_counterparty_id directly if cached - no verification round-trip,
    since Counterparty.get() (libraries/banking/revolut_business.py) doesn't return a clean
    success/failure signal to check (its return value is None either way; only self._values
    changes on success). If the cached counterparty was deleted directly in Revolut's own
    dashboard, the Transfer.create() call below simply fails cleanly instead (no PayoutRecord
    created, same as any other rejection) - one wasted API round-trip in that rare case, not a
    masked bug. Creates and caches a fresh counterparty otherwise; a valid counterparty is reusable
    across every future payout from this account regardless of any individual transfer's own
    outcome, so it's saved immediately on creation.

    EUR and GBP need entirely different account fields on the Revolut side (iban vs sort_code/
    accountNo) - mirrors OwnerBankAccount.clean()'s own currency branching."""
    if account.revolut_counterparty_id:
        counterparty = connection.counterparty
        counterparty.id = account.revolut_counterparty_id
        return counterparty

    counterparty = connection.counterparty
    counterparty.individualName = account.account_holder_name
    counterparty.account.currency = account.currency
    if account.currency == OwnerBankAccount.Currency.EUR:
        counterparty.account.iban = account.iban
        counterparty.account.country = account.iban[:2]
    else:
        counterparty.account.sortCode = account.sort_code
        counterparty.account.accountNo = account.account_number
        counterparty.account.country = 'GB'
    # Revolut rejects counterparty creation without at least address.country (confirmed live,
    # 2026-09-12, code 2101 "'address.country' is required") - OwnerBankAccount has no separate
    # postal-address fields on file, so this reuses the bank account's own country as the best
    # available answer. Revolut's docs recommend supplying the full address (street/city/postcode)
    # to reduce payment-disruption risk - worth adding to OwnerBankAccount as a real field if
    # transfers start bouncing on this in practice, not guessed at here.
    counterparty.address.country = counterparty.account.country
    counterparty.create()
    if not counterparty.id:
        return None

    account.revolut_counterparty_id = counterparty.id
    account.save(update_fields=['revolut_counterparty_id'])
    return counterparty
