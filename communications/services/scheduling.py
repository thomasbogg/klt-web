from datetime import timedelta

from django.utils import timezone

from communications.models import EmailTemplate, ScheduledEmail
from communications.registry import EMAIL_TYPES

# Maps an event-triggered slug to the queryset of bookings currently eligible to have a row
# created for it - deliberately hand-written per slug rather than generic, since there are only
# two of these and a real WHERE clause (payment__status='paid') is a much cheaper query on this
# project's slow/variable-latency remote Postgres (see project memory on preferring bulk/targeted
# DB operations here) than iterating every Booking and calling EMAIL_TYPES[...].eligible() in
# Python one row at a time.
def _event_triggered_candidates():
    from bookings.models import Booking
    return {
        'deposit_payment_received': Booking.objects.filter(payment__status='paid'),
        'balance_payment_received': Booking.objects.filter(balance_payment__status='paid'),
    }


def create_scheduled_emails_for_booking(booking):
    """Creates a pending ScheduledEmail row for every active, non-event-triggered EmailTemplate,
    regardless of current eligibility - eligibility is rechecked live by send_scheduled_email()
    itself when the row's date arrives (or a "Send now" click jumps it ahead), so a
    since-become-ineligible row is marked skipped with a reason rather than silently sent or
    silently missing (see ScheduledEmail's own docstring). Event-triggered types (payment-received
    confirmations) are deliberately skipped here - see sync_event_triggered_emails() below.

    Called once, at booking-creation time, from bookings/utils.py::create_booking() - NOT from
    create_owner_booking() (an owner's own stay never has a Payment/BalancePayment/hold to warn
    about, and "your booking is confirmed" isn't a useful email for a booking the owner just made
    themselves)."""
    for template in EmailTemplate.objects.filter(active=True):
        definition = EMAIL_TYPES.get(template.slug)
        if definition is None or definition.event_triggered:
            continue
        anchor = definition.anchor(booking)
        if anchor is None:
            continue
        ScheduledEmail.objects.get_or_create(
            booking=booking, template=template,
            defaults={'scheduled_for': anchor + timedelta(days=template.offset_days)},
        )


def sync_event_triggered_emails():
    """Creates (but does not send) a ScheduledEmail row, scheduled_for today, for any booking
    whose Payment/BalancePayment has already cleared but has no row yet for that event-triggered
    template. This is the reconciliation half of a belt-and-braces design alongside
    communications/signals.py's post_save handler - a Django signal alone would miss any
    status='paid' write klt-hooks makes directly via raw SQL against the shared Postgres
    (confirmed: postgres_bookings.py's mark_payment_paid/mark_balance_payment_paid are plain
    'UPDATE booking_payments'/'UPDATE booking_balance_payments' statements from a separate Flask
    process - Django's ORM signals can never observe a write that never goes through the ORM).
    That webhook route is currently suspended (see project memory on the dormant balance-payment
    webhook), but the whole point of this sweep is to not silently break the moment it's
    re-enabled. Safe to call as often as needed - get_or_create makes it a no-op for a booking
    that's already reconciled. Called both by send_due_scheduled_emails (the cron path, covering
    the future webhook route) and the post_save signal handler (the prompt path for today's actual
    live path: staff manually flipping Payment.status via the Django ORM on the Booking page)."""
    today = timezone.now().date()
    for slug, candidates in _event_triggered_candidates().items():
        template = EmailTemplate.objects.filter(slug=slug, active=True).first()
        if template is None:
            continue
        already_scheduled = set(
            ScheduledEmail.objects.filter(template=template).values_list('booking_id', flat=True)
        )
        for booking in candidates.exclude(pk__in=already_scheduled):
            ScheduledEmail.objects.get_or_create(
                booking=booking, template=template, defaults={'scheduled_for': today},
            )
