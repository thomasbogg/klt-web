from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Booking
from staff.utils import CLOSED_STATUSES, sync_checkins_for_booking


class Command(BaseCommand):
    """Backfills/reconciles Checkin rows for upcoming bookings - normally unnecessary since
    signals (staff/signals.py) keep Checkin in sync with Arrival/Booking on every save, but this
    is safe and cheap to re-run any time (e.g. once after this feature's migration first ships, to
    pick up existing bookings whose Arrival rows were saved before the signals existed). Skips
    past arrivals - no value cluttering the calendar with already-happened check-ins, and skips
    closed/cancelled bookings entirely (sync_checkins_for_booking's own cancellation branch would
    just no-op on them anyway, but there's no reason to even call it)."""
    help = "Backfill/reconcile Checkin rows for upcoming arrivals."

    def handle(self, *args, **options):
        today = timezone.now().date()
        bookings = Booking.objects.filter(arrival_date__gte=today).exclude(enquiry_status__in=CLOSED_STATUSES)

        count = 0
        for booking in bookings:
            sync_checkins_for_booking(booking)
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Synced check-ins for {count} booking(s)."))
