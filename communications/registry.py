"""What each EmailTemplate slug actually means: anchor date, live eligibility, and template
context. EmailTemplate itself (communications/models.py) only holds staff-editable subject/body/
offset_days - several real emails need live domain checks a DB row can't express (already paid,
already submitted, no email on file), so that half of the definition lives here in code, the
direct successor to legacy klt-management-software's dates.py classmethod-per-type windows.

Lazy imports throughout (bookings.models etc.) - this module is imported from bookings/utils.py
(create_booking()), which would otherwise create a circular import at module load time, same
reason create_booking() itself lazily imports its own models.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from django.urls import reverse
from django.utils import timezone

import env_settings


def _absolute_url(url_name, *args):
    return env_settings.SITE_BASE_URL.rstrip('/') + reverse(url_name, args=args)


def deposit_bank_details_submitted(details):
    """No existing 'is this complete' concept for DepositBankDetails anywhere else in the
    codebase (BookingManageDepositView just displays whatever fields exist, get_or_create'd blank
    on first visit - confirmed by direct code check, not assumed) - defined fresh here for this
    email's eligibility only. Treated as submitted once the guest has given at least one field that
    actually identifies an account (iban, or a swift code, or a plain account number) - bank_name/
    account_name/bank_address alone don't count, since none of those alone would let a transfer
    actually be made."""
    return bool(details.iban or details.swift_code or details.account_number)


def guest_registration_complete(registration):
    """No existing 'is this complete' concept for GuestRegistration either (same situation as
    deposit_bank_details_submitted above) - has_nif=True needs only nif_number; has_nif=False
    needs every field BookingManageGuestRegistrationsView itself requires server-side."""
    if registration.has_nif is True:
        return bool(registration.nif_number)
    if registration.has_nif is False:
        return all([
            registration.birth_date, registration.place_of_birth, registration.nationality,
            registration.country_of_residence, registration.id_type, registration.id_number,
            registration.issued_by,
        ])
    return False


def all_guests_registered(booking):
    from bookings.models import GuestRegistration
    party = list(booking.party.all())
    if not party:
        return False
    registrations = {
        row.booking_guest_id: row
        for row in GuestRegistration.objects.filter(booking_guest__booking=booking)
    }
    return all(
        guest.pk in registrations and guest_registration_complete(registrations[guest.pk])
        for guest in party
    )


def _booking_is_live(booking):
    from staff.utils import CLOSED_STATUSES
    return booking.enquiry_status not in CLOSED_STATUSES


def _guest_context(booking):
    charge = getattr(booking, 'charges', None)
    context = {
        'guest_first_name': booking.guest.first_name or booking.guest.last_name,
        'guest_full_name': str(booking.guest),
        'property_name': booking.property.title,
        'reference': booking.reference,
        'arrival_date': booking.arrival_date,
        'departure_date': booking.departure_date,
        'manage_hub_url': _absolute_url('bookings:manage_hub', booking.reference),
        'pay_url': _absolute_url('bookings:pay', booking.reference),
        # NB: "deposit" is overloaded in this codebase - manage_deposit_url below is the SECURITY
        # deposit bank-details page (DepositBankDetails), unrelated to the booking Payment deposit.
        # balance_details_url is the actual "pay your balance" page - don't conflate the two.
        'manage_deposit_url': _absolute_url('bookings:manage_deposit', booking.reference),
        'balance_details_url': _absolute_url('bookings:balance_details', booking.reference),
        'manage_guest_registrations_url': _absolute_url('bookings:manage_guest_registrations', booking.reference),
        'manage_location_url': _absolute_url('bookings:manage_location', booking.reference),
        'manage_arrival_departure_url': _absolute_url('bookings:manage_arrival_departure', booking.reference),
    }
    if charge is not None:
        due_now, due_now_currency = charge.due_at_booking_in_charge_currency()
        context.update({'amount_due_now': due_now, 'amount_due_now_currency': due_now_currency})
        if charge.due_at_balance:
            due_balance, due_balance_currency = charge.due_at_balance_in_charge_currency()
            context.update({
                'amount_due_balance': due_balance, 'amount_due_balance_currency': due_balance_currency,
                'balance_due_date': charge.balance_due_date,
            })
    return context


def _security_deposit_still_needed(booking):
    from bookings.models import DepositBankDetails
    charge = getattr(booking, 'charges', None)
    if charge is None or not charge.security:
        return False
    details = DepositBankDetails.objects.filter(booking=booking).first()
    if details is None:
        return True
    return not deposit_bank_details_submitted(details)


def _mark_balance_reminder_sent(booking):
    booking.balance_payment.reminder_sent = True
    booking.balance_payment.reminder_sent_at = timezone.now()
    booking.balance_payment.save(update_fields=['reminder_sent', 'reminder_sent_at'])


@dataclass
class EmailDefinition:
    audience: str  # EmailTemplate.Audience value - used to validate against the DB row, not read from it
    anchor: Callable[[object], Optional[date]]
    eligible: Callable[[object], bool]
    context: Callable[[object], dict]
    recipient_email: Callable[[object], Optional[str]]
    on_sent: Optional[Callable[[object], None]] = None
    # True for a type whose ScheduledEmail row can only be created once some other event has
    # already happened (a payment clearing) rather than at booking-creation time - these are
    # created by the reconciliation sweep in send_due_scheduled_emails, not create_booking().
    event_triggered: bool = False


EMAIL_TYPES: dict[str, EmailDefinition] = {

    'guest_booking_confirmation': EmailDefinition(
        audience='guest',
        anchor=lambda booking: date.today(),
        eligible=lambda booking: bool(booking.guest.email) and _booking_is_live(booking),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
    ),

    'owner_booking_confirmation': EmailDefinition(
        audience='owner',
        anchor=lambda booking: date.today(),
        eligible=lambda booking: (
            not booking.is_owner and booking.property.owner is not None
            and bool(booking.property.owner.email) and _booking_is_live(booking)
        ),
        context=lambda booking: {**_guest_context(booking), 'owner_name': booking.property.owner.name},
        recipient_email=lambda booking: booking.property.owner.email if booking.property.owner else None,
    ),

    'deposit_payment_received': EmailDefinition(
        audience='guest',
        anchor=lambda booking: (booking.payment.paid_at.date() if getattr(booking, 'payment', None)
                                 and booking.payment.paid_at else None),
        eligible=lambda booking: (
            hasattr(booking, 'payment') and booking.payment.status == 'paid' and bool(booking.guest.email)
        ),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
        event_triggered=True,
    ),

    'balance_payment_received': EmailDefinition(
        audience='guest',
        anchor=lambda booking: (booking.balance_payment.paid_at.date()
                                 if getattr(booking, 'balance_payment', None)
                                 and booking.balance_payment.paid_at else None),
        eligible=lambda booking: (
            hasattr(booking, 'balance_payment') and booking.balance_payment.status == 'paid'
            and bool(booking.guest.email)
        ),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
        event_triggered=True,
    ),

    'hold_expiry_warning_wise': EmailDefinition(
        audience='guest',
        anchor=lambda booking: booking.hold_expires_at.date() if booking.hold_expires_at else None,
        eligible=lambda booking: (
            getattr(booking, 'payment', None) is not None and booking.payment.provider == 'wise'
            and booking.payment.status not in ('paid', 'failed', 'cancelled')
            and booking.enquiry_status in env_settings.PROVISIONAL_BOOKING_STATUSES
            and booking.hold_expires_at is not None and booking.hold_expires_at > timezone.now()
            and bool(booking.guest.email)
        ),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
    ),

    'balance_payment_reminder': EmailDefinition(
        audience='guest',
        anchor=lambda booking: booking.arrival_date,
        eligible=lambda booking: (
            hasattr(booking, 'balance_payment') and booking.balance_payment.status != 'paid'
            and _booking_is_live(booking) and bool(booking.guest.email)
        ),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
        on_sent=_mark_balance_reminder_sent,
    ),

    'security_deposit_request': EmailDefinition(
        audience='guest',
        anchor=lambda booking: booking.arrival_date,
        eligible=lambda booking: _security_deposit_still_needed(booking) and _booking_is_live(booking)
        and bool(booking.guest.email),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
    ),

    'guest_registration_reminder': EmailDefinition(
        audience='guest',
        anchor=lambda booking: booking.arrival_date,
        eligible=lambda booking: (
            not all_guests_registered(booking) and _booking_is_live(booking) and bool(booking.guest.email)
        ),
        context=_guest_context,
        recipient_email=lambda booking: booking.guest.email or None,
    ),
}


# Documentation only, shown to staff on the Settings > Emails template-editing page (a static list
# kept manually in sync with what each slug's context() callable above actually produces - the
# callables themselves need a real Booking to run, so can't be introspected generically here).
PLACEHOLDER_KEYS = {
    'guest_booking_confirmation': (
        'guest_first_name', 'guest_full_name', 'property_name', 'reference', 'arrival_date',
        'departure_date', 'amount_due_now', 'amount_due_now_currency', 'amount_due_balance',
        'amount_due_balance_currency', 'balance_due_date', 'manage_hub_url',
    ),
    'owner_booking_confirmation': (
        'owner_name', 'guest_full_name', 'property_name', 'reference', 'arrival_date', 'departure_date',
    ),
    'deposit_payment_received': (
        'guest_first_name', 'property_name', 'reference', 'arrival_date', 'departure_date',
        'amount_due_now', 'amount_due_now_currency', 'manage_hub_url',
    ),
    'balance_payment_received': (
        'guest_first_name', 'property_name', 'reference', 'arrival_date', 'departure_date',
        'manage_hub_url',
    ),
    'hold_expiry_warning_wise': (
        'guest_first_name', 'property_name', 'reference', 'arrival_date', 'amount_due_now',
        'amount_due_now_currency', 'pay_url',
    ),
    'balance_payment_reminder': (
        'guest_first_name', 'property_name', 'reference', 'arrival_date', 'amount_due_balance',
        'amount_due_balance_currency', 'balance_due_date', 'balance_details_url', 'manage_hub_url',
    ),
    'security_deposit_request': (
        'guest_first_name', 'property_name', 'reference', 'arrival_date', 'manage_deposit_url',
    ),
    'guest_registration_reminder': (
        'guest_first_name', 'property_name', 'reference', 'arrival_date',
        'manage_guest_registrations_url', 'manage_hub_url',
    ),
}


DEFAULT_OFFSET_DAYS = {
    'guest_booking_confirmation': 0,
    'owner_booking_confirmation': 0,
    'deposit_payment_received': 0,
    'balance_payment_received': 0,
    'hold_expiry_warning_wise': -1,
    # Matches BookingSettings.balance_reminder_days_before_arrival's current default (63) - kept
    # as an independent, staff-editable number here rather than read live from BookingSettings, so
    # this row's own offset_days field is a real, working knob (see EmailTemplate's docstring).
    # BookingSettings.balance_reminder_days_before_arrival keeps driving the admin's own
    # BalanceReminderDueFilter fallback independently - the two aren't wired together, so editing
    # one won't move the other.
    'balance_payment_reminder': -63,
    'security_deposit_request': -14,
    'guest_registration_reminder': -10,
}
