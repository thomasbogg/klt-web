from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Arrival, Booking
from bookings.utils import compute_effective_self_check_in, compute_eta_from_given_time
from staff.utils import CLOSED_STATUSES, sync_checkins_for_booking


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-08: compute_effective_self_check_in() only ever runs when an
    Arrival is saved, so every booking that predates the ETA change (see that function's docstring)
    still holds a self_check_in derived from the guest's raw given time rather than their computed
    at-property ETA. A guest landing at Faro at 21:00 against a 22:00 cutoff is stored as an
    in-person meet & greet even though they don't reach the property until 22:30 - wrong on the
    staff check-ins calendar, and wrong on the guest's own Location & Check-in page.

    Only ever flips False -> True: an ETA is always at or after the raw given time (buffers are
    additive), so no booking currently marked self-check-in can lose it here.

    Also re-runs sync_checkins_for_booking() for each flipped booking - the 'key_box'/
    'welcome_visit' Checkin rows a self-check-in booking needs are created by that function, not
    by the Arrival save itself, so the calendar would otherwise still show only the bare arrival
    task.

    Scoped to upcoming, non-cancelled bookings: a past stay's stored flag is a record of what
    actually happened and isn't ours to rewrite."""
    help = "One-off: re-derive Arrival.self_check_in from the computed ETA for upcoming bookings."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help="Write the changes. Without this flag, only reports what would happen.")

    def handle(self, *args, **options):
        apply = options['apply']
        today = timezone.now().date()

        candidates = (
            Booking.objects.filter(arrival_date__gte=today, arrival__isnull=False)
            .exclude(enquiry_status__in=CLOSED_STATUSES)
            .select_related('arrival', 'property', 'property__booking_company', 'property__location', 'guest')
        )

        flips = []
        for booking in candidates:
            arrival = booking.arrival
            computed = compute_effective_self_check_in(booking.property, arrival.method, arrival.time)
            if computed is None or computed == arrival.self_check_in:
                continue
            flips.append((booking, arrival, computed))

        self.stdout.write(f"Upcoming bookings checked: {candidates.count()}")
        self.stdout.write(f"Arrival.self_check_in values that change: {len(flips)}")
        for booking, arrival, computed in flips:
            eta = compute_eta_from_given_time(arrival.method, arrival.time)
            self.stdout.write(
                f"  {booking.reference} {booking.property.title} {booking.arrival_date} "
                f"{arrival.method} given={arrival.time} eta={eta} "
                f"{arrival.self_check_in} -> {computed}"
            )

        # A booking flipping to self check-in joins the same-night competition for its Location's
        # shared postbox (bookings/utils.py::resolve_shared_postbox_path) - worth surfacing before
        # writing, since it can reassign preferred/fallback for guests who aren't changing here.
        fork_locations = {
            booking.property.location for booking, _, computed in flips
            if computed and booking.property.location is not None
            and booking.property.location.self_check_in_preferred_code
        }
        if fork_locations:
            self.stdout.write(self.style.WARNING(
                f"\n{len(fork_locations)} shared-postbox location(s) affected - preferred/fallback "
                f"assignment may shift for other guests arriving those nights:"
            ))
            for location in fork_locations:
                nights = sorted({
                    booking.arrival_date for booking, _, computed in flips
                    if computed and booking.property.location == location
                })
                self.stdout.write(f"  {location.title}: {', '.join(str(n) for n in nights)}")

        if not apply:
            self.stdout.write("\n(dry run - pass --apply to write)")
            return

        for booking, arrival, computed in flips:
            arrival.self_check_in = computed
            arrival.save(update_fields=['self_check_in'])
            sync_checkins_for_booking(booking)

        self.stdout.write(self.style.SUCCESS(
            f"\nUpdated {len(flips)} arrival(s) and re-synced their check-in tasks."
        ))
