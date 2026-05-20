import sqlite3
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from bookings.models import (
    Booking, Arrival, Departure, Charge, Extra, Form, Email, Update
)
from guests.models import Guest
from properties.models import (
    Location, Manager, Owner, Accountant, Price, Property, Spec, SEFDetail
)


class Command(BaseCommand):
    help = 'Migrate data from existing KLT.db SQLite database to Django models'
    skipped_booking_rows = list()

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
                self.migrate_specs(old_cursor, dry_run)
                self.migrate_sef_details(old_cursor, dry_run)
                self.migrate_guests(old_cursor, dry_run)
                self.migrate_bookings(old_cursor, dry_run)
                self.migrate_arrivals(old_cursor, dry_run)
                self.migrate_departures(old_cursor, dry_run)
                self.migrate_charges(old_cursor, dry_run)
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
        cursor.execute("SELECT * FROM propertyManagers")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No managers to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} property managers...')
        
        if not dry_run:
            for row in rows:
                Manager.objects.create(
                    id=row['id'],
                    company=row['company'],
                    head_name=row['name'],
                    head_email=row['email'],
                    head_phone=row['phone'],
                    maintenance=row['maintenance'],
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
                    wants_accounting=bool(row['wantsAccounting']),
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
                    name=row['name'],
                    short_name=row['shortName'],
                    owner_id=row['ownerId'],
                    manager_id=row['managerId'],
                    address_id=row['addressId'],
                    #price_id=row['priceId'] if row['priceId'] else None,
                    accountant_id=row['accountantId'] if row['accountantId'] else None,
                    al_number=row['alNumber'],
                    we_book=bool(row['weBook']),
                    booking_com_name=row['bookingComName'],
                    airbnb_name=row['airbnbName'],
                    we_clean=bool(row['weClean']),
                    standard_cleaning_fee=row['standardCleaningFee'],
                    send_owner_booking_forms=bool(row['sendOwnerBookingForms'])
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
                Spec.objects.create(
                    id=row['id'],
                    property_id=row['propertyId'],
                    is_listed=bool(row['isListed']),
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
        
        if not dry_run:
            for row in rows:
                Guest.objects.create(
                    id=row['id'],
                    first_name=row['firstName'],
                    last_name=row['lastName'],
                    email=row['email'],
                    phone=row['phone'],
                    id_card=row['idCard'],
                    nif_number=row['nifNumber'],
                    nationality=row['nationality'],
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
                    enquiry_status=row['enquiryStatus'],
                    enquiry_date=parse_date(row['enquiryDate']) if row['enquiryDate'] else None,
                    enquiry_source=row['enquirySource'],
                    adults=row['adults'],
                    children=row['children'],
                    babies=row['babies'],
                    manual_guests=bool(row['manualGuests']),
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
                    flight_number=row['flightNumber'],
                    is_faro=bool(row['isFaro']) if row['isFaro'] is not None else None,
                    time=parse_time(row['time']) if row['time'] else None,
                    details=row['details'],
                    self_check_in=bool(row['selfCheckIn']) if row['selfCheckIn'] is not None else None,
                    meet_greet=bool(row['meetGreet']),
                    manual_date=bool(row['manualDate']) if row['manualDate'] is not None else None
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
                    flight_number=row['flightNumber'],
                    is_faro=bool(row['isFaro']) if row['isFaro'] is not None else None,
                    time=parse_time(row['time']) if row['time'] else None,
                    details=row['details'],
                    clean=bool(row['clean']),
                    manual_date=bool(row['manualDate']) if row['manualDate'] is not None else None
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

    def migrate_extras(self, cursor, dry_run):
        cursor.execute("SELECT * FROM extras")
        rows = cursor.fetchall()
        
        if not rows:
            self.stdout.write('No extras to migrate')
            return
            
        self.stdout.write(f'Migrating {len(rows)} extras...')
        
        if not dry_run:
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
                    welcome_pack_modifications=row['welcomePackModifications'],
                    mid_stay_clean=bool(row['midStayClean']) if row['midStayClean'] is not None else None,
                    late_checkout=bool(row['lateCheckout']) if row['lateCheckout'] is not None else None,
                    other_requests=row['otherRequests'],
                    extra_nights=bool(row['extraNights']) if row['extraNights'] is not None else None,
                    airport_transfers=bool(row['airportTransfers']) if row['airportTransfers'] is not None else None,
                    airport_transfer_inbound_only=bool(row['airportTransferInboundOnly']) if row['airportTransferInboundOnly'] is not None else None,
                    airport_transfer_outbound_only=bool(row['airportTransferOutboundOnly']) if row['airportTransferOutboundOnly'] is not None else None,
                    child_seats=row['childSeats'],
                    excess_baggage=row['excessBaggage'],
                    owner_is_paying=bool(row['ownerIsPaying']) if row['ownerIsPaying'] is not None else None
                )

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