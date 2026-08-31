from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from bookings.models import BookingSettings, Charge


class Command(BaseCommand):
    """Run-once repair: backfills due_at_booking/due_at_balance on legacy Direct-sourced Charges
    whose split doesn't match their subtotal (basic_rental+admin) - these were imported without
    the due_at_booking/due_at_balance concept at all (see migrate_klt_data.py::migrate_charges,
    which never sets them), so they were simply never populated. Platform-sourced (Airbnb/
    Booking.com/Vrbo) bookings are deliberately excluded: staff/views.py's booking_detail view no
    longer shows the "Payments from guest" panel for those at all, since the guest pays the
    platform directly and PlatformPayout tracks what the platform pays out to us instead - that
    split concept doesn't apply to them.

    Uses BookingSettings.split_subtotal(), NOT compute_costs() - compute_costs() always derives
    its own fresh admin_fee_percent from whatever's passed in, which double-counts the admin fee
    already baked into subtotal (this was the actual bug behind staff/views.py's
    _recalculate_payment_split, now fixed to use split_subtotal() too). Skips any Charge whose
    booking has a Payment marked 'paid' - due_at_booking is a locked historical fact once the
    deposit is actually paid (same rule _recalculate_payment_split applies), and none of the
    currently-mismatched Direct charges have one, so this is a safety check, not the common case."""
    help = "Backfill due_at_booking/due_at_balance for mismatched Direct-sourced Charges."

    def handle(self, *args, **options):
        settings = BookingSettings.load()
        charges = (
            Charge.objects.exclude(basic_rental__isnull=True)
            .exclude(admin__isnull=True)
            .select_related('booking', 'booking__payment')
            .filter(booking__enquiry_source='Direct')
        )

        to_update = []
        skipped_paid = 0
        for charge in charges:
            subtotal = charge.total_rental + charge.admin
            due_total = (charge.due_at_booking or Decimal('0')) + (charge.due_at_balance or Decimal('0'))
            if abs(subtotal - due_total) <= Decimal('0.01'):
                continue
            payment = getattr(charge.booking, 'payment', None)
            if payment is not None and payment.status == 'paid':
                skipped_paid += 1
                continue
            due_at_booking, due_at_balance, balance_due_date = settings.split_subtotal(
                subtotal, arrival_date=charge.booking.arrival_date
            )
            charge.due_at_booking = due_at_booking
            charge.due_at_balance = due_at_balance
            charge.balance_due_date = balance_due_date
            to_update.append(charge)

        with transaction.atomic():
            Charge.objects.bulk_update(
                to_update, ['due_at_booking', 'due_at_balance', 'balance_due_date'], batch_size=500
            )

        self.stdout.write(f"Backfilled {len(to_update)} charges. Skipped {skipped_paid} with a paid deposit.")
