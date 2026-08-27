from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from bookings.models import Booking
from properties.models import Property
from staff.utils import sync_cleaning_tasks_for_booking, sync_freshen_tasks_for_property


class Command(BaseCommand):
    """Backfills/reconciles CleaningTask rows for upcoming bookings - normally unnecessary since
    signals (staff/signals.py) keep CleaningTask in sync with Departure.clean/Extra.mid_stay_clean
    on every save, but this is safe and cheap to re-run any time (e.g. once after this feature's
    migration first ships, to pick up existing bookings whose Departure/Extra rows were saved
    before the signals existed). Skips past departures/mid-stay dates - no value cluttering the
    rota with already-happened cleans.

    Also sweeps Freshen tasks for every property whose cleaning company has freshen_after_days
    set, independent of the turnover/mid-stay booking filter above - a property's Freshen state
    depends on its whole active booking timeline (staff/utils.py::sync_freshen_tasks_for_property),
    not on any single booking's Departure/Extra flags, so it can't reuse that same filtered
    queryset without under-covering properties whose bookings happen not to match it."""
    help = "Backfill/reconcile CleaningTask rows for upcoming turnover, mid-stay, and Freshen cleans."

    def handle(self, *args, **options):
        today = timezone.now().date()
        bookings = Booking.objects.filter(
            Q(departure__clean=True, departure_date__gte=today) |
            Q(extras__mid_stay_clean=True, extras__mid_stay_clean_date__gte=today)
        ).distinct()

        count = 0
        for booking in bookings:
            sync_cleaning_tasks_for_booking(booking)
            count += 1

        properties = list(Property.objects.filter(cleaning_company__freshen_after_days__isnull=False))
        for property in properties:
            sync_freshen_tasks_for_property(property)

        self.stdout.write(self.style.SUCCESS(
            f"Synced cleaning tasks for {count} booking(s) and Freshen tasks for "
            f"{len(properties)} propert{'y' if len(properties) == 1 else 'ies'}."
        ))
