import requests
from django.core.management.base import BaseCommand

from bookings.utils import sync_ical_link
from libraries.utils import logerror
from properties.models import iCalLink


class Command(BaseCommand):
    """Fetches every configured iCalLink's feed and syncs it against our Bookings (see
    bookings/utils.py::sync_ical_link() for the actual matching/create/cancel logic - this command
    is just the HTTP fetch + per-property error isolation + a printed summary). Not scheduled
    in-app (klt-web has no deployed scheduler yet) - run manually or via an external cron."""
    help = "Fetch each property's platform iCal feeds and sync Bookings against them."

    def add_arguments(self, parser):
        parser.add_argument(
            '--property-id', type=int, default=None,
            help="Only sync iCalLinks for this property's id (default: all configured links).",
        )

    def handle(self, *args, **options):
        links = iCalLink.objects.exclude(ical_url__isnull=True).exclude(ical_url='')
        if options['property_id'] is not None:
            links = links.filter(property_id=options['property_id'])

        if not links:
            self.stdout.write("No iCal links with a URL configured - nothing to sync.")
            return

        for link in links:
            label = f"{link.property} ({link.platform.name if link.platform_id else 'no platform set'})"
            try:
                response = requests.get(link.ical_url, timeout=30)
                response.raise_for_status()
            except requests.RequestException as error:
                logerror(f"Could not fetch iCal feed for {label}: {error}")
                self.stderr.write(self.style.ERROR(f"{label}: fetch failed - {error}"))
                continue

            try:
                summary = sync_ical_link(link, response.text)
            except Exception as error:
                logerror(f"Could not parse/sync iCal feed for {label}: {error}")
                self.stderr.write(self.style.ERROR(f"{label}: sync failed - {error}"))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"{label}: {summary['created']} created, {summary['updated']} updated, "
                f"{summary['resurrected']} resurrected, {summary['cancelled']} cancelled, "
                f"{len(summary['conflicts'])} conflicts skipped."
            ))
            for conflict in summary['conflicts']:
                self.stdout.write(self.style.WARNING(
                    f"  conflict: uid={conflict['uid']} {conflict['start']} - {conflict['end']} "
                    "overlaps an existing booking, not imported - resolve manually."
                ))
