from datetime import date

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q

from bookings.models import Booking
from libraries.utils import logerror
from properties.models import Platform, iCalLink


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-02: the legacy klt_main.db import never recorded each imported
    booking's platform VEVENT UID, so every pre-existing future platform booking has a blank
    ical_uid - meaning bookings/utils.py::sync_ical_link()'s disappearance-cancellation pass (which
    only ever looks at `ical_uid__isnull=False` rows) can't see them yet. This backfills ical_uid
    on those bookings by matching them against each iCalLink's *current* feed on exact
    (arrival_date, departure_date), using the same excluded-summary-terms filter sync_ical_link()
    applies (Not Available/Tentative blocks are never real bookings, so never candidates to match).

    Scope: Bookings with departure_date >= today, enquiry_source equal to a real Platform name, and
    a blank ical_uid - i.e. exactly the rows sync_ical_link()'s own disappearance check would want
    to see. Confirmed 2026-09-02 (see command's own dry-run output) that every such booking's
    (property, platform) pair has a configured iCalLink, there's only ever one iCalLink per
    (property, platform), and no candidate booking has manual_override set - so plain exact-date
    matching is sufficient; this does not attempt fuzzy/overlap matching for a manually-adjusted
    booking's shifted dates, since none exist in this backfill's scope. Never creates, cancels, or
    date-adjusts a Booking - matched rows only ever get ical_uid set.

    Dry-run by default; pass --apply to actually write. Always run without --apply first and read
    the inconsistency report - a booking that fails to match isn't necessarily wrong (it may
    genuinely have vanished from the platform since it was entered), but every case is worth a
    human look before this becomes the seed data for automated cancellation."""
    help = "One-off: backfill ical_uid onto existing future platform Bookings by matching each iCalLink's current feed on exact dates."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help="Write matched ical_uid values. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        from icalendar import Calendar

        apply = options['apply']
        today = date.today()
        platform_names = list(Platform.objects.values_list('name', flat=True))

        targets = Booking.objects.filter(
            departure_date__gte=today,
            enquiry_source__in=platform_names,
        ).filter(Q(ical_uid__isnull=True) | Q(ical_uid='')).select_related('property')

        links = {
            (link.property_id, link.platform.name): link
            for link in iCalLink.objects.exclude(ical_url__isnull=True).exclude(ical_url='').select_related('platform')
            if link.platform_id
        }

        matched, no_feed_event, ambiguous, no_link, fetch_failed = [], [], [], [], []
        unmatched_feed_events = []
        to_write = []

        by_link = {}
        for booking in targets:
            key = (booking.property_id, booking.enquiry_source)
            if key not in links:
                no_link.append(booking)
                continue
            by_link.setdefault(links[key], []).append(booking)

        for link, bookings in by_link.items():
            label = f"{link.property} ({link.platform.name})"
            try:
                response = requests.get(link.ical_url, timeout=30)
                response.raise_for_status()
                calendar = Calendar.from_ical(response.text)
            except Exception as error:
                logerror(f"backfill_ical_uids: could not fetch/parse feed for {label}: {error}")
                fetch_failed.append((link, str(error)))
                continue

            exclude_terms = [term.lower() for term in link.excluded_summary_terms()]
            feed_by_dates = {}
            for component in calendar.walk('VEVENT'):
                summary_text = str(component.get('summary') or '').lower()
                if exclude_terms and any(term in summary_text for term in exclude_terms):
                    continue
                uid = str(component.get('uid'))
                start = component.get('dtstart').dt
                end = component.get('dtend').dt
                start = start.date() if hasattr(start, 'date') and not isinstance(start, date) else start
                end = end.date() if hasattr(end, 'date') and not isinstance(end, date) else end
                feed_by_dates.setdefault((start, end), []).append(uid)

            local_by_dates = {}
            for booking in bookings:
                local_by_dates.setdefault((booking.arrival_date, booking.departure_date), []).append(booking)

            consumed_uids = set()
            for dates, local_bookings in local_by_dates.items():
                feed_uids = feed_by_dates.get(dates, [])
                if len(feed_uids) == 1 and len(local_bookings) == 1:
                    booking = local_bookings[0]
                    booking.ical_uid = feed_uids[0]
                    to_write.append(booking)
                    matched.append((booking, feed_uids[0]))
                    consumed_uids.add(feed_uids[0])
                elif not feed_uids:
                    no_feed_event.extend(local_bookings)
                else:
                    ambiguous.append((link, dates, local_bookings, feed_uids))

            for dates, uids in feed_by_dates.items():
                if dates[1] < today:
                    continue
                for uid in uids:
                    if uid not in consumed_uids and dates not in local_by_dates:
                        unmatched_feed_events.append((link, dates, uid))

        self.stdout.write(f"Scope: {targets.count()} future platform booking(s) missing ical_uid.")
        self.stdout.write(self.style.SUCCESS(f"Matched: {len(matched)}"))

        if apply:
            for booking in to_write:
                booking.save(update_fields=['ical_uid'])
            self.stdout.write(self.style.SUCCESS(f"Wrote ical_uid on {len(to_write)} booking(s)."))
        else:
            self.stdout.write("(dry run - pass --apply to write)")

        if no_link:
            self.stdout.write(self.style.ERROR(f"\n{len(no_link)} booking(s) with no configured iCalLink for their (property, platform):"))
            for booking in no_link:
                self.stdout.write(f"  #{booking.pk} {booking.property} {booking.enquiry_source} {booking.arrival_date}-{booking.departure_date}")

        if fetch_failed:
            self.stdout.write(self.style.ERROR(f"\n{len(fetch_failed)} feed(s) failed to fetch/parse - their bookings were skipped entirely:"))
            for link, error in fetch_failed:
                self.stdout.write(f"  {link.property} ({link.platform.name}): {error}")

        if no_feed_event:
            already_cancelled = [b for b in no_feed_event if b.enquiry_status == 'Booking cancelled']
            still_confirmed = [b for b in no_feed_event if b.enquiry_status != 'Booking cancelled']
            self.stdout.write(self.style.WARNING(
                f"\n{len(no_feed_event)} booking(s) with no matching feed event "
                f"({len(already_cancelled)} already 'Booking cancelled' locally - expected, no action needed; "
                f"{len(still_confirmed)} still active locally - genuine mismatch, needs a look):"
            ))
            if still_confirmed:
                self.stdout.write("  Still active locally but missing from the current feed:")
                for booking in still_confirmed:
                    self.stdout.write(f"    #{booking.pk} {booking.property} {booking.enquiry_source} {booking.arrival_date}-{booking.departure_date} [{booking.enquiry_status}]")
            if already_cancelled:
                self.stdout.write("  Already cancelled locally (listed for completeness only):")
                for booking in already_cancelled:
                    self.stdout.write(f"    #{booking.pk} {booking.property} {booking.enquiry_source} {booking.arrival_date}-{booking.departure_date}")

        if ambiguous:
            self.stdout.write(self.style.WARNING(f"\n{len(ambiguous)} ambiguous date-range group(s) - not auto-matched, needs manual review:"))
            for link, dates, local_bookings, feed_uids in ambiguous:
                self.stdout.write(
                    f"  {link.property} ({link.platform.name}) {dates[0]}-{dates[1]}: "
                    f"{len(local_bookings)} local booking(s) {[b.pk for b in local_bookings]} vs "
                    f"{len(feed_uids)} feed event(s) {feed_uids}"
                )

        if unmatched_feed_events:
            self.stdout.write(self.style.WARNING(f"\n{len(unmatched_feed_events)} feed event(s) with no corresponding local booking at all (possibly a booking never entered in klt-web):"))
            for link, dates, uid in unmatched_feed_events:
                self.stdout.write(f"  {link.property} ({link.platform.name}) {dates[0]}-{dates[1]} uid={uid}")
