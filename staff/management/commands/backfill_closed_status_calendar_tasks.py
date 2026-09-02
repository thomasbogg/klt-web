from django.core.management.base import BaseCommand
from django.db import transaction

from bookings.models import Booking
from staff.models import Checkin, CleaningTask

# One-off, per Thomas 2026-09-02: 'Booking cancelled', 'Enquiry that failed to convert',
# 'Open enquiry', and 'Booking cancelled with fees' (1041/221/55/8 real, legacy/migrated Bookings)
# were missing from staff/utils.py::CLOSED_STATUSES until this same change, so
# sync_checkins_for_booking()/sync_cleaning_tasks_for_booking() never recognized those bookings as
# closed - every save touching one of them (or a related Arrival/Departure/Extra row) kept
# regenerating real, pending Checkin/CleaningTask rows for it, which is why cancelled/never-
# confirmed bookings were showing up on the check-ins and cleaning calendars. Now that
# CLOSED_STATUSES includes all four, this just does retroactively what
# sync_checkins_for_booking()/sync_cleaning_tasks_for_booking() would have done on the next save
# anyway - bulk instead of a per-booking signal re-trigger, since this is a one-time backlog, not a
# live single-row update.


class Command(BaseCommand):
    help = "One-off: delete pending Checkin/CleaningTask rows left over from bookings whose status was only just added to CLOSED_STATUSES."

    def handle(self, *args, **options):
        statuses = [
            'Booking cancelled', 'Enquiry that failed to convert', 'Open enquiry',
            'Booking cancelled with fees',
        ]
        with transaction.atomic():
            for status in statuses:
                booking_ids = list(Booking.objects.filter(enquiry_status=status).values_list('pk', flat=True))
                checkins_deleted, _ = Checkin.objects.filter(booking_id__in=booking_ids, status='pending').delete()
                tasks_deleted, _ = CleaningTask.objects.filter(booking_id__in=booking_ids, status='pending').delete()
                self.stdout.write(self.style.SUCCESS(
                    f"{len(booking_ids)} '{status}' booking(s): deleted {checkins_deleted} pending Checkin row(s), "
                    f"{tasks_deleted} pending CleaningTask row(s)."
                ))
