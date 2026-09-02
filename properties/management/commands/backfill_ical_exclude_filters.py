from django.core.management.base import BaseCommand

from properties.models import iCalLink

AIRBNB_TERM = 'Airbnb (Not Available)'
VRBO_TERM = 'Tentative'


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-02: import_pims_ical_links.py only scraped
    exclude_summary_contains from a PIMS link whose own filter was already configured and of the
    supported "Summary contains" type - 3 of 37 Airbnb links and 28 of 36 Vrbo links came through
    with no filter at all, meaning sync_ical_link() (bookings/utils.py) would treat their
    'Airbnb (Not Available)' host blocks / Vrbo 'Tentative' enquiries as real bookings. Confirmed
    2026-09-02 against a live feed (Quinta da Barracuda A34) that this is exactly what happened:
    an 'Airbnb (Not available)' block with no local Booking behind it. Idempotently ensures every
    Airbnb iCalLink's exclude_summary_contains includes AIRBNB_TERM and every Vrbo iCalLink's
    includes VRBO_TERM, appended as an extra line rather than replacing - a link that already has
    its own scraped term (e.g. the bare 'Not Available' some Airbnb rows have) keeps it."""
    help = "Backfill exclude_summary_contains so every Airbnb iCalLink excludes 'Airbnb (Not Available)' and every Vrbo iCalLink excludes 'Tentative'."

    def handle(self, *args, **options):
        for platform_name, term in (('Airbnb', AIRBNB_TERM), ('Vrbo', VRBO_TERM)):
            links = iCalLink.objects.filter(platform__name=platform_name)
            updated = 0
            for link in links:
                existing_terms = [line.strip().lower() for line in link.exclude_summary_contains.splitlines()]
                if term.lower() in existing_terms:
                    continue
                link.exclude_summary_contains = (
                    f"{link.exclude_summary_contains}\n{term}" if link.exclude_summary_contains.strip() else term
                )
                link.save(update_fields=['exclude_summary_contains'])
                updated += 1
            self.stdout.write(self.style.SUCCESS(
                f"{platform_name}: {updated} of {links.count()} link(s) updated to include '{term}' "
                f"({links.count() - updated} already had it)."
            ))
