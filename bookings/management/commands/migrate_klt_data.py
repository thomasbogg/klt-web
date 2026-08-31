import sqlite3
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from bookings.models import (
    Booking, Arrival, BookingRequestedExtra, Charge, Departure, Email, Extra, Form, RequestType,
    TouristTax, Update,
)
from guests.models import Guest
from properties.models import (
    Accountant, Location, ManagementCompany, Owner, Platform, Price, Property, PropertyPlatformID,
    PropertySpec, SEFDetail,
)

# klt-web trimmed VALID_BOOKING_STATUSES/PROVISIONAL_BOOKING_STATUSES (env_settings.py) to just
# 'Booking confirmed'/'Awaiting payment' 2026-08-25 - these five PIMS-inherited labels were never
# individually meaningful in either app (see [[project_klt_web_guest_registration_migration]] for
# the full writeup). `bookings.enquiryStatus` in KLT.db is unconstrained free text (see
# create.py), so a legacy row can still carry one of these even though klt-web itself no longer
# recognises them - collapse to the surviving canonical status in the same bucket rather than copy
# verbatim, or a migrated booking would silently stop blocking the calendar/bucket correctly
# (exactly what already happened once for real with a drifted 'Booking cancelled' value - see the
# same memory). Anything NOT in this map is passed through unchanged - it's still the migrator's
# job to review the result afterward for any other legacy status this map doesn't yet cover.
LEGACY_ENQUIRY_STATUS_MAP = {
    'Guests have departed': 'Booking confirmed',
    'Guests on-site': 'Booking confirmed',
    'Holiday completed': 'Booking confirmed',
    'Provisional booking': 'Awaiting payment',
    'Dates agreed and held': 'Awaiting payment',
}


def _truncated(value, max_length):
    """Arrival/Departure.flight_number is CharField(max_length=50), but at least one legacy row
    (booking 5149) has a full itinerary description crammed into that column instead of a real
    flight code (92/74 chars) - truncate rather than let one bad row crash the whole migration."""
    return value[:max_length] if value else value


class Command(BaseCommand):
    help = 'Migrate data from existing KLT.db SQLite database to Django models'
    skipped_booking_rows = list()
    # Populated by migrate_extras() for legacy airportTransfers/childSeats/excessBaggage signal
    # that has no reliable structured home in the current AirportTransfer model (which requires a
    # real, non-nullable time - the legacy schema never captured one) - reported as a summary at
    # the end of the run rather than silently dropped or fabricated. See migrate_extras()'s
    # docstring for the full reasoning.
    unmigrated_airport_transfer_bookings = list()

    def add_arguments(self, parser):
        parser.add_argument(
            '--db-path',
            type=str,
            required=True,
            help='Path to the existing KLT.db SQLite database file'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without actually doing it'
        )

    def handle(self, *args, **options):
        db_path = options['db_path']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be written'))

        try:
            # Connect to the old SQLite database
            old_conn = sqlite3.connect(db_path)
            old_conn.row_factory = sqlite3.Row  # Access columns by name
            old_cursor = old_conn.cursor()
            
            with transaction.atomic():
                # Migrate in dependency order
                self.migrate_addresses(old_cursor, dry_run)
                self.migrate_managers(old_cursor, dry_run)
                self.migrate_owners(old_cursor, dry_run)
                self.migrate_accountants(old_cursor, dry_run)
                #self.migrate_prices(old_cursor, dry_run)
                self.migrate_properties(old_cursor, dry_run)
                self.migrate_platform_ids(old_cursor, dry_run)
                self.migrate_specs(old_cursor, dry_run)
                self.migrate_sef_details(old_cursor, dry_run)
                self.migrate_guests(old_cursor, dry_run)
                self.migrate_bookings(old_cursor, dry_run)
                self.migrate_arrivals(old_cursor, dry_run)
                self.migrate_departures(old_cursor, dry_run)
                self.migrate_charges(old_cursor, dry_run)
                self.migrate_touristtax(old_cursor, dry_run)
                self.migrate_extras(old_cursor, dry_run)
                self.migrate_forms(old_cursor, dry_run)
                self.migrate_emails(old_cursor, dry_run)
                #self.migrate_updates(old_cursor, dry_run)

            old_conn.close()
            self.stdout.write(self.style.SUCCESS('Migration completed successfully!'))
            
        except sqlite3.Error as e:
            raise CommandError(f'SQLite error: {e}')
        except Exception as e:
            raise CommandError(f'Migration error: {e}')

    def migrate_addresses(self, cursor, dry_run):
        cursor.execute("SELECT * FROM propertyAddresses")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No addresses to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} property addresses...')
        
        if not dry_run:
            for row in rows:
                Location.objects.create(
                    id=row['id'],
                    title=row['location'],
                    street=row['street'],
                    coordinates=row['coordinates'],
                    map_link=row['map'],
                    directions=row['directions'],
                    nearest_bins=row['nearestBins'],
                    nearest_corner_shop=row['nearestCornerShop'],
                    nearest_supermarket=row['nearestSupermarket']
                )

    def migrate_managers(self, cursor, dry_run):
        # Manager was folded into ManagementCompany (properties/models.py) since this script was
        # first written - legacy company/name/maintenance map onto ManagementCompany's
        # name/head_name/maintenance_name (renamed), everything else is a direct rename. No legacy
        # equivalent for the newer finance_*/operational-default fields - left at their defaults.
        cursor.execute("SELECT * FROM propertyManagers")
        rows = cursor.fetchall()

        if not rows:
            self.stdout.write('No managers to migrate')
            return

        self.stdout.write(f'Migrating {len(rows)} property managers...')

        if not dry_run:
            for row in rows:
                ManagementCompany.objects.create(
                    id=row['id'],
                    name=row['company'],
                    head_name=row['name'],
                    head_email=row['email'],
                    head_phone=row['phone'],
                    maintenance_name=row['maintenance'],
                    maintenance_phone=row['maintenancePhone'],
                    maintenance_email=row['maintenanceEmail'],
                    liaison_name=row['liaison'],
                    liaison_phone=row['liaisonPhone'],
                    liaison_email=row['liaisonEmail'],
                    cleaning_name=row['cleaning'],
                    cleaning_phone=row['cleaningPhone'],
                    cleaning_email=row['cleaningEmail'],
                )

    def migrate_owners(self, cursor, dry_run):
        cursor.execute("SELECT * FROM propertyOwners")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No owners to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} property owners...')
        
        if not dry_run:
            for row in rows:
                Owner.objects.create(
                    id=row['id'],
                    name=row['name'],
                    email=row['email'],
                    phone=row['phone'],
                    nif_number=row['nifNumber'],
                    default_clean=bool(row['defaultClean']),
                    default_meet_greet=bool(row['defaultMeetGreet']),
                    takes_euros=bool(row['takesEuros']),
                    takes_pounds=bool(row['takesPounds']),
                    # wants_accounting has no current equivalent field on Owner (dropped along
                    # with the rest of the model's restructure) - not migrated.
                    cleans_are_invoiced=bool(row['cleansAreInvoiced']),
                    rental_commissions_are_invoiced=bool(row['rentalCommissionsAreInvoiced']),
                    is_paid_regularly=bool(row['isPaidRegularly'])
                )

    def migrate_accountants(self, cursor, dry_run):
        cursor.execute("SELECT * FROM propertyAccountants")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No accountants to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} accountants...')
        
        if not dry_run:
            for row in rows:
                Accountant.objects.create(
                    id=row['id'],
                    company=row['company'],
                    name=row['name'],
                    email=row['email'],
                    phone=row['phone']
                )

    def migrate_prices(self, cursor, dry_run):
        cursor.execute("SELECT * FROM propertyPrices")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No prices to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} property prices...')
        
        if not dry_run:
            for row in rows:
                Price.objects.create(
                    year=row['year'],
                    name=row['name'],
                    january=row['january'],
                    february=row['february'],
                    march=row['march'],
                    april=row['april'],
                    may=row['may'],
                    june=row['june'],
                    july=row['july'],
                    august=row['august'],
                    september=row['september'],
                    october=row['october'],
                    november=row['november'],
                    december=row['december'],
                    festive=row['festive'],
                    early_winter_monthly_rate=row['earlyWinterMonthlyRate'],
                    late_winter_monthly_rate=row['lateWinterMonthlyRate']
                )

    def migrate_properties(self, cursor, dry_run):
        """Property.manager (single FK) was split into booking_company/cleaning_company - the
        legacy weBook/weClean booleans are exactly the gate for which of the two (if either) the
        single legacy managerId should be assigned to (confirmed against the real data: every
        combination of managerId/weBook/weClean in klt_main.db is consistent with "managerId is
        only a real booking/cleaning party when the matching flag is set", matching
        ManagementCompany's own docstring for what a NULL booking_company/cleaning_company means).
        booking_com_title/airbnb_title/send_owner_booking_forms no longer exist on Property -
        the platform listing names are migrated separately, see migrate_platform_ids() below
        (this method depends on it running first, for the same reason charges depends on bookings)."""
        cursor.execute("SELECT * FROM properties")
        rows = cursor.fetchall()

        if not rows:
            self.stdout.write('No properties to migrate')
            return

        self.stdout.write(f'Migrating {len(rows)} properties...')

        if not dry_run:
            for row in rows:
                Property.objects.create(
                    id=row['id'],
                    title=row['name'],
                    short_title=row['shortName'],
                    owner_id=row['ownerId'],
                    location_id=row['addressId'],
                    #price_id=row['priceId'] if row['priceId'] else None,
                    accountant_id=row['accountantId'] if row['accountantId'] else None,
                    al_number=row['alNumber'],
                    booking_company_id=row['managerId'] if row['weBook'] else None,
                    cleaning_company_id=row['managerId'] if row['weClean'] else None,
                    standard_cleaning_fee=row['standardCleaningFee'],
                    # sendOwnerBookingForms/ownerRegistersGuests/lockBoxNumber have no current
                    # equivalent field on Property - not migrated.
                )

    def migrate_platform_ids(self, cursor, dry_run):
        """properties.bookingComName/airbnbName/vrboId replace Property's old hardcoded
        booking_com_title/airbnb_title/vrbo_id fields with the open-ended PropertyPlatformID model
        (properties/models.py) - one row per non-empty legacy value, matched against the Platform
        catalog seeded by properties/migrations/0036_seed_platforms.py (must already exist in the
        target DB - this doesn't create Platform rows itself, since that catalog is admin-managed)."""
        cursor.execute("SELECT id, bookingComName, airbnbName, vrboId FROM properties")
        rows = cursor.fetchall()

        if not rows:
            self.stdout.write('No properties to migrate platform IDs for')
            return

        self.stdout.write(f'Migrating platform IDs for {len(rows)} properties...')

        if not dry_run:
            platform_ids_by_name = {p.name: p.id for p in Platform.objects.all()}
            legacy_columns = (('bookingComName', 'Booking.com'), ('airbnbName', 'Airbnb'), ('vrboId', 'Vrbo'))
            for row in rows:
                for column, platform_name in legacy_columns:
                    listing_id = row[column]
                    if not listing_id:
                        continue
                    platform_id = platform_ids_by_name.get(platform_name)
                    if platform_id is None:
                        self.stdout.write(self.style.WARNING(
                            f'Skipping {platform_name} listing ID for property {row["id"]} - '
                            f'no "{platform_name}" Platform row found in the target DB.'))
                        continue
                    PropertyPlatformID.objects.create(
                        property_id=row['id'], platform_id=platform_id, listing_id=listing_id,
                    )

    def migrate_specs(self, cursor, dry_run):
        cursor.execute("SELECT * FROM propertySpecs")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No specs to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} property specs...')
        
        if not dry_run:
            for row in rows:
                # Spec was renamed to PropertySpec, and is_listed no longer exists on it (not
                # migrated) - both since this script was first written.
                PropertySpec.objects.create(
                    id=row['id'],
                    property_id=row['propertyId'],
                    is_sea_view=bool(row['isSeaView']),
                    is_upper_floor=bool(row['isUpperFloor']),
                    is_beachfront=bool(row['isBeachfront']),
                    bedrooms=row['bedrooms'],
                    bathrooms=row['bathrooms'],
                    square_metres=row['squareMetres'],
                    max_guests=row['maxGuests']
                )

    def migrate_sef_details(self, cursor, dry_run):
        cursor.execute("SELECT * FROM propertySEFDetails")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No SEF details to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} SEF details...')
        
        if not dry_run:
            for row in rows:
                SEFDetail.objects.create(
                    id=row['id'],
                    property_id=row['propertyId'],
                    unidade_hoteleira=row['unidadeHoteleira'],
                    estabelecimento=row['estabelecimento'],
                    chave_de_autenticacao=row['chaveDeAutenticacao']
                )

    def migrate_guests(self, cursor, dry_run):
        cursor.execute("SELECT * FROM guests")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No guests to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} guests...')
        
        # idCard/nifNumber/nationality no longer land on Guest (removed 2026-08-24, see
        # bookings.GuestRegistration instead - one row per BookingGuest, not per Guest, so
        # migrating that data needs a bookings/guests pass, not this one; see the migration
        # notes memory for the planned approach).
        if not dry_run:
            for row in rows:
                Guest.objects.create(
                    id=row['id'],
                    first_name=row['firstName'],
                    last_name=row['lastName'],
                    email=row['email'],
                    phone=row['phone'],
                    preferred_language=row['preferredLanguage']
                )

    def migrate_bookings(self, cursor, dry_run):
        cursor.execute("SELECT * FROM bookings")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No bookings to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} bookings...')
        
        if not dry_run:
            from django.utils.dateparse import parse_datetime, parse_date
            for row in rows:
                arrival_date = cursor.execute("SELECT date FROM arrivals WHERE bookingId = ?", (row['id'],)).fetchone()
                departure_date = cursor.execute("SELECT date FROM departures WHERE bookingId = ?", (row['id'],)).fetchone()
                if not arrival_date or not departure_date:
                    self.stdout.write(self.style.WARNING(f'Booking {row} has missing arrival or departure date. Skipping.'))
                    self.skipped_booking_rows.append(row['id'])
                    continue
                Booking.objects.create(
                    id=row['id'],
                    property_id=row['propertyId'],
                    guest_id=row['guestId'],
                    pims_id=row['PIMSId'],
                    platform_id=row['platformId'],
                    arrival_date=parse_date(arrival_date['date']) if arrival_date else None,
                    departure_date=parse_date(departure_date['date']) if departure_date else None,
                    is_owner=bool(row['isOwner']),
                    enquiry_status=LEGACY_ENQUIRY_STATUS_MAP.get(row['enquiryStatus'], row['enquiryStatus']),
                    enquiry_date=parse_date(row['enquiryDate']) if row['enquiryDate'] else None,
                    enquiry_source=row['enquirySource'],
                    adults=row['adults'],
                    children=row['children'],
                    babies=row['babies'],
                    # manualGuests has no current equivalent - Booking.manual_override means
                    # something different now (an extra-nights date adjustment flag for the
                    # external platform-sync scraper, see its own docstring) - not migrated.
                    # reference is left blank: these are closed historical bookings that predate
                    # the reference-based self-service system, same "predates this feature"
                    # convention already used for Payment/BalancePayment elsewhere in this codebase.
                    last_updated=parse_datetime(row['lastUpdated']) or parse_date(row['lastUpdated'])
                )

    def migrate_arrivals(self, cursor, dry_run):
        cursor.execute("SELECT * FROM arrivals")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No arrivals to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} arrivals...')
        
        if not dry_run:
            from django.utils.dateparse import parse_date, parse_time
            for row in rows:
                if row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(f'Skipping arrival for booking {row["bookingId"]} due to missing booking data.'))
                    continue
                Arrival.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    flight_number=_truncated(row['flightNumber'], 50),
                    method='flight_faro' if row['isFaro'] else 'other',
                    time=parse_time(row['time']) if row['time'] else None,
                    details=row['details'],
                    self_check_in=bool(row['selfCheckIn']) if row['selfCheckIn'] is not None else None,
                    meet_greet=bool(row['meetGreet']),
                )
                #Booking.objects.filter(id=row['bookingId']).update(arrival_date=parse_date(row['date']) if row['date'] else None)

    def migrate_departures(self, cursor, dry_run):
        cursor.execute("SELECT * FROM departures")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No departures to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} departures...')
        
        if not dry_run:
            from django.utils.dateparse import parse_date, parse_time
            for row in rows:
                if row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(f'Skipping departure for booking {row["bookingId"]} due to missing booking data.'))
                    continue
                Departure.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    flight_number=_truncated(row['flightNumber'], 50),
                    method='flight_faro' if row['isFaro'] else 'other',
                    time=parse_time(row['time']) if row['time'] else None,
                    details=row['details'],
                    clean=bool(row['clean']),
                )
                #Booking.objects.filter(id=row['bookingId']).update(departure_date=parse_date(row['date']) if row['date'] else None)

    def migrate_charges(self, cursor, dry_run):
        cursor.execute("SELECT * FROM charges")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No charges to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} charges...')
        
        if not dry_run:
            for row in rows:
                if row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(f'Skipping charge for booking {row["bookingId"]} due to missing booking data.'))
                    continue
                Charge.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    bank_transfer=bool(row['bankTransfer']) if row['bankTransfer'] is not None else None,
                    credit_card=bool(row['creditCard']) if row['creditCard'] is not None else None,
                    currency=row['currency'],
                    basic_rental=row['basicRental'],
                    admin=row['admin'],
                    security=row['security'],
                    security_method=row['securityMethod'],
                    platform_fee=row['platformFee'],
                    extra_nights=row['extraNights'],
                    manual_charges=bool(row['manualCharges']) if row['manualCharges'] is not None else None
                )

    def migrate_touristtax(self, cursor, dry_run):
        cursor.execute("SELECT * FROM touristtax")
        rows = cursor.fetchall()

        if not rows:
            self.stdout.write('No tourist tax records to migrate')
            return

        self.stdout.write(f'Migrating {len(rows)} tourist tax records...')

        if not dry_run:
            for row in rows:
                charge_row = cursor.execute(
                    "SELECT bookingId FROM charges WHERE id = ?", (row['chargesId'],)
                ).fetchone()
                if not charge_row or charge_row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(
                        f'Skipping tourist tax {row["id"]} - charge/booking not migrated.'))
                    continue
                TouristTax.objects.create(
                    id=row['id'],
                    booking_id=charge_row['bookingId'],
                    total=row['total'],
                    provider='revolut',
                    status='paid' if row['paid'] else 'pending',
                    revolut_order_id=row['orderId'] or None,
                    # Legacy schema has no paid-timestamp column, only a boolean - paid_at stays
                    # null for migrated rows even when status='paid'. Acceptable for historical
                    # data, not an oversight.
                )

    def migrate_extras(self, cursor, dry_run):
        """Extra's field set was restructured (Welcome Pack/Cot/Late Checkout/AirportTransfer
        redesign, see project_klt_web_extras_feature in memory) since this migrator was first
        written - welcome_pack_modifications, other_requests, airport_transfers,
        airport_transfer_inbound_only/outbound_only, child_seats, and excess_baggage no longer
        exist on Extra at all. This rewrite (2026-08-30) maps each legacy field to its closest
        current home rather than dropping the data or crashing on the removed kwargs:

        - cot/high_chair/welcome_pack/mid_stay_clean/late_checkout/extra_nights/owner_is_paying
          are unaffected 1:1 boolean fields, still present on Extra.
        - welcomePackModifications (freeform text) -> Extra.welcome_pack_note, the closest
          surviving free-text field (now labelled "allergies/dietary notes only" - relaxed here
          deliberately for migrated historical data rather than dropping real guest requests).
        - otherRequests (freeform text) -> one BookingRequestedExtra row against a shared
          catch-all RequestType, rather than trying to match it to a real catalog item.
        - airportTransfers/airportTransferInboundOnly/airportTransferOutboundOnly/childSeats/
          excessBaggage -> NOT migrated into a fabricated AirportTransfer row (that model
          requires a real, non-nullable time with no legacy equivalent, plus per-transfer detail
          the old schema never captured). Collected into
          self.unmigrated_airport_transfer_bookings instead and reported as a summary at the end
          of this method, so the gap is visible and reviewable rather than silently dropped."""
        cursor.execute("SELECT * FROM extras")
        rows = cursor.fetchall()

        if not rows:
            self.stdout.write('No extras to migrate')
            return

        self.stdout.write(f'Migrating {len(rows)} extras...')

        if not dry_run:
            legacy_request_type = None
            for row in rows:
                if row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(f'Skipping extra for booking {row["bookingId"]} due to missing booking data.'))
                    continue
                Extra.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    cot=bool(row['cot']) if row['cot'] is not None else None,
                    high_chair=bool(row['highChair']) if row['highChair'] is not None else None,
                    welcome_pack=bool(row['welcomePack']) if row['welcomePack'] is not None else None,
                    welcome_pack_note=row['welcomePackModifications'] or None,
                    mid_stay_clean=bool(row['midStayClean']) if row['midStayClean'] is not None else None,
                    late_checkout=bool(row['lateCheckout']) if row['lateCheckout'] is not None else None,
                    extra_nights=bool(row['extraNights']) if row['extraNights'] is not None else None,
                    owner_is_paying=bool(row['ownerIsPaying']) if row['ownerIsPaying'] is not None else None
                )

                if row['otherRequests']:
                    if legacy_request_type is None:
                        legacy_request_type, _created = RequestType.objects.get_or_create(
                            name='Legacy request (see note)', defaults={'default_price': 0, 'active': False},
                        )
                    BookingRequestedExtra.objects.create(
                        booking_id=row['bookingId'], request_type=legacy_request_type,
                        quantity=1, note=row['otherRequests'][:200], price_at_request=0,
                    )

                if row['airportTransfers'] or row['childSeats'] or row['excessBaggage']:
                    self.unmigrated_airport_transfer_bookings.append({
                        'booking_id': row['bookingId'],
                        'inbound_only': bool(row['airportTransferInboundOnly']),
                        'outbound_only': bool(row['airportTransferOutboundOnly']),
                        'child_seats': row['childSeats'],
                        'excess_baggage': row['excessBaggage'],
                    })

            if self.unmigrated_airport_transfer_bookings:
                self.stdout.write(self.style.WARNING(
                    f'{len(self.unmigrated_airport_transfer_bookings)} booking(s) had legacy '
                    f'airport-transfer signal that could not be migrated into a real AirportTransfer '
                    f'row (no reliable time value in the legacy data) - review manually if needed:'
                ))
                for entry in self.unmigrated_airport_transfer_bookings:
                    self.stdout.write(self.style.WARNING(f'  {entry}'))

    def migrate_forms(self, cursor, dry_run):
        cursor.execute("SELECT * FROM forms")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No forms to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} forms...')
        
        if not dry_run:
            for row in rows:
                if row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(f'Skipping form for booking {row["bookingId"]} due to missing booking data.'))
                    continue
                Form.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    balance_payment=row['balancePayment'],
                    arrival_questionnaire=row['arrivalQuestionnaire'],
                    guest_registration=row['guestRegistration'],
                    guest_registration_done=bool(row['guestRegistrationDone']) if row['guestRegistrationDone'] is not None else None,
                    security_deposit=row['securityDeposit'],
                    pims_uin=row['PIMSuin'],
                    pims_oid=row['PIMSoid']
                )

    def migrate_emails(self, cursor, dry_run):
        cursor.execute("SELECT * FROM emails")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No emails to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} emails...')
        
        if not dry_run:
            for row in rows:
                if row['bookingId'] in self.skipped_booking_rows:
                    self.stdout.write(self.style.WARNING(f'Skipping email for booking {row["bookingId"]} due to missing booking data.'))
                    continue
                Email.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    balance_payment=bool(row['balancePayment']) if row['balancePayment'] is not None else None,
                    arrival_questionnaire=bool(row['arrivalQuestionnaire']) if row['arrivalQuestionnaire'] is not None else None,
                    security_deposit_request=bool(row['securityDepositRequest']) if row['securityDepositRequest'] is not None else None,
                    arrival_information=bool(row['arrivalInformation']) if row['arrivalInformation'] is not None else None,
                    guest_registration_form=bool(row['guestRegistrationForm']) if row['guestRegistrationForm'] is not None else None,
                    check_in_instructions=bool(row['checkInInstructions']) if row['checkInInstructions'] is not None else None,
                    final_days_reminder=bool(row['finalDaysReminder']) if row['finalDaysReminder'] is not None else None,
                    goodbye=bool(row['goodbye']) if row['goodbye'] is not None else None,
                    management=bool(row['management']) if row['management'] is not None else None,
                    pay_owner=bool(row['payOwner']) if row['payOwner'] is not None else None,
                    security_deposit_return=bool(row['securityDepositReturn']) if row['securityDepositReturn'] is not None else None,
                    airport_transfers=bool(row['airportTransfers']) if row['airportTransfers'] is not None else None,
                    guest_registration_form_to_owner=bool(row['guestRegistrationFormToOwner']) if row['guestRegistrationFormToOwner'] is not None else None,
                    paused=bool(row['paused']) if row['paused'] is not None else None
                )

    def migrate_updates(self, cursor, dry_run):
        cursor.execute("SELECT * FROM updates")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No updates to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} updates...')
        
        if not dry_run:
            from django.utils.dateparse import parse_date
            for row in rows:
                Update.objects.create(
                    id=row['id'],
                    booking_id=row['bookingId'],
                    date=parse_date(row['date']),
                    details=row['details'],
                    extras=row['extras'],
                    email_sent=bool(row['emailSent'])
                )