import calendar
import math
import sqlite3
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand

from properties.models import Price, Property

KLT_MAIN_DB_PATH = Path(__file__).resolve().parents[3] / 'klt_main.db'

MONTH_COLUMNS = [
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
]


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-02: klt-web's new Price model prices per property, per night -
    the legacy system (klt_main.db) only ever priced per *cluster* of properties (propertyPrices,
    joined onto properties.priceId), per *week*. This backfills 2026 and 2027 (2027 using the same
    figures as 2026, per Thomas - no 2027 row exists in the legacy DB to check against) directly
    from that legacy weekly/cluster data, converted with Thomas's own explicit rule:
    nightly rate = ceil(weekly rate / 7), whole euros.

    Confirmed before writing (see project_klt_web_2026_pricing_backfill memory for the full
    derivation): klt-web's Property.pk matches legacy properties.id 1:1, so no title-matching is
    needed, just a direct id join. 10 properties have no priceId at all in the legacy data (no rate
    was ever entered for them) - skipped entirely, per Thomas. A 0.0 legacy rate for a given
    month/property (e.g. QdB7, the Penthouse's own cluster, outside Jun-Sep) means "not offered
    that season", not "free" - no Price row is created for those brackets rather than writing €0.

    Date brackets, also per Thomas: Festive is Dec 23 - Jan 2 inclusive (spans the calendar-year
    boundary), so the plain December bracket is only Dec 1-22 and the plain January bracket is only
    Jan 3-31. Jan 1-2, 2026 (which would belong to the 2025-vintage Festive bracket) is deliberately
    left with no Price row - out of scope, confirmed with Thomas rather than assumed. The chain per
    property runs Jan 3 2026 -> Jan 2 2028 (the second Festive bracket, using the same 2026 figures,
    spills 2 days into January 2028 - intentional, matches "the whole of 2027 too").

    Only sets `rate` - weekly/monthly/last-minute discounts and extra adult/child charges have no
    equivalent in the legacy weekly sheet, so they're left at Price's own model defaults rather than
    guessed at.

    Idempotent guard: skips any property that already has ANY Price row (rather than a narrower
    per-bracket check) - since this is a from-scratch backfill (0 Price rows existed before this
    command was written), the only way a property already has rows is a previous partial run or
    manual entry, and either way re-deriving figures under it would risk violating Price.clean()'s
    no-overlap rule. Bulk-created in one query per this project's established pattern for one-off
    data scripts against the remote Postgres."""
    help = "One-off: backfill Price (per-night) rows for 2026+2027 from legacy per-cluster weekly rates in klt_main.db."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report what would be created, without writing to the database.")

    def handle(self, *args, **options):
        conn = sqlite3.connect(KLT_MAIN_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cluster_by_property_id = {
            row['id']: row['priceId']
            for row in cur.execute("SELECT id, priceId FROM properties WHERE priceId IS NOT NULL AND priceId != ''")
        }
        nightly_rates = {}
        for row in cur.execute("SELECT * FROM propertyPrices WHERE year = 2026"):
            nightly_rates[row['name']] = {
                **{month: self._nightly(row[month]) for month in MONTH_COLUMNS},
                'festive': self._nightly(row['festive']),
            }
        conn.close()

        no_cluster = Property.objects.exclude(pk__in=cluster_by_property_id.keys())
        already_priced_ids = set(Price.objects.values_list('property_id', flat=True).distinct())

        to_create = []
        skipped_already_priced = []
        skipped_no_rate_data = []

        for prop in Property.objects.filter(pk__in=cluster_by_property_id.keys()).order_by('pk'):
            if prop.pk in already_priced_ids:
                skipped_already_priced.append(prop)
                continue
            cluster = cluster_by_property_id[prop.pk]
            rates = nightly_rates.get(cluster)
            if rates is None:
                skipped_no_rate_data.append((prop, cluster))
                continue
            to_create.extend(self._price_rows_for_property(prop, rates))

        self.stdout.write(f'{"Would create" if options["dry_run"] else "Creating"} {len(to_create)} Price rows across '
                           f'{len(cluster_by_property_id) - len(skipped_already_priced) - len(skipped_no_rate_data)} properties.')
        self.stdout.write(f'{no_cluster.count()} properties have no legacy priceId at all, skipped entirely (per Thomas):')
        for prop in no_cluster.order_by('pk'):
            self.stdout.write(f'  {prop.pk}: {prop.title}')
        if skipped_already_priced:
            self.stdout.write(f'{len(skipped_already_priced)} properties already have Price rows, left untouched:')
            for prop in skipped_already_priced:
                self.stdout.write(f'  {prop.pk}: {prop.title}')
        if skipped_no_rate_data:
            self.stdout.write(f'{len(skipped_no_rate_data)} properties reference a cluster code with no 2026 rate row (unexpected):')
            for prop, cluster in skipped_no_rate_data:
                self.stdout.write(f'  {prop.pk}: {prop.title} (cluster {cluster})')

        if not options['dry_run']:
            Price.objects.bulk_create(to_create, batch_size=500)
            self.stdout.write(f'Created {len(to_create)} Price rows.')

    @staticmethod
    def _nightly(weekly_rate):
        return math.ceil(weekly_rate / 7) if weekly_rate else 0

    def _price_rows_for_property(self, prop, rates):
        rows = []
        for year in (2026, 2027):
            for month_index, month in enumerate(MONTH_COLUMNS, start=1):
                rate = rates[month]
                if not rate:
                    continue
                start, end = self._month_bracket(year, month_index)
                rows.append(Price(property=prop, start_date=start, end_date=end, rate=rate))
            if rates['festive']:
                start, end = self._festive_bracket(year)
                rows.append(Price(property=prop, start_date=start, end_date=end, rate=rates['festive']))
        return rows

    @staticmethod
    def _month_bracket(year, month_index):
        start = date(year, 1, 3) if month_index == 1 else date(year, month_index, 1)
        if month_index == 12:
            end = date(year, 12, 22)
        else:
            end = date(year, month_index, calendar.monthrange(year, month_index)[1])
        return start, end

    @staticmethod
    def _festive_bracket(year):
        return date(year, 12, 23), date(year + 1, 1, 2)
