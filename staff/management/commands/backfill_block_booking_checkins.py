from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from staff.models import Checkin

# One-off, per Thomas 2026-09-03: sync_checkins_for_booking() (staff/utils.py) only just learned
# to gate on is_block_booking() - the two legacy PIMS calendar-block categories, 'BLOCK -
# Unbookable' and 'BLOCK - Late Check-out', identified by guest.last_name - so this does
# retroactively what it would have done on the next save anyway (235 real, existing pending
# Checkin rows), bulk instead of a per-booking signal re-trigger, same shape as
# backfill_closed_status_calendar_tasks.py's own one-off for the CLOSED_STATUSES gate.


class Command(BaseCommand):
    help = "One-off: delete pending Checkin rows left over from BLOCK-placeholder bookings."

    def handle(self, *args, **options):
        with transaction.atomic():
            checkins = Checkin.objects.filter(
                Q(booking__guest__last_name__iexact='BLOCK - Unbookable')
                | Q(booking__guest__last_name__iexact='BLOCK - Late Check-out'),
                status='pending',
            )
            deleted, _ = checkins.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} pending Checkin row(s) for BLOCK bookings."))
