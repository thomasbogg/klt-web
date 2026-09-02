from datetime import date, time

from django.core.management.base import BaseCommand

from bookings.models import Arrival, Booking, Departure, TravelMethod

CUTOFF = date(2026, 9, 1)


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-02: the check-ins calendar's Arrival Plans popup
    (staff/templates/staff/_checkin_popup.html) reads Arrival/Departure straight off the current
    model (arrival.get_method_display/arrival.time), but a lot of what's in those fields for
    bookings arriving from CUTOFF onward is legacy-migration noise that predates - and doesn't
    match - what the new model actually means by each field:

    1. Arrival/Departure.time == 00:00:00 with no flight_number at all (208/221 rows respectively,
       as of this run) - confirmed there is zero overlap between a "00:00" row and a row that also
       has a real flight_number, across the whole scope: the legacy klt_main.db schema stored a
       literal "00:00" placeholder for "no time given" instead of NULL
       (bookings/management/commands/migrate_klt_data.py's migrate_arrivals()/migrate_departures()
       just parsed whatever string was there, including that placeholder, into a real time value).
       The new model's own convention is NULL = unknown (blank=True, null=True, and the popup
       template's own `|default:"—"` fallback exists precisely for this) - so a stored 00:00 reads
       to a check-in officer as "guest said midnight", which for the overwhelming majority of
       these is simply false. Nulled out here, but ONLY where flight_number is also blank - a row
       that somehow has a 00:00 time AND a real flight number is left alone rather than guessed at.
    2. flight_number holding the method's own display text verbatim ('Flight to Faro'/'Flight from
       Faro' etc.) rather than a real flight code - checked against get_method_display() for
       Arrival, but against TravelMethod.departure_choices() for Departure (its `method` field's
       own get_method_display() always returns the arrival-worded label regardless of direction -
       see TravelMethod's own docstring - the departure-worded label only exists via that separate
       classmethod, which is what staff/views.py:2537 and every departure-facing template actually
       render). Not a hardcoded string list, so this same pass is safe to reuse if it recurs.
       Cleared outright - it's a pure restatement of what `method` already says, no information
       lost.
    3. A Booking missing its Arrival or Departure row entirely (e.g. one created directly via
       sync_ical_feeds, bypassing every other code path that already get_or_create()s these -
       bookings/views.py::_save_arrival, staff/views.py::StaffBookingDetailView._update_booking(),
       owners/views.py) - created with the same defaults every other call site already uses."""
    help = "One-off: clean up legacy-migration placeholder noise in Arrival/Departure for bookings arriving from 2026-09-01 onward."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help="Write the changes. Without this flag, only reports what would happen.")

    def handle(self, *args, **options):
        apply = options['apply']
        bookings = Booking.objects.filter(arrival_date__gte=CUTOFF)

        midnight_arrivals = Arrival.objects.filter(
            booking__in=bookings, time=time(0, 0),
        ).filter(flight_number__isnull=True) | Arrival.objects.filter(
            booking__in=bookings, time=time(0, 0), flight_number='',
        )
        midnight_departures = Departure.objects.filter(
            booking__in=bookings, time=time(0, 0),
        ).filter(flight_number__isnull=True) | Departure.objects.filter(
            booking__in=bookings, time=time(0, 0), flight_number='',
        )
        midnight_arrival_ids = list(midnight_arrivals.values_list('pk', flat=True))
        midnight_departure_ids = list(midnight_departures.values_list('pk', flat=True))

        departure_method_labels = dict(TravelMethod.departure_choices())

        redundant_arrival_flight_numbers = [
            a for a in Arrival.objects.filter(booking__in=bookings).exclude(flight_number__isnull=True).exclude(flight_number='')
            if a.flight_number.strip().lower() == a.get_method_display().strip().lower()
        ]
        redundant_departure_flight_numbers = [
            d for d in Departure.objects.filter(booking__in=bookings).exclude(flight_number__isnull=True).exclude(flight_number='')
            if d.flight_number.strip().lower() == departure_method_labels.get(d.method, '').strip().lower()
        ]

        missing_arrival_bookings = list(bookings.filter(arrival__isnull=True))
        missing_departure_bookings = list(bookings.filter(departure__isnull=True))

        self.stdout.write(f"Placeholder 00:00 time (no flight_number): {len(midnight_arrival_ids)} Arrival row(s), {len(midnight_departure_ids)} Departure row(s)")
        self.stdout.write(f"flight_number redundant with method label: {len(redundant_arrival_flight_numbers)} Arrival row(s), {len(redundant_departure_flight_numbers)} Departure row(s)")
        for a in redundant_arrival_flight_numbers:
            self.stdout.write(f"  Arrival booking #{a.booking_id}: {a.flight_number!r}")
        for d in redundant_departure_flight_numbers:
            self.stdout.write(f"  Departure booking #{d.booking_id}: {d.flight_number!r}")
        self.stdout.write(f"Missing Arrival row: {len(missing_arrival_bookings)} booking(s) {[b.pk for b in missing_arrival_bookings]}")
        self.stdout.write(f"Missing Departure row: {len(missing_departure_bookings)} booking(s) {[b.pk for b in missing_departure_bookings]}")

        if not apply:
            self.stdout.write("(dry run - pass --apply to write)")
            return

        Arrival.objects.filter(pk__in=midnight_arrival_ids).update(time=None)
        Departure.objects.filter(pk__in=midnight_departure_ids).update(time=None)
        Arrival.objects.filter(pk__in=[a.pk for a in redundant_arrival_flight_numbers]).update(flight_number=None)
        Departure.objects.filter(pk__in=[d.pk for d in redundant_departure_flight_numbers]).update(flight_number=None)
        for booking in missing_arrival_bookings:
            Arrival.objects.create(booking=booking, self_check_in=False, meet_greet=True)
        for booking in missing_departure_bookings:
            Departure.objects.create(booking=booking, clean=True)

        self.stdout.write(self.style.SUCCESS("Applied."))
