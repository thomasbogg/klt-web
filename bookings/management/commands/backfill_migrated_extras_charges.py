from datetime import date

from django.core.management.base import BaseCommand

from bookings.models import Extra, ExtrasSettings


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-02: bookings/management/commands/migrate_klt_data.py's
    migrate_extras() only ever migrated the boolean flags (cot/high_chair/welcome_pack/
    late_checkout/mid_stay_clean) from the legacy `extras` table, never the priced fields
    (cot_high_chair_charge/welcome_pack_charge/late_checkout_charge/mid_stay_clean_charge) - the
    legacy schema apparently didn't carry a stored price for these at all, only the yes/no
    request. Every OTHER way an Extra row's charge gets set (bookings/views.py's guest-facing
    _save_extras, staff/owners equivalents) computes it live off ExtrasSettings at save time - a
    migrated row just never went through that path, so it silently prices at EUR 0 wherever
    extras_summary() (bookings/utils.py) reads `extra.X_charge or 0` - found 2026-09-02 via a real
    booking (#5851) showing "Cot - EUR0" in the check-ins calendar popup.

    Computes each missing charge using TODAY's ExtrasSettings and the same formulas
    bookings/views.py::BookingDetailsView._save_extras uses (compute_cot_high_chair_price/
    welcome_pack_price/late_checkout_price/compute_mid_stay_clean_price) - the best available
    estimate, since the actual price in effect back when the guest made the request isn't
    preserved anywhere. Scoped to departure_date >= today rather than the Arrival/Departure sync's
    2026-09-01 arrival cutoff (that investigation's own trigger, booking #5851, arrived 2026-08-31
    and is still mid-stay - a departure-based cutoff is what actually matters for whether this is
    still worth fixing operationally)."""
    help = "One-off: backfill missing Extra charge fields (cot/welcome pack/late checkout/mid-stay clean) left blank by the legacy data migration."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help="Write the changes. Without this flag, only reports what would happen.")

    def handle(self, *args, **options):
        apply = options['apply']
        settings = ExtrasSettings.load()
        extras = Extra.objects.filter(booking__departure_date__gte=date.today()).select_related('booking')

        cot_rows = [
            e for e in extras.filter(cot_high_chair_charge__isnull=True)
            if e.cot or e.high_chair
        ]
        welcome_pack_rows = list(extras.filter(welcome_pack=True, welcome_pack_charge__isnull=True))
        late_checkout_rows = list(extras.filter(late_checkout=True, late_checkout_charge__isnull=True))
        mid_stay_clean_rows = list(extras.filter(mid_stay_clean=True, mid_stay_clean_charge__isnull=True))

        self.stdout.write(f"Cot/High Chair missing charge: {len(cot_rows)}")
        for e in cot_rows:
            nights = (e.booking.departure_date - e.booking.arrival_date).days
            price = settings.compute_cot_high_chair_price(nights, e.cot, e.high_chair)
            self.stdout.write(f"  booking #{e.booking_id}: cot={e.cot} high_chair={e.high_chair} nights={nights} -> €{price}")
            if apply:
                e.cot_high_chair_charge = price
                e.save(update_fields=['cot_high_chair_charge'])

        self.stdout.write(f"Welcome Pack missing charge: {len(welcome_pack_rows)}")
        for e in welcome_pack_rows:
            self.stdout.write(f"  booking #{e.booking_id}: -> €{settings.welcome_pack_price}")
            if apply:
                e.welcome_pack_charge = settings.welcome_pack_price
                e.save(update_fields=['welcome_pack_charge'])

        self.stdout.write(f"Late Checkout missing charge: {len(late_checkout_rows)}")
        for e in late_checkout_rows:
            self.stdout.write(f"  booking #{e.booking_id}: -> €{settings.late_checkout_price}")
            if apply:
                e.late_checkout_charge = settings.late_checkout_price
                e.save(update_fields=['late_checkout_charge'])

        self.stdout.write(f"Mid-stay Clean missing charge: {len(mid_stay_clean_rows)}")
        for e in mid_stay_clean_rows:
            price = settings.compute_mid_stay_clean_price(e.booking.property)
            self.stdout.write(f"  booking #{e.booking_id}: -> €{price}")
            if apply:
                e.mid_stay_clean_charge = price
                e.save(update_fields=['mid_stay_clean_charge'])

        if not apply:
            self.stdout.write("(dry run - pass --apply to write)")
        else:
            self.stdout.write(self.style.SUCCESS("Applied."))
