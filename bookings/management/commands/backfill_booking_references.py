from django.core.management.base import BaseCommand
from django.db import transaction

from bookings.models import REFERENCE_GENERATION_ATTEMPTS, Booking
from bookings.utils import generate_reference_candidate


class Command(BaseCommand):
    """Run-once repair: assigns a reference to every legacy-imported Booking that has none.
    Booking.save() only generates a reference for brand-new rows (not self.pk), so
    migrate_klt_data.py's bulk-created rows never got one - leaving them unopenable in the staff
    booking_detail view, which looks bookings up by reference alone."""
    help = "Backfill Booking.reference for imported bookings that don't have one."

    def handle(self, *args, **options):
        bookings = list(Booking.objects.filter(reference__isnull=True).only('id'))
        if not bookings:
            self.stdout.write("No bookings are missing a reference.")
            return

        used = set(Booking.objects.exclude(reference__isnull=True).values_list('reference', flat=True))

        for booking in bookings:
            for _ in range(REFERENCE_GENERATION_ATTEMPTS):
                candidate = generate_reference_candidate()
                if candidate not in used:
                    booking.reference = candidate
                    used.add(candidate)
                    break
            else:
                raise RuntimeError(f"Could not generate a unique reference for booking id={booking.id}.")

        with transaction.atomic():
            Booking.objects.bulk_update(bookings, ['reference'], batch_size=500)

        self.stdout.write(f"Assigned references to {len(bookings)} bookings.")
