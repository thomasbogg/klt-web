from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from bookings.models import Booking
from staff.utils import sync_cleaning_tasks_for_booking


class Command(BaseCommand):
    """Backfills/reconciles CleaningTask rows for upcoming bookings - normally unnecessary since
    signals (staff/signals.py) keep CleaningTask in sync with Departure.clean/Extra.mid_stay_clean
    on every save, but this is safe and cheap to re-run any time (e.g. once after this feature's
    migration first ships, to pick up existing bookings whose Departure/Extra rows were saved
    before the signals existed). Skips past departures/mid-stay dates - no value cluttering the
    rota with already-happened cleans."""
    help = "Backfill/reconcile CleaningTask rows for upcoming turnover and mid-stay cleans."

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

        self.stdout.write(self.style.SUCCESS(f"Synced cleaning tasks for {count} booking(s)."))
