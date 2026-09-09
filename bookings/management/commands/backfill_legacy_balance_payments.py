from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from bookings.models import BalancePayment, Booking, BookingSettings, Charge, Payment
from bookings.utils import determine_payment_provider


class Command(BaseCommand):
    """Repeatable, per Thomas 2026-09-09: migrate_klt_data.py's migrate_bookings()/migrate_charges()
    never create a Payment or BalancePayment row for a migrated (legacy pims_id) booking, and never
    set due_at_booking/due_at_balance/balance_due_date either (see those functions' own docstrings)
    - by design, since payment for a legacy booking was tracked in the old klt_main.db/PIMS system,
    not here. But an already-confirmed legacy booking that hasn't arrived yet genuinely still owes
    its balance through klt-web now the guest can self-serve it via the Manage Booking hub - without
    a BalancePayment row, is_balance_paid() (bookings/views.py) treats "no row at all" as "nothing
    outstanding", which is wrong for one of these: the deposit really was collected (that's what
    'Booking confirmed' already means for a Direct booking), but the balance has not been.

    Retrofits klt-web's own online payment-tracking structure using the exact same 25%/75% split
    BookingSettings already applies to a brand new booking (BookingSettings.split_subtotal()) -
    deposit is marked Payment(status='paid') outright (already collected, outside klt-web), balance
    gets a fresh BalancePayment(status='pending') due balance_due_days_before_arrival before
    arrival, same as any other two-stage booking.

    Both rows are created via bulk_create, not .save() - deliberately, not just for speed: Payment/
    BalancePayment both have a post_save signal (communications/signals.py) that emails the guest a
    "payment received" confirmation the moment status='paid' is saved through the ORM. Retrofitting
    43 legacy deposits at once must never fire 43 real "we just received your payment" emails for
    money that was actually collected months or years ago - bulk_create skips signals entirely,
    which is exactly the point here, not a side effect to work around.

    Idempotent and safe to re-run: only ever touches a Direct, non-owner, 'Booking confirmed',
    legacy-linked (pims_id set) booking with NO existing Payment/BalancePayment row, arriving today
    or later. Explicitly meant to be repeated - every future klt_main.db re-sync (before full
    go-live) will bring in more bookings exactly like the 55 this was first written against,
    2026-09-09 - see migrate_klt_data.py's own note pointing here.

    Two classes of row are surfaced but deliberately NOT silently folded into the bulk write:
    - A Charge priced at exactly 0 (basic_rental+admin) - no real balance to collect, and
      fabricating a payment structure for it would be actively misleading. Likely a comp stay or a
      legacy row whose real pricing was never entered - flagged for a human to look at instead.
    - A GBP-quoted Charge with no gbp_conversion_rate frozen (the same live gap just fixed in
      StaffBookingDetailView._update_booking() for anyone setting currency going forward). This
      command DOES freeze the current live BookingSettings rate for these while backfilling their
      payment structure, since there's no better source of truth for what they were actually
      quoted - but reports each one it touched so a rate that doesn't match what the guest was
      actually told can be corrected by hand.

    Also zeroes Charge.security for every in-scope booking (2026-09-09, per Thomas - same session,
    right after the payment-structure retrofit above) - these are cash-at-check-in deposits set by
    the legacy PIMS process, not klt-web's own compute_deposit_waiver(), and Thomas's call is that
    none of them should carry one. Runs independently of the payment-structure step above (a
    booking already handled on an earlier run - already_done below - still gets its security
    cleared here if it hasn't been already), so this half alone is also safe to re-run."""
    help = (
        "Retrofit Payment/BalancePayment rows (25%/75% split) onto upcoming, confirmed, "
        "legacy-linked Direct bookings that never got klt-web's own online payment structure."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true', help="Write the changes. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        apply = options['apply']
        settings = BookingSettings.load()

        bookings = Booking.objects.filter(
            enquiry_source='Direct', is_owner=False, enquiry_status='Booking confirmed',
            pims_id__isnull=False, arrival_date__gte=date.today(),
        ).select_related('charges', 'payment', 'balance_payment')

        payments_to_create = []
        balance_payments_to_create = []
        charges_to_update = []
        zero_priced = []
        gbp_rate_backfilled = []
        security_cleared = []
        already_done = 0
        no_charge = 0

        for booking in bookings:
            charge = getattr(booking, 'charges', None)
            if charge is None:
                no_charge += 1
                continue

            charge_dirty = False

            # Independent of the payment-structure branch below - a booking already handled on an
            # earlier run still gets its security cleared here if it hasn't been already.
            if charge.security:
                charge.security = Decimal('0.00')
                charge_dirty = True
                security_cleared.append(booking)

            if charge.total_rental is None:
                pass  # unpriced Charge - nothing else to do here, but security may still be cleared above.
            elif getattr(booking, 'payment', None) is not None or getattr(booking, 'balance_payment', None) is not None:
                already_done += 1
            else:
                subtotal = charge.total_rental + charge.admin
                if subtotal == 0:
                    zero_priced.append(booking)
                else:
                    expected_booking, expected_balance, expected_date = settings.split_subtotal(
                        subtotal, arrival_date=booking.arrival_date,
                    )
                    if (charge.due_at_booking, charge.due_at_balance, charge.balance_due_date) != (
                        expected_booking, expected_balance, expected_date,
                    ):
                        charge.due_at_booking = expected_booking
                        charge.due_at_balance = expected_balance
                        charge.balance_due_date = expected_date
                        charge_dirty = True

                    if charge.currency == 'GBP' and charge.gbp_conversion_rate is None:
                        charge.gbp_conversion_rate = settings.gbp_conversion_rate
                        charge_dirty = True
                        gbp_rate_backfilled.append(booking)

                    provider = determine_payment_provider(booking.arrival_date)
                    payments_to_create.append(Payment(booking=booking, provider=provider, status='paid'))
                    if charge.due_at_balance > 0:
                        balance_payments_to_create.append(
                            BalancePayment(booking=booking, provider=provider, status='pending'),
                        )

            if charge_dirty:
                charges_to_update.append(charge)

        self.stdout.write(f"Bookings in scope: {bookings.count()}")
        self.stdout.write(f"  No Charge row at all (skipped): {no_charge}")
        self.stdout.write(f"  Security deposit cleared to 0.00: {len(security_cleared)}")
        for b in security_cleared:
            self.stdout.write(f"    {b.reference} ({b.property})")
        self.stdout.write(f"  Already has Payment/BalancePayment (payment structure skipped): {already_done}")
        self.stdout.write(f"  Zero-priced Charge (payment structure skipped - flag for review): {len(zero_priced)}")
        for b in zero_priced:
            self.stdout.write(f"    {b.reference} ({b.property})")
        self.stdout.write(
            f"  GBP charges with no rate on file, backfilling live rate {settings.gbp_conversion_rate}: "
            f"{len(gbp_rate_backfilled)}"
        )
        for b in gbp_rate_backfilled:
            self.stdout.write(f"    {b.reference} ({b.property})")
        self.stdout.write(f"  Charges to update overall: {len(charges_to_update)}")
        self.stdout.write(f"  Payment rows to create (status=paid): {len(payments_to_create)}")
        self.stdout.write(f"  BalancePayment rows to create (status=pending): {len(balance_payments_to_create)}")

        if apply:
            with transaction.atomic():
                if charges_to_update:
                    Charge.objects.bulk_update(
                        charges_to_update,
                        ['due_at_booking', 'due_at_balance', 'balance_due_date', 'gbp_conversion_rate', 'security'],
                        batch_size=500,
                    )
                if payments_to_create:
                    Payment.objects.bulk_create(payments_to_create, batch_size=500)
                if balance_payments_to_create:
                    BalancePayment.objects.bulk_create(balance_payments_to_create, batch_size=500)
            self.stdout.write(self.style.SUCCESS("Applied."))
        else:
            self.stdout.write("(dry run - pass --apply to write)")
