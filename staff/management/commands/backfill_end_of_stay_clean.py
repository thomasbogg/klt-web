from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Booking, Departure
from staff.utils import CLOSED_STATUSES, sync_cleaning_tasks_for_booking


class Command(BaseCommand):
    """One-off fix for the 2026-08-27 change making Departure.clean default to True (an
    end-of-stay clean is the normal case for every booking, not something staff opt into) -
    without this, only bookings created after that change actually get one, since the old
    default (False) is what every earlier booking already has on its Departure row, or would get
    from Departure.objects.get_or_create()'s fallback if it has none at all yet. Safe to re-run
    (a booking whose Departure.clean is already True is simply skipped), but only meant to be run
    once shortly after deploying that change - not a standing part of any regular sync, unlike
    sync_cleaning_tasks. Scoped to non-owner, non-closed, not-yet-departed bookings - a past stay's
    cleaning status is moot, a cancelled one was never going to be cleaned, and an owner's own
    booking is left alone since they may have their own cleaning arrangement."""
    help = "One-off: default Departure.clean to True for existing non-owner, upcoming bookings."

    def handle(self, *args, **options):
        today = timezone.now().date()
        bookings = Booking.objects.filter(
            is_owner=False, departure_date__gte=today,
        ).exclude(enquiry_status__in=CLOSED_STATUSES)

        created = updated = synced = 0
        for booking in bookings:
            departure = getattr(booking, 'departure', None)
            if departure is None:
                Departure.objects.create(booking=booking)
                created += 1
            elif not departure.clean:
                departure.clean = True
                departure.save(update_fields=['clean'])
                updated += 1
            else:
                continue
            sync_cleaning_tasks_for_booking(booking)
            synced += 1

        self.stdout.write(self.style.SUCCESS(
            f"Created {created} Departure row(s), flipped {updated} to clean=True, "
            f"synced cleaning tasks for {synced} booking(s)."
        ))
