from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Booking
from communications.models import EmailTemplate, ScheduledEmail
from communications.registry import EMAIL_TYPES
from staff.utils import CLOSED_STATUSES

SLUG = 'arrival_departure_reminder'


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-08: create_scheduled_emails_for_booking() only ever runs at
    booking-creation time (bookings/utils.py::create_booking()), so a brand new EmailTemplate never
    reaches a booking that already existed before it was seeded - exactly the "bookings that have
    been set for a while" case this reminder was built for. Scoped to arrival_date >= today and a
    real guest email on file, mirroring EMAIL_TYPES['arrival_departure_reminder'].eligible() itself
    (rechecked live at send time regardless, so a row created here for a booking that fills in its
    details before scheduled_for arrives is simply marked skipped, same as any other row)."""
    help = "One-off: create arrival_departure_reminder ScheduledEmail rows for existing live bookings."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help="Write the changes. Without this flag, only reports what would happen.")

    def handle(self, *args, **options):
        apply = options['apply']
        template = EmailTemplate.objects.filter(slug=SLUG, active=True).first()
        if template is None:
            self.stderr.write(self.style.ERROR(f"No active EmailTemplate with slug '{SLUG}' - run migrations first."))
            return
        definition = EMAIL_TYPES[SLUG]

        today = timezone.now().date()
        already_scheduled = ScheduledEmail.objects.filter(template=template).values_list('booking_id', flat=True)
        candidates = (
            Booking.objects.filter(arrival_date__gte=today)
            .exclude(enquiry_status__in=CLOSED_STATUSES)
            .exclude(guest__email='').exclude(guest__email__isnull=True)
            .exclude(pk__in=already_scheduled)
            .select_related('guest', 'property')
        )

        to_create = []
        for booking in candidates:
            anchor = definition.anchor(booking)
            if anchor is None:
                continue
            to_create.append(ScheduledEmail(
                booking=booking, template=template,
                scheduled_for=anchor + timedelta(days=template.offset_days),
            ))

        self.stdout.write(f"Bookings to schedule: {len(to_create)} (of {candidates.count()} candidates checked)")
        if to_create:
            overdue = sum(1 for row in to_create if row.scheduled_for <= today)
            self.stdout.write(f"  already due as of today (scheduled_for <= {today}): {overdue}")

        if not apply:
            self.stdout.write("(dry run - pass --apply to write)")
            return

        ScheduledEmail.objects.bulk_create(to_create)
        self.stdout.write(self.style.SUCCESS(f"Created {len(to_create)} ScheduledEmail rows."))
