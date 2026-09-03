from django.core.management.base import BaseCommand

from staff.models import CleaningTask

# One-off, per Thomas 2026-09-03: sync_cleaning_tasks_for_booking()/sync_freshen_tasks_for_
# property() (staff/utils.py) only just learned to gate on is_unbookable_block_booking() -
# 'BLOCK - Unbookable' placeholder bookings, identified by guest.last_name - so this does
# retroactively what they would have done on the next save/sweep anyway (85 real, existing pending
# CleaningTask rows), bulk instead of a per-booking signal re-trigger, same shape as
# backfill_block_booking_checkins.py's own one-off for the check-ins calendar. Deliberately does
# NOT touch 'BLOCK - Late Check-out' rows - Thomas is keeping those as a placeholder reminder for a
# real cleaning-schedule effect that hasn't been designed yet.


class Command(BaseCommand):
    help = "One-off: delete pending CleaningTask rows left over from 'BLOCK - Unbookable' placeholder bookings."

    def handle(self, *args, **options):
        deleted, _ = CleaningTask.objects.filter(
            booking__guest__last_name__iexact='BLOCK - Unbookable', status='pending',
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} pending CleaningTask row(s) for 'BLOCK - Unbookable' bookings."))
