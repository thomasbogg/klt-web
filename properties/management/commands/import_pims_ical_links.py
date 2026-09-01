import sys
import time

from django.core.management.base import BaseCommand

from properties.models import Platform, Property, iCalLink

KLT_MANAGEMENT_SOFTWARE_PATH = '/home/thomas-bogg/apps/klt-management-software'
PIMS_URL = 'https://holidayrentalmanagement.com/pimsv18.0'


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-01: klt-web's own iCalLink import feature
    (bookings/management/commands/sync_ical_feeds.py) had never been seeded with the Airbnb/
    Booking.com/Vrbo calendar URLs Thomas had already configured for years in PIMS (the legacy
    system at holidayrentalmanagement.com, Settings > Sync calendars (iCal)). Rather than write a
    new scraper from scratch, this reuses klt-management-software's existing, already-hardened
    `BrowsePIMS` class (selenium + undetected-chromedriver + a persistent Chrome profile
    specifically to avoid re-triggering PIMS' captcha every run) via a sys.path import - klt-web
    and klt-management-software are siblings sharing one venv (see CLAUDE.md), but not installed
    as dependencies of each other, so this only works run from a machine with
    KLT_MANAGEMENT_SOFTWARE_PATH actually present.

    Scrapes TWO things per property from PIMS' iCal Import table (Settings > option=remotecal,
    behind an <iframe src="settings_remotecalendars_ical.php">, itself gated behind a
    `propchoice` <select> per property):
    1. Each row's Name (platform) + "Full URL/Link to iCal file" - matched onto klt-web's own
       Property (by exact `title` string - confirmed 2026-09-01 that PIMS' own property dropdown
       text IS the same string as Property.title, no fuzzy matching needed) and Platform (by
       name, case-insensitive - Airbnb/Booking.com/Vrbo already exist; a Name that doesn't match
       an existing Platform, e.g. a one-off TripAdvisor listing, is reported and skipped rather
       than silently creating a new Platform - see properties/models.py::Platform's own docstring
       for why a new Platform isn't automatically wired into env_settings.PLATFORMS-based logic).
    2. Where PIMS' own per-row filter icon shows a filter is active (filteron.png vs filter.png),
       opens that row's icalFilter.php?id=N popup and scrapes its "Summary/Name of booking
       Contains X" filter (F_opt1/F_opt1_sel/F_opt1_value) into the new
       iCalLink.exclude_summary_contains field, IF that's the filter type PIMS has configured -
       PIMS also offers a start-date and a duration filter type (F_opt2/F_opt3), neither of which
       klt-web's own filter supports yet, so a row using one of those gets its URL imported with
       no filter and is reported separately rather than silently dropped.

    One-off, not idempotent by request (Thomas confirmed a one-time backfill is enough, not an
    ongoing re-sync) - re-running this WILL create duplicate iCalLink rows for anything already
    imported, since there's no uniqueness constraint on (property, platform) to check against (a
    property can and does have more than one link per platform in principle, e.g. testing a
    replacement URL). Check `iCalLink.objects.count()` before re-running.

    Live run 2026-09-01: 107 rows found across 39 properties, 106 imported (1 TripAdvisor row on
    Parque da Corcovada 39-2B skipped - no matching Platform, per Thomas's call), 42 with a
    scraped exclude filter (mostly 'Not Available'/'Airbnb (Not Available)' for Airbnb rows,
    'Tentative' for Vrbo rows), 4 with a PIMS filter of an unsupported type (imported without a
    filter)."""
    help = "One-off: scrape PIMS' existing iCal import URLs (and, where present, their filters) into klt-web's iCalLink model."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Scrape and report what would be created, without writing to the database.",
        )

    def handle(self, *args, **options):
        sys.path.insert(0, KLT_MANAGEMENT_SOFTWARE_PATH)
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        from PIMS.browser import BrowsePIMS

        pims = BrowsePIMS(visible=True)
        pims.goTo()
        pims.login()
        pims.goTo(f'{PIMS_URL}/settings.php?option=remotecal')
        time.sleep(1.5)

        driver = pims._driver
        driver.switch_to.default_content()
        iframe = driver.find_element(By.TAG_NAME, 'iframe')
        driver.switch_to.frame(iframe)

        action_select = Select(driver.find_element(By.NAME, 'ical_action'))
        if action_select.first_selected_option.get_attribute('value') != 'Import':
            action_select.select_by_visible_text('iCal Import')
            time.sleep(1.5)
            driver.switch_to.default_content()
            iframe = driver.find_element(By.TAG_NAME, 'iframe')
            driver.switch_to.frame(iframe)

        prop_select_el = driver.find_element(By.CSS_SELECTOR, 'select[name="propchoice"]')
        properties = [(opt.get_attribute('value'), opt.text.strip()) for opt in Select(prop_select_el).options]
        self.stdout.write(f'Found {len(properties)} properties in PIMS.')

        scraped = self._scrape_all_properties(driver, properties)
        self._scrape_filters(driver, scraped)
        self._write_to_db(scraped, dry_run=options['dry_run'])

    def _scrape_all_properties(self, driver, properties):
        import re

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        rows = []
        for prop_id, prop_title in properties:
            driver.switch_to.default_content()
            iframe = driver.find_element(By.TAG_NAME, 'iframe')
            driver.switch_to.frame(iframe)
            Select(driver.find_element(By.CSS_SELECTOR, 'select[name="propchoice"]')).select_by_value(prop_id)
            time.sleep(1.2)

            driver.switch_to.default_content()
            iframe = driver.find_element(By.TAG_NAME, 'iframe')
            driver.switch_to.frame(iframe)
            html = driver.page_source

            row_ids = sorted({int(m) for m in re.findall(r'iCal_name_(\d+)', html)})
            for row_id in row_ids:
                name_match = re.search(rf'name="iCal_name_{row_id}" value="([^"]*)"', html)
                link_match = re.search(rf'name="iCal_link_{row_id}" value="([^"]*)"', html)
                has_filter = bool(re.search(rf'filteron\.png[^>]*onclick="[^"]*id={row_id}&', html))
                rows.append({
                    'property_title': prop_title, 'pims_row_id': row_id,
                    'name': (name_match.group(1) if name_match else '').replace('&amp;', '&'),
                    'url': (link_match.group(1) if link_match else '').replace('&amp;', '&'),
                    'has_pims_filter': has_filter, 'exclude_terms': [],
                })
            self.stdout.write(f'{prop_title} ({prop_id}): {len(row_ids)} import link(s)')
        return rows

    def _scrape_filters(self, driver, rows):
        import re

        filtered_rows = [r for r in rows if r['has_pims_filter']]
        self.stdout.write(f'Fetching filter details for {len(filtered_rows)} rows with an active PIMS filter...')
        for row in filtered_rows:
            driver.get(f'{PIMS_URL}/icalFilter.php?id={row["pims_row_id"]}')
            time.sleep(0.6)
            html = driver.page_source
            checked = re.search(r'checked=""\s*name="F_opt1"', html) is not None
            is_contains = re.search(r'name="F_opt1_sel"[^>]*>.*?<option value="2" selected="">', html, re.S)
            value_match = re.search(r'name="F_opt1_value" value="([^"]*)"', html)
            if checked and is_contains and value_match:
                row['exclude_terms'] = [value_match.group(1)]
            else:
                row['other_filter_type_not_scraped'] = True

    def _write_to_db(self, rows, dry_run):
        platforms = {p.name.lower(): p for p in Platform.objects.all()}
        properties = {p.title: p for p in Property.objects.all()}

        to_create = []
        skipped = []
        unsupported_filter = [r for r in rows if r.get('other_filter_type_not_scraped')]

        for row in rows:
            prop = properties.get(row['property_title'])
            platform = platforms.get(row['name'].strip().lower())
            if prop is None or platform is None:
                skipped.append(row)
                continue
            to_create.append(iCalLink(
                property=prop, platform=platform, ical_url=row['url'],
                exclude_summary_contains='\n'.join(row['exclude_terms']),
            ))

        self.stdout.write(f'{"Would create" if dry_run else "Creating"} {len(to_create)} iCalLink rows.')
        self.stdout.write(f'Skipped {len(skipped)} rows (no matching Property/Platform):')
        for row in skipped:
            self.stdout.write(f'  {row["property_title"]} | {row["name"]} | {row["url"]}')
        self.stdout.write(f'{len(unsupported_filter)} row(s) had a PIMS filter of an unsupported type (imported without a filter):')
        for row in unsupported_filter:
            self.stdout.write(f'  {row["property_title"]} | {row["name"]}')

        if not dry_run:
            iCalLink.objects.bulk_create(to_create, batch_size=200)
            self.stdout.write(f'Created {len(to_create)} iCalLink rows.')
