import calendar
from datetime import date, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField

from bookings.utils import generate_reference_candidate
from env_settings import VALID_BOOKING_STATUSES, PROVISIONAL_BOOKING_STATUSES
from properties.models import Location, Property
from guests.models import Guest

TWO_PLACES = Decimal('0.01')
REFERENCE_GENERATION_ATTEMPTS = 5

# The only quote/charge currencies offered for now. Kept as a hardcoded pair rather than a free-text
# field so a staff member manually editing a booking in the admin can't introduce a currency the rest
# of the system (GBP conversion, price display toggle) doesn't know how to handle.
CURRENCY_CHOICES = (
    ('EUR', 'EUR'),
    ('GBP', 'GBP'),
)

MONTH_CHOICES = tuple((i, calendar.month_name[i]) for i in range(1, 13))


class BookingSettings(models.Model):
    """Site-wide financial rules for costing a booking. Singleton — always exactly one row (pk=1)."""
    admin_fee_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.50'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Admin fee charged cumulatively on top of the basic rental, as a percentage."
    )
    deposit_percent_at_booking = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('25.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Percentage of the rental + admin fee due at the reservation stage. The remainder is due at the balance payment stage."
    )
    balance_due_days_before_arrival = models.PositiveIntegerField(
        default=56,
        help_text="How many days before arrival the balance payment becomes due."
    )
    balance_reminder_days_before_arrival = models.PositiveIntegerField(
        default=63,
        help_text="How many days before arrival a balance-payment reminder becomes due for staff "
                  "follow-up (surfaced in the Balance Payments admin, not sent automatically yet). "
                  "Should be greater than balance_due_days_before_arrival so the guest gets some "
                  "notice before the balance is actually due - the gap between the two is that "
                  "notice period."
    )
    extras_edit_cutoff_days_before_arrival = models.PositiveIntegerField(
        default=3,
        help_text="How many days before arrival Extras (Welcome Pack, Cot/High Chair, Late "
                  "Checkout, Airport Transfers, special requests) can still be edited self-serve "
                  "via the Manage Booking hub."
    )
    security_deposit_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('200.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Fixed refundable security deposit. Collected in cash at check-in, separate from the online rental/admin payment split."
    )
    monthly_discount_min_nights = models.PositiveIntegerField(
        default=28,
        help_text="Minimum number of nights a stay must be for a property's monthly discount to apply."
    )
    gbp_conversion_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal('0.8600'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Euro-to-Pound rate for optional GBP price quotes (amount in GBP = amount in EUR × this rate). Charges are always recorded in EUR regardless of the quote currency shown to the guest."
    )
    revolut_hold_minutes = models.PositiveIntegerField(
        default=20,
        help_text="Minutes a Revolut-path deposit hold lasts with no payment signal at all before being released."
    )
    revolut_hold_extension_minutes = models.PositiveIntegerField(
        default=20,
        help_text="Extra minutes granted when Revolut reports a payment in progress (e.g. mid Open Banking bank-app confirmation), instead of releasing the hold."
    )
    payment_clearing_business_days = models.PositiveIntegerField(
        default=3,
        help_text="Business days (Mon-Fri) a deposit hold lasts once a payment is confirmed to be clearing: "
                   "for Wise, from the moment the reservation is made (Wise gives no in-progress signal to "
                   "extend on); for Revolut, from the moment Revolut reports ORDER_PAYMENT_AUTHENTICATED (the "
                   "guest approved payment at their own bank - card payments settle in seconds after this so "
                   "the long window costs them nothing, but Open Banking bank transfers can take up to 2 "
                   "business days to clear, and Revolut doesn't tell us which one a guest used)."
    )
    adult_min_age = models.PositiveIntegerField(
        default=13,
        help_text="Age at time of stay (inclusive) from which a guest is priced as an adult."
    )
    child_min_age = models.PositiveIntegerField(
        default=2,
        help_text="Age at time of stay (inclusive) from which a guest is priced as a child rather than "
                   "an infant. Below this: infant, currently free (see cot-as-extra, not yet built)."
    )
    tourist_tax_per_night = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('2.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Flat municipal tourist tax charged per qualifying guest, per night."
    )
    tourist_tax_min_age = models.PositiveIntegerField(
        default=13,
        help_text="Age at time of stay (inclusive) from which a guest is liable for tourist tax. "
                  "Deliberately separate from adult_min_age above - this tracks a municipal tax-law "
                  "threshold, not a pricing decision, even though both happen to be 13 today."
    )
    tourist_tax_max_nights = models.PositiveIntegerField(
        default=7,
        help_text="Maximum number of nights per guest that tourist tax is charged for, regardless "
                  "of how much longer the actual stay is."
    )

    # Cost dict keys from compute_costs() that represent a money amount and are shown converted to
    # GBP when a guest toggles the currency display. security_deposit is deliberately excluded: it's
    # cash collected locally in Portugal at check-in and stays in EUR regardless of quote currency.
    GBP_DISPLAY_COST_KEYS = ('rental_total', 'admin_fee', 'subtotal', 'due_at_booking', 'due_at_balance')

    class Meta:
        db_table = 'booking_settings'
        verbose_name = 'Booking Settings'
        verbose_name_plural = 'Booking Settings'

    def __str__(self):
        return "Booking Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

    def compute_costs(self, rental_total, arrival_date=None, today=None):
        """Cost breakdown for a stay's final rental total (already net of any discount, plus any
        extra-guest surcharge - see properties/utils.py::get_stay_total_price() and
        Charge.total_rental): admin fee, booking/balance split, balance due date.

        The security deposit is reported separately since it's collected in cash at check-in,
        not part of the online rental/admin payment split.

        Collapses to a single payment when the stay is already inside the balance window at
        booking time (balance_due_date would fall on/before today) - there's no point asking for
        25% now and the remaining 75% by a date that's already passed, so due_at_booking absorbs
        the full subtotal, due_at_balance is zeroed, and balance_due_date is cleared entirely
        (rather than left as a stale past date) so it doubles as the "this booking never gets a
        balance stage" signal for the guest-facing Extras-at-balance flow.
        """
        rental_total = Decimal(rental_total)
        admin_fee = (rental_total * self.admin_fee_percent / Decimal('100')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        subtotal = rental_total + admin_fee
        due_at_booking, due_at_balance, balance_due_date = self.split_subtotal(subtotal, arrival_date, today)

        return {
            'rental_total': rental_total,
            'admin_fee': admin_fee,
            'subtotal': subtotal,
            'due_at_booking': due_at_booking,
            'due_at_balance': due_at_balance,
            'balance_due_date': balance_due_date,
            'security_deposit': self.security_deposit_amount,
        }

    def split_subtotal(self, subtotal, arrival_date=None, today=None):
        """The booking/balance-window portion of compute_costs(), for a subtotal that's already
        final (admin fee already included, e.g. Charge.total_rental + Charge.admin) - use this
        instead of compute_costs() to re-derive due_at_booking/due_at_balance for an existing
        Charge, since compute_costs() always adds its own fresh admin_fee_percent on top of
        whatever's passed in and would double-count an admin fee already baked into the subtotal."""
        subtotal = Decimal(subtotal)
        today = today or date.today()
        due_at_booking = (subtotal * self.deposit_percent_at_booking / Decimal('100')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        due_at_balance = subtotal - due_at_booking
        balance_due_date = arrival_date - timedelta(days=self.balance_due_days_before_arrival) if arrival_date else None

        if balance_due_date is not None and balance_due_date <= today:
            due_at_booking = subtotal
            due_at_balance = Decimal('0')
            balance_due_date = None

        return due_at_booking, due_at_balance, balance_due_date

    def to_gbp(self, amount):
        return (Decimal(amount) * self.gbp_conversion_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    def costs_in_gbp(self, costs):
        """GBP-converted view of a compute_costs() dict, for optional display alongside the EUR original."""
        return {key: self.to_gbp(costs[key]) for key in self.GBP_DISPLAY_COST_KEYS if key in costs}


class BookingQuerySet(models.QuerySet):
    def holding(self):
        """Bookings that currently occupy the calendar: valid statuses, plus provisional statuses
        whose hold hasn't expired (or has no expiry at all - staff-held, pre-existing, or synced
        from a platform, none of which set hold_expires_at)."""
        now = timezone.now()
        return self.filter(
            models.Q(enquiry_status__in=VALID_BOOKING_STATUSES) |
            models.Q(enquiry_status__in=PROVISIONAL_BOOKING_STATUSES, hold_expires_at__isnull=True) |
            models.Q(enquiry_status__in=PROVISIONAL_BOOKING_STATUSES, hold_expires_at__gt=now)
        )

    def overlapping(self, property, start_date, end_date):
        return self.holding().filter(
            property_id=getattr(property, 'pk', property),
            arrival_date__lt=end_date,
            departure_date__gt=start_date,
        )

    def next_confirmed_arrival_after(self, property, date):
        """Earliest confirmed (VALID_BOOKING_STATUSES) arrival for `property` strictly after
        `date`, or None if uncapped. Confirmed only, not holding() - a still-provisional hold
        shouldn't constrain where a cleaning task can be dragged to (see
        staff/utils.py::cleaning_task_valid_range)."""
        return self.filter(
            property_id=getattr(property, 'pk', property),
            enquiry_status__in=VALID_BOOKING_STATUSES,
            arrival_date__gt=date,
        ).order_by('arrival_date').values_list('arrival_date', flat=True).first()

    def next_confirmed_booking_after(self, property, date):
        """Same lookup as next_confirmed_arrival_after but returns the whole Booking (not just its
        arrival_date) - for display purposes (the cleaning-task popup's "next check-in" summary,
        staff/views.py::StaffCleaningTaskDetailView), not the date-boundary math that function
        exists for."""
        return self.filter(
            property_id=getattr(property, 'pk', property),
            enquiry_status__in=VALID_BOOKING_STATUSES,
            arrival_date__gt=date,
        ).select_related('arrival', 'guest').order_by('arrival_date').first()


class Booking(models.Model):
    """Main booking model."""
    property = models.ForeignKey(Property, on_delete=models.PROTECT)
    guest = models.ForeignKey(Guest, on_delete=models.PROTECT)

    # Booking identifiers
    reference = models.CharField(max_length=20, unique=True, blank=True, null=True, db_index=True)
    pims_id = models.IntegerField(blank=True, null=True)
    platform_id = models.CharField(max_length=200, blank=True, null=True)
    # The imported platform VEVENT's UID (see bookings/utils.py::sync_ical_link()) - not a URL
    # despite the field's history (was URLField, never actually populated by any live code path
    # until iCal sync). Real UIDs are typically email-address-shaped (e.g. "xxxx@airbnb.com"), not
    # a URL, so URLField would reject them if ever passed through full_clean()/a ModelForm.
    ical_uid = models.CharField(max_length=255, blank=True, null=True)

    # Booking details
    arrival_date = models.DateField()
    departure_date = models.DateField()
    manual_override = models.BooleanField(
        default=False,
        help_text="Set automatically when staff record an extra-nights date adjustment (see "
                  "BookingDateAdjustment). The external platform-sync scraper must check this "
                  "before touching arrival_date/departure_date on this booking - true means the "
                  "dates were adjusted directly with the guest and the scraper must not overwrite them.",
    )
    is_owner = models.BooleanField()
    enquiry_status = models.CharField(max_length=100)
    enquiry_date = models.DateField(blank=True, null=True)
    enquiry_source = models.CharField(max_length=100)

    # Guest numbers
    adults = models.IntegerField()
    children = models.IntegerField()
    babies = models.IntegerField()

    # Deposit-hold expiry for online bookings awaiting payment. NULL means "no expiry, blocks
    # indefinitely" - true for every booking that predates this field and every non-online booking
    # (platform-synced or staff-held in the admin), so they keep blocking the calendar unchanged.
    # klt-hooks extends this directly via raw SQL when a Revolut payment is detected in progress -
    # see libraries/banking/revolut.py and the klt-hooks postgres_bookings.py module.
    hold_expires_at = models.DateTimeField(blank=True, null=True)

    # Metadata
    last_updated = models.DateTimeField()

    objects = BookingQuerySet.as_manager()

    class Meta:
        db_table = 'bookings'
        verbose_name = 'Booking'
        verbose_name_plural = 'Bookings'

    def __str__(self):
        return f"{self.property.short_title} - {self.guest.last_name} ({self.id})"

    def total_guests(self):
        """Current party size - the actual named guest list (self.party) once one exists, falling
        back to the original adults+children+babies counts from booking time for a booking whose
        guest list hasn't been filled in yet at all (adults/children/babies do get kept in sync
        with the real party by both BookingManageGuestAddView and BookingManageGuestRemoveView
        once any change happens, but that sync doesn't exist for a booking nobody has touched via
        either flow, which is exactly the gap this fallback covers).

        Deliberately a plain method, not @property: this model has a field literally named
        `property` (the FK to properties.Property), which rebinds the name `property` inside this
        class body - any `@property` decorator anywhere in this class resolves to that FK field
        instead of the builtin and crashes at import time ("'ForeignKey' object is not callable"),
        confirmed the hard way. Django templates still call a zero-arg method automatically, so
        `{{ booking.total_guests }}` works the same either way - only Python call sites need the
        explicit ()."""
        party_count = self.party.count()
        return party_count if party_count else self.adults + self.children + self.babies

    def clean(self):
        super().clean()
        if self.property_id and self.arrival_date and self.departure_date:
            overlap = Booking.objects.overlapping(
                self.property_id, self.arrival_date, self.departure_date
            ).exclude(pk=self.pk).first()
            if overlap:
                message = f"These dates overlap an existing booking ({overlap.arrival_date} to {overlap.departure_date})."
                raise ValidationError({'arrival_date': message, 'departure_date': message})
        # The guest-facing party-size flow (bookings/views.py) already enforces this before it ever
        # constructs/saves a Booking, so this only actually bites on the staff edit path (which
        # otherwise had no cap at all - see staff/views.py::_update_booking) - kept here rather than
        # duplicated per-view so any future edit path gets the same protection for free. Guarded on
        # specs existing at all - PropertySpec is a separate row, not auto-created for every
        # Property (confirmed against staff/views.py's own get_or_create calls), so plenty of real
        # and test properties have none.
        specs = self.property and getattr(self.property, 'specs', None)
        if specs is not None and None not in (self.adults, self.children, self.babies):
            total_guests = self.adults + self.children + self.babies
            if total_guests > specs.max_guests:
                message = f"This property allows a maximum of {specs.max_guests} guests (currently {total_guests})."
                raise ValidationError({'adults': message})

    def save(self, *args, **kwargs):
        if not self.pk and not self.reference:
            for _ in range(REFERENCE_GENERATION_ATTEMPTS):
                candidate = generate_reference_candidate()
                if not Booking.objects.filter(reference=candidate).exists():
                    self.reference = candidate
                    break
            else:
                raise RuntimeError("Could not generate a unique booking reference.")
        super().save(*args, **kwargs)


class BookingGuest(models.Model):
    """One party member on a booking, named + aged for pricing and (later) SEF registration -
    separate from guests.models.Guest, which is keyed by email and can be shared across bookings,
    so correcting a typo here never mutates a shared record. is_lead marks the row pre-filled from
    Booking.guest's name on the booking-details page (see bookings/views.py::BookingDetailsView)."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='party')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    age = models.PositiveIntegerField(validators=[MaxValueValidator(120)])
    is_lead = models.BooleanField(default=False)
    added_via_adjustment = models.ForeignKey(
        'GuestListAdjustment', on_delete=models.SET_NULL, null=True, blank=True, related_name='added_guests',
        help_text="Set only when this row was added post-payment via the Manage Booking hub. Null "
                  "for every guest present at the original deposit/balance stage."
    )

    class Meta:
        db_table = 'booking_guests'
        verbose_name = 'Booking Guest'
        verbose_name_plural = 'Booking Guests'
        ordering = ('-is_lead', 'id')

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.booking})"


class GuestRegistration(models.Model):
    """SEF-mandated guest identity details (Portuguese border-registration requirement) - one per
    BookingGuest, captured guest-facing via the Manage Booking hub's Guest Registrations section
    (bookings/views.py::BookingManageGuestRegistrationsView, added 2026-08-24 per Thomas's
    reference screenshot of the legacy klt-management-software equivalent form). Forwarding this
    on to SEF isn't built yet - this model is only the capture step Thomas asked for first, one
    guest at a time, get_or_create'd against whichever BookingGuest rows currently exist (adding
    more requires adding them to the Guest List first - there's deliberately no way to register a
    guest who hasn't been named)."""

    class IDType(models.TextChoices):
        ID_CARD = 'id_card', 'ID card'
        PASSPORT = 'passport', 'Passport'

    booking_guest = models.OneToOneField(BookingGuest, on_delete=models.CASCADE, related_name='registration')
    # A guest with a Portuguese NIF only needs to give us that number - the fields below aren't
    # asked for at all in that case (Thomas: "we don't ask them to fill out this form, only
    # provide us with that number"). has_nif is nullable so "not yet answered" (a fresh guest) is
    # distinguishable from a real "No".
    has_nif = models.BooleanField(null=True, blank=True)
    nif_number = models.CharField(max_length=20, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    place_of_birth = CountryField(blank_label='-', blank=True)
    nationality = CountryField(blank_label='-', blank=True)
    country_of_residence = CountryField(blank_label='-', blank=True)
    id_type = models.CharField(max_length=10, choices=IDType.choices, blank=True)
    id_number = models.CharField(max_length=50, blank=True)
    issued_by = CountryField(blank_label='-', blank=True)

    class Meta:
        db_table = 'guest_registrations'
        verbose_name = 'Guest Registration'
        verbose_name_plural = 'Guest Registrations'

    def __str__(self):
        return f"Registration for {self.booking_guest}"

    def is_complete(self):
        if self.has_nif is None:
            return False
        if self.has_nif:
            return bool(self.nif_number)
        return bool(
            self.birth_date and self.place_of_birth and self.nationality
            and self.country_of_residence and self.id_type and self.id_number and self.issued_by
        )


class DepositBankDetails(models.Model):
    """Guest-supplied bank account details for the security-deposit refund - the cash deposit is
    taken at check-in and returned by bank transfer, so this is where that account information
    comes from. One per Booking, captured guest-facing via the Manage Booking hub's Security
    Deposit section (bookings/views.py::BookingManageDepositView, added 2026-08-29 per Thomas's
    reference screenshot of the legacy klt-management-software 'Account details' popup). Only
    reachable for a booking that actually has a deposit owed (Charge.security, see its own
    docstring above) - see that section's own docstring. get_or_create'd the first time the
    guest visits, same lazy-creation pattern as GuestRegistration above; every field starts blank
    rather than defaulting to anything guessed, since a wrong bank detail is worse than an
    obviously-incomplete one. No validation beyond max_length - deliberately accepts whatever
    format the guest's own bank uses (IBAN-only for most EU accounts, sort code + account number
    for UK, SWIFT/bank address for anything needing an international wire), same reasoning as
    Departure.details being a free-text note rather than a structured field."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='deposit_bank_details')
    bank_name = models.CharField(max_length=200, blank=True)
    account_name = models.CharField(max_length=200, blank=True)
    account_number = models.CharField(max_length=100, blank=True)
    sort_code = models.CharField(max_length=50, blank=True)
    iban = models.CharField(max_length=50, blank=True)
    swift_code = models.CharField(max_length=50, blank=True)
    bank_address = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'booking_deposit_bank_details'
        verbose_name = 'Deposit Bank Details'
        verbose_name_plural = 'Deposit Bank Details'

    def __str__(self):
        return f"{self.booking} - Deposit bank details"

    def is_blank(self):
        return not any([
            self.bank_name, self.account_name, self.account_number,
            self.sort_code, self.iban, self.swift_code, self.bank_address,
        ])


class BookingCondition(models.Model):
    """A single bullet point shown on the public Booking Conditions page. Order is admin-editable."""
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'booking_conditions'
        verbose_name = 'Booking Condition'
        verbose_name_plural = 'Booking Conditions'
        ordering = ('order',)

    def __str__(self):
        return self.text[:80]


class FAQ(models.Model):
    """A single question/answer pair shown on the Manage Booking hub's FAQ page - same
    order-editable, staff-authored plain-text pattern as BookingCondition above, just split into
    two fields instead of one. location=None ("All") is the default and covers most questions
    (payment, cancellation, check-in/out) equally everywhere; set it to answer something that
    genuinely varies by location (e.g. "Is there parking?", which is usually a building/
    condominium-level fact shared by every apartment at that location, not an individual
    property's own) differently for that one location without duplicating every universal FAQ
    alongside it."""
    question = models.CharField(max_length=300)
    answer = models.TextField()
    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, null=True, blank=True, related_name='faqs',
        help_text="Leave blank to show this on every location's FAQ page.",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'faqs'
        verbose_name = 'FAQ'
        verbose_name_plural = 'FAQs'
        ordering = ('order',)

    def __str__(self):
        return self.question


class TravelMethod(models.TextChoices):
    """Same stored values for Arrival and Departure - only the label wording differs by
    direction (departure_choices() below), since "Flight to Faro" reads backwards for a guest
    leaving via Faro. Never branch on direction anywhere except display label."""
    FLIGHT_FARO = 'flight_faro', 'Flight to Faro'
    FLIGHT_LISBON = 'flight_lisbon', 'Flight to Lisbon'
    BUS = 'bus', 'Bus to Albufeira'
    TRAIN = 'train', 'Train to Ferreiras (Albufeira)'
    DRIVING = 'driving', 'Driving from another location'
    OTHER = 'other', 'Other'

    @classmethod
    def departure_choices(cls):
        labels = {
            cls.FLIGHT_FARO: 'Flight from Faro',
            cls.FLIGHT_LISBON: 'Flight from Lisbon',
            cls.BUS: 'Bus from Albufeira',
            cls.TRAIN: 'Train from Ferreiras (Albufeira)',
            cls.DRIVING: 'Driving to another location',
            cls.OTHER: 'Other',
        }
        return [(value, labels[value]) for value, _ in cls.choices]


class Arrival(models.Model):
    """Guest-facing arrival information, self-serve via the Manage Booking hub (see
    bookings/views.py::BookingManageArrivalDepartureView) - editable any time once the deposit is
    paid, no cutoff. method drives which of flight_number/time/travelling_from/hiring_car are
    relevant - travelling_from is only meaningful for DRIVING, hiring_car only for the two flight
    methods. self_check_in/meet_greet are staff/ops-only - the guest-facing save path never
    touches them (only supplies creation defaults via get_or_create so the row can exist before
    staff have set anything); they're edited from the staff booking detail page's Booking Info
    panel instead (see StaffBookingDetailView._update_booking()), same pattern as Departure.clean
    below."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='arrival')
    method = models.CharField(max_length=20, choices=TravelMethod.choices, default=TravelMethod.FLIGHT_FARO)
    flight_number = models.CharField(max_length=50, blank=True, null=True)
    travelling_from = models.CharField(max_length=200, blank=True)
    hiring_car = models.BooleanField(default=False)
    time = models.TimeField(blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    self_check_in = models.BooleanField(blank=True, null=True)
    # Defaults to True (2026-08-29, per Thomas), same reasoning and same "normal case, not an
    # opt-in extra" wording as Departure.clean below: a meet & greet happens for every non-owner
    # booking unless staff say otherwise. Only meaningful to toggle off for an owner booking (see
    # the Owner-booking-conditional row on the staff booking detail page) - a non-owner booking's
    # row stays hidden/disabled there, so this default is effectively permanent for guests.
    meet_greet = models.BooleanField(default=True)

    class Meta:
        db_table = 'booking_arrivals'
        verbose_name = 'Arrival'
        verbose_name_plural = 'Arrivals'

    def __str__(self):
        return f"{self.booking} - Arrival {self.booking.arrival_date}"


class Departure(models.Model):
    """Guest-facing departure information, same self-serve role as Arrival above (see method's
    docstring there - travelling_from is reused here as "travelling to", same field different
    context). clean is a staff/ops-only flag (turnover-cleaning scheduling) - the guest-facing
    save path never touches it. Defaults to True (2026-08-27, per Thomas: an end-of-stay clean is
    the normal case for every booking, not an opt-in extra) and this row is now created eagerly at
    booking-creation time (bookings.utils.create_booking()) precisely so that default takes effect
    without staff needing to visit this booking at all. Still staff-editable for the rare booking
    that shouldn't get one (e.g. an owner cleaning it themselves) - from the "Clean on departure"
    checkbox alongside Meet & Greet in the Owner-booking-conditional row on the staff booking
    detail page's Booking Info panel (StaffBookingDetailView._update_booking()), same place it's
    always lived. The Cleaning panel elsewhere on that page just displays the resulting
    CleaningTask - it's not itself where this flag gets toggled."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='departure')
    method = models.CharField(max_length=20, choices=TravelMethod.choices, default=TravelMethod.FLIGHT_FARO)
    flight_number = models.CharField(max_length=50, blank=True, null=True)
    travelling_from = models.CharField(max_length=200, blank=True)
    time = models.TimeField(blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    clean = models.BooleanField(default=True)

    class Meta:
        db_table = 'booking_departures'
        verbose_name = 'Departure'
        verbose_name_plural = 'Departures'

    def __str__(self):
        return f"{self.booking} - Departure {self.booking.departure_date}"


class Charge(models.Model):
    """Booking charges and payments."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='charges')
    
    # Payment methods
    bank_transfer = models.BooleanField(blank=True, null=True)
    credit_card = models.BooleanField(blank=True, null=True)
    currency = models.CharField(max_length=3, blank=True, null=True, choices=CURRENCY_CHOICES)
    
    # Charge amounts. basic_rental is the clean, pre-discount/pre-extra-guest nightly-rate total
    # (see properties/utils.py::get_stay_total_price()) - discount_total/extra_guest_total are the
    # amounts that get combined with it into what's actually charged, see total_rental() below.
    # NULL discount_total/extra_guest_total (any Charge created before these fields existed) means
    # "unknown", not "zero" - but total_rental() treats it as zero anyway, which is exactly correct
    # for that historical data: basic_rental on those older rows already holds the final,
    # already-adjusted total, so basic_rental - 0 + 0 reproduces it exactly.
    basic_rental = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    discount_total = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    extra_guest_total = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    admin = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    # The actual source of truth for whether/how much cash security deposit is owed at check-in -
    # not just a rental-charges line item (2026-09-02, per Thomas). Set once, waiver-aware, at
    # create_booking() time (bookings/utils.py, via compute_deposit_waiver() - owner booking,
    # returning guest, no-deposit platform, or non-UK/EU guest all zero it automatically at that
    # point) using whatever the guest's own submitted details say at that moment. Deliberately
    # NEVER re-derived after that - a party-size change (bookings/views.py's recalculate_costs_for_
    # party/recalculate_balance_for_party call sites) explicitly leaves it alone, and nothing else
    # recomputes it live. From creation onward, editing this field directly on the staff Booking
    # page (including down to 0 to waive it) IS how staff make any needed adjustment - e.g. a
    # genuinely returning guest who books under a different email, so has_completed_previous_
    # stay() can't (and shouldn't try to) catch her automatically. Every consumer - the check-ins
    # popup, the guest-facing confirmation page, and the manage-booking sidebar's deposit gate -
    # reads this field directly rather than recomputing anything.
    security = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    security_method = models.CharField(max_length=100, blank=True, null=True)

    # Legacy, imported-only - the old system used this one table for every booking's money
    # regardless of source. Charge is now scoped to the online direct-booking flow only; a platform
    # booking's commission/payout lives on PlatformPayout instead, and extra-nights charges live on
    # BookingDateAdjustment (see [[project_klt_web_platform_payout]] / [[project_klt_web_extras_feature]]
    # in memory) - neither of these two fields is written by any current code path.
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    extra_nights = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    manual_charges = models.BooleanField(blank=True, null=True)

    # Locked-in payment split at booking time - see BookingSettings.compute_costs(). Kept fixed
    # even if BookingSettings' percentages/timing change later, so past bookings don't retroactively change.
    due_at_booking = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    due_at_balance = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    balance_due_date = models.DateField(blank=True, null=True)

    # Snapshot of BookingSettings.gbp_conversion_rate at booking time. A guest who saw/paid a GBP
    # quote at deposit time must see the same GBP total at balance time, even if the live rate has
    # since changed - so GBP display for an existing booking always uses this frozen rate, never
    # BookingSettings.load().gbp_conversion_rate directly.
    gbp_conversion_rate = models.DecimalField(max_digits=6, decimal_places=4, blank=True, null=True)

    @property
    def total_rental(self):
        """basic_rental adjusted by discount/extra-guest - the actual rental amount charged, as
        opposed to basic_rental alone (the clean pre-adjustment figure). Every consumer that needs
        "the final rental total" (owner payout commission base, guest-facing confirmation total,
        the extra-guest add-on charge calc) should read this, not basic_rental directly."""
        if self.basic_rental is None:
            return None
        return self.basic_rental - (self.discount_total or Decimal('0')) + (self.extra_guest_total or Decimal('0'))

    def to_gbp(self, amount):
        if self.gbp_conversion_rate is None or amount is None:
            return None
        return (Decimal(amount) * self.gbp_conversion_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    def due_at_booking_in_charge_currency(self):
        """(amount, currency) for the deposit in whatever currency the guest was quoted at booking
        time - GBP via the frozen rate, or EUR (the underlying base currency) otherwise. This is
        what the guest actually pays via Revolut/Wise, not just what's shown on a display toggle -
        see bookings/views.py::BookingPaymentView."""
        if self.currency == 'GBP':
            return self.to_gbp(self.due_at_booking), 'GBP'
        return self.due_at_booking, 'EUR'

    def due_at_balance_in_charge_currency(self):
        """Same as due_at_booking_in_charge_currency() but for the balance stage - same frozen
        currency/rate, since a guest who was quoted GBP at deposit time should keep seeing GBP at
        balance time even if the live rate has moved on. See bookings/views.py::BookingBalancePaymentView."""
        if self.currency == 'GBP':
            return self.to_gbp(self.due_at_balance), 'GBP'
        return self.due_at_balance, 'EUR'

    def costs_in_gbp(self):
        """GBP-converted view of the locked EUR charge amounts, using the rate frozen at booking time."""
        if self.gbp_conversion_rate is None:
            return None
        return {
            'basic_rental': self.to_gbp(self.total_rental),
            'admin_fee': self.to_gbp(self.admin),
            'subtotal': self.to_gbp(self.total_rental + self.admin),
            'due_at_booking': self.to_gbp(self.due_at_booking),
            'due_at_balance': self.to_gbp(self.due_at_balance),
        }

    class Meta:
        db_table = 'booking_charges'
        verbose_name = 'Charge'
        verbose_name_plural = 'Charges'

    def __str__(self):
        return f"{self.booking} - Charges"


class PlatformPayout(models.Model):
    """What a platform (Airbnb/Booking.com/Vrbo - see env_settings.PLATFORMS) actually paid out for
    a booking, as its own distinct relationship from Charge, which is scoped to the online
    direct-booking flow (Revolut/Wise deposit/balance split - see bookings/utils.py::create_booking()).
    A platform booking never has a Charge/Payment split in that sense - the guest pays the platform
    directly, not us, so this captures gross/commission/payout instead of overloading Charge's
    fields for two structurally different kinds of money (see [[project_klt_web_platform_payout]]
    in memory for the full reasoning)."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='platform_payout')
    gross_amount = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="Total the guest paid the platform."
    )
    platform_commission = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="What the platform kept as its fee/commission."
    )
    payout_amount = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="Net amount actually paid out to us."
    )
    payout_currency = models.CharField(max_length=3, blank=True, null=True, choices=CURRENCY_CHOICES)
    payout_date = models.DateField(blank=True, null=True, help_text="When the platform's payout was received, if known.")

    class Meta:
        db_table = 'booking_platform_payouts'
        verbose_name = 'Platform Payout'
        verbose_name_plural = 'Platform Payouts'

    def __str__(self):
        return f"{self.booking} - Platform Payout"


PROVIDER_CHOICES = (
    ('revolut', 'Revolut'),
    ('wise', 'Wise'),
)

PAYMENT_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('in_progress', 'In progress'),
    ('paid', 'Paid'),
    ('declined', 'Declined'),
    ('failed', 'Failed'),
    ('cancelled', 'Cancelled'),
)


class Payment(models.Model):
    """Deposit-payment tracking for a booking's due_at_booking amount. Kept separate from Charge,
    which is an immutable pricing snapshot - this is the opposite, mutated repeatedly by webhook
    events from klt-hooks (see bookings/models.py::Booking.hold_expires_at for how that ties in).
    """
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='payment')
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    status = models.CharField(max_length=15, choices=PAYMENT_STATUS_CHOICES, default='pending')

    # Revolut-specific - unused for provider='wise' rows, which have no per-booking API object.
    revolut_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    revolut_checkout_url = models.URLField(blank=True, null=True)

    last_event_type = models.CharField(max_length=100, blank=True, null=True)
    in_progress_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'booking_payments'
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'

    def __str__(self):
        return f"{self.booking} - Payment ({self.get_status_display()})"


class BalancePayment(models.Model):
    """Balance-payment tracking for a booking's due_at_balance amount - the second, later payment
    stage for a booking that wasn't made within BookingSettings.balance_due_days_before_arrival of
    arrival (see BookingSettings.compute_costs()'s collapse behaviour for the alternative). Mirrors
    Payment field-for-field since it's the same shape of problem one booking-lifecycle stage later,
    but is a distinct model (not a second row shape shoehorned into Payment) since Payment's own
    docstring already scopes it specifically to the deposit.

    A row only exists for a booking that actually has a balance stage - created in
    bookings/utils.py::create_booking() only when due_at_balance > 0, so hasattr(booking,
    'balance_payment') doubles as the "is this a two-stage booking" signal used throughout the
    guest-facing balance flow, without needing a separate flag anywhere."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='balance_payment')
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    status = models.CharField(max_length=15, choices=PAYMENT_STATUS_CHOICES, default='pending')

    # Revolut-specific - unused for provider='wise' rows, which have no per-booking API object.
    revolut_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    revolut_checkout_url = models.URLField(blank=True, null=True)

    last_event_type = models.CharField(max_length=100, blank=True, null=True)
    in_progress_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # No automated reminder email yet (see project_klt_web_automation_roadmap in memory) - staff
    # send the guest a link manually, then mark it here via the admin action, so the "reminder due"
    # admin filter stops surfacing this booking.
    reminder_sent = models.BooleanField(default=False)
    reminder_sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'booking_balance_payments'
        verbose_name = 'Balance Payment'
        verbose_name_plural = 'Balance Payments'

    def __str__(self):
        return f"{self.booking} - Balance Payment ({self.get_status_display()})"


class TouristTax(models.Model):
    """Municipal tourist tax owed on a booking - see BookingSettings.tourist_tax_per_night/
    tourist_tax_min_age/tourist_tax_max_nights and bookings/utils.py::compute_tourist_tax().
    Mirrors Payment/BalancePayment's shape (a mutable row updated by webhook events) but always
    provider='revolut' - unlike the deposit/balance stages, this has no seasonal Wise alternative
    in the legacy pattern it's ported from. total is nullable until first computed (lazily, on the
    guest's first visit to the Manage hub's Tourist Tax section) and gets recomputed/re-frozen on
    each visit until paid, since it depends on the party's real ages which can change up until
    then - see BookingManageTouristTaxView."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='tourist_tax')
    total = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES, default='revolut')
    status = models.CharField(max_length=15, choices=PAYMENT_STATUS_CHOICES, default='pending')

    revolut_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    revolut_checkout_url = models.URLField(blank=True, null=True)

    last_event_type = models.CharField(max_length=100, blank=True, null=True)
    in_progress_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'booking_tourist_tax'
        verbose_name = 'Tourist Tax'
        verbose_name_plural = 'Tourist Tax'

    def __str__(self):
        return f"{self.booking} - Tourist Tax ({self.get_status_display()})"


class WelcomePackFoodChoice(models.TextChoices):
    STANDARD = 'standard', 'Standard'
    VEGAN = 'vegan', 'Vegan'


class WelcomePackDrinksChoice(models.TextChoices):
    ALCOHOLIC = 'alcoholic', 'Alcoholic'
    NON_ALCOHOLIC = 'non_alcoholic', 'Non-alcoholic'


class Extra(models.Model):
    """Booking extras and additional services."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='extras')

    # Baby/child items
    cot = models.BooleanField(blank=True, null=True)
    high_chair = models.BooleanField(blank=True, null=True)
    cot_high_chair_charge = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text="Combined price for whichever of cot/high chair were requested, computed from "
                  "ExtrasSettings at request time (day-tier pricing plus the combo discount if both "
                  "were requested) - see ExtrasSettings.compute_cot_high_chair_price().",
    )

    # Services
    welcome_pack = models.BooleanField(blank=True, null=True)
    welcome_pack_food = models.CharField(max_length=20, choices=WelcomePackFoodChoice.choices, blank=True, null=True)
    welcome_pack_drinks = models.CharField(max_length=20, choices=WelcomePackDrinksChoice.choices, blank=True, null=True)
    welcome_pack_note = models.TextField(blank=True, null=True,
                                          help_text="Allergies or dietary notes only - the pack's contents are "
                                                     "fixed by the food/drinks choice, not open to swap requests.")
    welcome_pack_charge = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text="Snapshotted from ExtrasSettings.welcome_pack_price at request time (same "
                  "convention as every other Extras price field) - staff can hand-edit here "
                  "afterward for a rare exception outside the standard fixed pack choices.",
    )
    mid_stay_clean = models.BooleanField(blank=True, null=True)
    mid_stay_clean_date = models.DateField(
        blank=True, null=True,
        help_text="When the mid-stay clean happens - not guest-editable (auto-set to the middle "
                  "of the stay by BookingFormMixin._mid_stay_clean_default_date() whenever the "
                  "guest ticks the Extras-section checkbox, same cash-at-check-in convention as "
                  "every other Extra), though staff can still hand-set it from the booking detail "
                  "page's Booking Info panel. Feeds staff.utils.sync_cleaning_tasks_for_booking() "
                  "to create a CleaningTask - a further nudge within the stay is then a staff-side "
                  "drag on the cleaning calendar, not a guest choice.",
    )
    mid_stay_clean_charge = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text="Snapshotted from ExtrasSettings.compute_mid_stay_clean_price() at request time "
                  "(same convention as every other Extras price field) - priced by the property's "
                  "bedroom count, not stay length.",
    )
    late_checkout = models.BooleanField(blank=True, null=True)
    late_checkout_time = models.TimeField(
        blank=True, null=True,
        help_text="Guest's exact requested checkout time. A future cleaning-automation system may "
                  "add a 'latest possible' boundary this has to respect - not yet built.",
    )
    late_checkout_charge = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text="Flat fee snapshotted from ExtrasSettings.late_checkout_price at request time.",
    )
    extra_nights = models.BooleanField(blank=True, null=True)

    # Payment
    owner_is_paying = models.BooleanField(blank=True, null=True)

    class Meta:
        db_table = 'booking_extras'
        verbose_name = 'Extra'
        verbose_name_plural = 'Extras'

    def clean(self):
        super().clean()
        if self.mid_stay_clean_date and self.booking_id:
            if not (self.booking.arrival_date <= self.mid_stay_clean_date <= self.booking.departure_date):
                message = (
                    f"Mid-stay clean date must fall within the stay ({self.booking.arrival_date} "
                    f"to {self.booking.departure_date})."
                )
                raise ValidationError({'mid_stay_clean_date': message})

    def __str__(self):
        return f"{self.booking} - Extras"


class RequestType(models.Model):
    """Admin-managed catalog of extra items/services guests can request (e.g. extra bed, extra
    pillows) - replaces the old freeform Extra.other_requests text field."""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    default_price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = 'booking_request_types'
        verbose_name = 'Request type'
        ordering = ('name',)

    def __str__(self):
        return self.name


class BookingRequestedExtra(models.Model):
    """A guest's request for a RequestType catalog item on a specific booking. price_at_request is
    snapshotted from RequestType.default_price at creation time so a later catalog price change
    doesn't retroactively reprice an already-requested item (same convention as Charge/PlatformPayout)."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='requested_extras')
    request_type = models.ForeignKey(RequestType, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    note = models.CharField(max_length=200, blank=True)
    price_at_request = models.DecimalField(max_digits=8, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'booking_requested_extras'
        ordering = ('created_at',)

    def __str__(self):
        return f"{self.booking} - {self.request_type} x{self.quantity}"


class WelcomePackItem(models.Model):
    """Admin-managed item list shown to guests for the Welcome Pack, tagged by which fixed food/
    drinks choice(s) it belongs to - the *_COMMON categories show regardless of which side of that
    axis the guest picked (e.g. water is fine for both alcoholic and non-alcoholic)."""
    class Category(models.TextChoices):
        FOOD_STANDARD = 'food_standard', 'Food - standard only'
        FOOD_VEGAN = 'food_vegan', 'Food - vegan only'
        FOOD_COMMON = 'food_common', 'Food - both standard and vegan'
        DRINKS_ALCOHOLIC = 'drinks_alcoholic', 'Drinks - alcoholic only'
        DRINKS_NON_ALCOHOLIC = 'drinks_non_alcoholic', 'Drinks - non-alcoholic only'
        DRINKS_COMMON = 'drinks_common', 'Drinks - both alcoholic and non-alcoholic'

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.FOOD_COMMON)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = 'welcome_pack_items'
        verbose_name = 'Welcome pack item'
        ordering = ('category', 'name')

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

    def matches(self, food_choice, drinks_choice):
        """Whether this item should show for a guest who picked the given food/drinks variant."""
        if self.category in (self.Category.FOOD_STANDARD, self.Category.FOOD_VEGAN):
            return self.category == f'food_{food_choice}'
        if self.category in (self.Category.DRINKS_ALCOHOLIC, self.Category.DRINKS_NON_ALCOHOLIC):
            return self.category == f'drinks_{drinks_choice}'
        return True


class ExtrasSettings(models.Model):
    """Singleton admin settings for Extras pricing that doesn't fit BookingSettings - kept separate
    because these pricing shapes (guest-count bands, a night-surcharge window) are a genuinely
    different concern from BookingSettings' flat percentages/day-counts (see the plan this was
    built from). Same singleton pattern as BookingSettings.load()."""
    airport_transfer_price_1_4_guests = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Flat price for a transfer carrying 1 to 4 guests total (adults + children + infants).",
    )
    airport_transfer_price_5_8_guests = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Flat price for a transfer carrying 5 to 8 guests total. More than 8 guests "
                  "needs a separate transfer booked at one of these two prices, rather than a "
                  "third tier - in practice there's never been a need for one.",
    )
    airport_transfer_night_surcharge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    airport_transfer_night_window_start = models.TimeField(default=time(22, 0))
    airport_transfer_night_window_end = models.TimeField(default=time(6, 0))

    cot_price_short_stay = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                                help_text="Flat price for a stay of up to 7 nights.")
    cot_price_long_stay = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                               help_text="Flat price for a stay of more than 7 nights.")
    high_chair_price_short_stay = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                                       help_text="Flat price for a stay of up to 7 nights.")
    high_chair_price_long_stay = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                                      help_text="Flat price for a stay of more than 7 nights.")
    cot_and_high_chair_combo_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Percentage discount applied to the combined price when both a cot and a high "
                  "chair are requested together on the same booking.",
    )

    welcome_pack_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Flat price for the standard Welcome Pack (the fixed food/drinks picker). Staff "
                  "can hand-edit Extra.welcome_pack_charge afterward for a rare exception.",
    )

    late_checkout_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Flat fee for a late checkout request. A future cleaning-automation system may "
                  "vary this by how late the checkout is - kept as a single flat fee for now.",
    )

    mid_stay_clean_price_one_bedroom = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Flat price for a mid-stay clean on a 1-bedroom property. Same one-bedroom/"
                  "multi-bedroom split as PaymentSettings.cleaning_surcharge_one_bedroom - a "
                  "different, guest-facing fee though, not the owner-side cleaning surcharge.",
    )
    mid_stay_clean_price_multi_bedroom = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Flat price for a mid-stay clean on a property with more than 1 bedroom.",
    )
    mid_stay_clean_minimum_nights = models.IntegerField(
        default=2, validators=[MinValueValidator(2)],
        help_text="Mid-stay clean isn't offered on a shorter stay than this. 2 is the structural "
                  "floor - a 1-night stay has no day strictly between arrival and departure at "
                  "all (see bookings/views.py::BookingFormMixin._extras_context's "
                  "show_mid_stay_clean and _parse_mid_stay_clean, which both enforce this, not "
                  "just the display gate).",
    )

    class Meta:
        db_table = 'extras_settings'
        verbose_name = 'Extras Settings'
        verbose_name_plural = 'Extras Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def is_night_time(self, pickup_time):
        """Whether a pickup/dropoff time falls in the night-surcharge window. The window can wrap
        past midnight (default 22:00-06:00), so this isn't a plain start <= t <= end check."""
        start, end = self.airport_transfer_night_window_start, self.airport_transfer_night_window_end
        if start <= end:
            return start <= pickup_time <= end
        return pickup_time >= start or pickup_time <= end

    def compute_transfer_price(self, total_guests, pickup_time):
        """Two fixed tiers only (1-4 guests, 5-8 guests) - a single transfer vehicle's real
        capacity, not an arbitrary pricing choice. Returns None above 8 guests; the caller is
        responsible for deciding what that means (in practice, booking a second transfer)."""
        if total_guests <= 4:
            price = self.airport_transfer_price_1_4_guests
        elif total_guests <= 8:
            price = self.airport_transfer_price_5_8_guests
        else:
            return None
        if self.is_night_time(pickup_time):
            price += self.airport_transfer_night_surcharge
        return price

    def compute_cot_high_chair_price(self, nights, wants_cot, wants_high_chair):
        """nights is the length of the stay (departure_date - arrival_date), not a per-request
        value - cot/high chair pricing covers the whole stay, so a 7-night stay is "short" and an
        8-night stay is "long" (see cot_price_short_stay/cot_price_long_stay). The combo discount
        is a percentage of the combined price, only applied when both are requested on the same
        booking; clamped at 0 so a discount over 100% can never make this negative."""
        is_long_stay = nights > 7
        total = Decimal('0')
        if wants_cot:
            total += self.cot_price_long_stay if is_long_stay else self.cot_price_short_stay
        if wants_high_chair:
            total += self.high_chair_price_long_stay if is_long_stay else self.high_chair_price_short_stay
        if wants_cot and wants_high_chair:
            total -= total * (self.cot_and_high_chair_combo_discount_percent / Decimal('100'))
        return max(total, Decimal('0'))

    def compute_mid_stay_clean_price(self, property):
        """Priced by bedroom count, not stay length - the one new pricing axis Thomas asked for
        (2026-08-26), mirroring PaymentSettings' existing one-bedroom/multi-bedroom cleaning-fee
        split. PropertySpec isn't guaranteed to exist for every Property (confirmed elsewhere in
        this codebase) - falls back to 1 bedroom, matching PropertySpec.bedrooms' own model
        default, so a property with no spec row prices the same as an explicit 1-bedroom one."""
        specs = getattr(property, 'specs', None)
        bedrooms = specs.bedrooms if specs else 1
        return self.mid_stay_clean_price_one_bedroom if bedrooms == 1 else self.mid_stay_clean_price_multi_bedroom


class CheckinSettings(models.Model):
    """Singleton settings for the staff check-ins calendar (staff/utils.py::compute_arrival_eta,
    staff/models.py::Checkin) - same singleton pattern as BookingSettings/ExtrasSettings, kept
    separate since travel-time buffers and self-check-in policy timing are a genuinely different
    concern from either. Global, not per-property/per-company - travel logistics from Faro/Lisbon
    don't vary by which management company a property happens to be under.

    faro_buffer_minutes/lisbon_buffer_minutes/transit_buffer_minutes are added to the guest's own
    Arrival.time (see compute_arrival_eta's docstring for exactly what that field means per
    method) to get the calendar-displayed arrival time - driving is deliberately never buffered,
    Arrival.time already is the at-property estimate for that method.

    key_box_prep_time/welcome_visit_time are the two fixed, policy-driven times used for a self-
    check-in booking's auto-generated Checkin rows - not computed from the guest's arrival time at
    all, by design (mirrors the exact fixed-10:00 approach already used manually today)."""
    faro_buffer_minutes = models.PositiveSmallIntegerField(default=90)
    lisbon_buffer_minutes = models.PositiveSmallIntegerField(default=270)
    transit_buffer_minutes = models.PositiveSmallIntegerField(default=30)
    key_box_prep_time = models.TimeField(default=time(10, 0))
    welcome_visit_time = models.TimeField(default=time(10, 0))

    class Meta:
        db_table = 'checkin_settings'
        verbose_name = 'Check-in Settings'
        verbose_name_plural = 'Check-in Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PaymentSettings(models.Model):
    """Site-wide default owner-payout/commission figures - same singleton pattern as
    BookingSettings/ExtrasSettings. Owner payment is rental minus commission minus VAT on that
    commission minus VAT on the platform's own fee (see bookings/payouts.py::compute_owner_payout(),
    reverse-engineered against a real legacy Bookings Report export rather than assumed from the
    legacy management-software's determine_commission()/determine_owner_payment() alone), not a
    flat "owner payout %". PlatformPayout's amounts are still entered per-booking by hand -
    deliberately simplified from the legacy system's full complexity (see field help text for
    what got dropped)."""
    high_season_commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('15.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Default management commission on rental income during high season. The legacy "
                  "system also varied this by an owner-level 'wants accounting' flag and a VAT-"
                  "regime cutoff date - deliberately simplified to season only for now."
    )
    low_season_commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('10.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Default management commission on rental income outside high season."
    )
    high_season_start_month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES, default=4,
        help_text="First month of high season (inclusive)."
    )
    high_season_end_month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES, default=10,
        help_text="Last month of high season (inclusive)."
    )
    klt_commission_share_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('100.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Share of the commission above kept by KLT itself, with the remainder going to "
                  "the other party in the historical split arrangement (the legacy system phases "
                  "this to 100% for any booking arriving after 2026 - kept configurable in case "
                  "that arrangement changes again)."
    )
    vat_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('23.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text="Portuguese VAT/IVA rate applied to invoiced commission and platform fees."
    )
    cleaning_surcharge_one_bedroom = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('10.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Added on top of a property's own standard cleaning fee for a 1-bedroom property."
    )
    cleaning_surcharge_multi_bedroom = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('15.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Added on top of a property's own standard cleaning fee for a property with more than 1 bedroom."
    )
    cleaning_high_occupancy_surcharge = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('15.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Added on top of the cleaning fee when a stay's guest count exceeds 2 per "
                  "bedroom (the legacy system's trigger rule - not yet automatically applied here)."
    )
    meet_greet_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('28.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Flat fee for a meet & greet (see Owner.default_meet_greet for whether one applies by default)."
    )
    extra_bed_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('25.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Flat fee for an extra bed. In the legacy system this only ever triggered for "
                  "one specific property - stored here as a general default, not yet tied to any "
                  "automatic per-property trigger."
    )
    regular_payout_days_after_arrival = models.PositiveIntegerField(
        default=3,
        help_text="For an owner paid on a regular schedule (Owner.is_paid_regularly), how many "
                  "days after guest arrival their payout is due. An owner not on a regular "
                  "schedule is instead batched into a single payout at the end of the arrival "
                  "month."
    )
    charge_vat_on_low_season_direct_commission = models.BooleanField(
        default=False,
        help_text="Whether Internal Commission carries VAT for a direct booking outside high "
                  "season. High-season commission always carries VAT regardless of origin - "
                  "this only controls the low-season direct case, which can be disregarded for "
                  "simplicity."
    )
    charge_vat_on_low_season_platform_commission = models.BooleanField(
        default=True,
        help_text="Whether Internal Commission carries VAT for a platform booking outside high "
                  "season. High-season commission always carries VAT regardless of origin, and "
                  "a platform booking already carries VAT on the platform's own fee year-round - "
                  "defaults on for consistency, but kept togglable separately from the direct "
                  "case."
    )

    class Meta:
        db_table = 'payment_settings'
        verbose_name = 'Payment Settings'
        verbose_name_plural = 'Payment Settings'

    def __str__(self):
        return "Payment Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class AirportTransferDirection(models.TextChoices):
    INBOUND = 'inbound', 'Inbound (arrival)'
    OUTBOUND = 'outbound', 'Outbound (departure)'


class AirportTransfer(models.Model):
    """One airport transfer request on a booking - many rows per Booking (unlike Extra), since a
    stay can need more than one (inbound + outbound at minimum, occasionally more). Deliberately no
    formal cross-booking linkage for shared rides - see the plan this was built from for the
    privacy/security reasoning. adults/children/infants mirror the search-page guest picker's
    shape rather than BookingGuest's named/aged list, since a shared ride's headcount can exceed
    the requesting guest's own party. The date is implied by direction (Booking.arrival_date for
    inbound, Booking.departure_date for outbound) rather than stored separately."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='airport_transfers')
    direction = models.CharField(max_length=10, choices=AirportTransferDirection.choices)
    is_faro = models.BooleanField(default=True, help_text="Uncheck if flying via an airport other than Faro.")
    flight_number = models.CharField(max_length=20, blank=True)
    time = models.TimeField()
    adults = models.PositiveIntegerField(default=1)
    children = models.PositiveIntegerField(default=0)
    infants = models.PositiveIntegerField(default=0)
    child_seats = models.CharField(max_length=200, blank=True)
    excess_baggage = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    price_at_request = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'airport_transfers'
        verbose_name = 'Airport transfer'
        ordering = ('created_at',)

    def __str__(self):
        return f"{self.booking} - {self.get_direction_display()} ({self.time})"

    @property
    def total_guests(self):
        return self.adults + self.children + self.infants

    @property
    def date(self):
        return self.booking.arrival_date if self.direction == AirportTransferDirection.INBOUND else self.booking.departure_date


class BookingDateAdjustment(models.Model):
    """Audit log of an extra-nights date change (platform-derived bookings only) - the guest
    agrees direct with Thomas to extend their stay, off-platform to avoid commission, and pays the
    cash difference on arrival. Each row is a delta from whatever Booking.arrival_date/
    departure_date were immediately before it - not a shadow "real dates" table, since every date
    consumer (calendar, BookingQuerySet.overlapping(), admin) reads those two fields as sole source
    of truth (see the plan this was built from for why a parallel table was rejected). Supports
    multiple chained extensions over time, each its own row. additional_charge is deliberately not
    wired into Charge - see PlatformPayout for why platform-derived bookings have their own money
    model, separate from the online direct-booking Charge/Payment flow.

    previous_arrival_date/previous_departure_date are snapshotted automatically in save() from the
    booking's current dates - never staff-entered - so a new row can't accidentally record the
    wrong "before" state. Creating a new row atomically does all three things a staff member could
    otherwise partially forget: snapshots the previous dates onto this row, updates the live
    Booking dates, and sets Booking.manual_override so the external scraper stops touching them.
    Editing an existing row afterwards is just a correction to the log - it does not re-trigger
    that cascade, since only a new adjustment represents a new date change."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='date_adjustments')
    previous_arrival_date = models.DateField(editable=False)
    previous_departure_date = models.DateField(editable=False)
    new_arrival_date = models.DateField()
    new_departure_date = models.DateField()
    additional_charge = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                             help_text="Cash difference the guest pays on arrival - not part of Charge.")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'booking_date_adjustments'
        verbose_name = 'Booking date adjustment'
        ordering = ('created_at',)

    def __str__(self):
        return f"{self.booking} - {self.previous_arrival_date} → {self.new_arrival_date}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new:
            self.previous_arrival_date = self.booking.arrival_date
            self.previous_departure_date = self.booking.departure_date
        super().save(*args, **kwargs)
        if is_new:
            self.booking.arrival_date = self.new_arrival_date
            self.booking.departure_date = self.new_departure_date
            self.booking.manual_override = True
            self.booking.save(update_fields=['arrival_date', 'departure_date', 'manual_override'])


class GuestListAdjustment(models.Model):
    """Audit log of a guest-list addition made self-serve through the Manage Booking hub after the
    booking is already fully paid (see bookings/views.py::is_fully_paid()) - i.e. after Charge is
    frozen for good. Mirrors BookingDateAdjustment's role as an audit-log row for money that moves
    outside the online Charge/Payment/BalancePayment flow once those are settled, but unlike that
    model this one does not self-apply in save(): adding guests means bulk-creating new
    BookingGuest rows, which doesn't fit a single new_x/previous_x field swap, so the view
    orchestrates the new party rows and this audit row together in one atomic block instead.

    Deliberately increases-only - there is no refund logic anywhere in this codebase, so the
    guest-add flow this logs only ever appends new BookingGuest rows (see
    BookingGuest.added_via_adjustment) and can never edit or delete an existing one.
    additional_charge is the extra rental+admin cost of the added guest(s) - cash at check-in,
    same convention as BookingDateAdjustment.additional_charge, never wired into Charge."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='guest_list_adjustments')
    previous_party_size = models.PositiveIntegerField(editable=False)
    new_party_size = models.PositiveIntegerField(editable=False)
    additional_charge = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Extra rental+admin cost of the added guest(s) - cash at check-in, not part of Charge."
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'booking_guest_list_adjustments'
        verbose_name = 'Guest list adjustment'
        ordering = ('created_at',)

    def __str__(self):
        return f"{self.booking} - +{self.new_party_size - self.previous_party_size} guest(s)"


class Form(models.Model):
    """Booking forms and documentation."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='forms')
    balance_payment = models.CharField(max_length=200, blank=True, null=True)
    arrival_questionnaire = models.CharField(max_length=200, blank=True, null=True)
    guest_registration = models.CharField(max_length=200, blank=True, null=True)
    guest_registration_done = models.BooleanField(blank=True, null=True)
    security_deposit = models.CharField(max_length=200, blank=True, null=True)
    pims_uin = models.CharField(max_length=200, blank=True, null=True)
    pims_oid = models.CharField(max_length=200, blank=True, null=True)

    class Meta:
        db_table = 'booking_forms'
        verbose_name = 'Booking Form'
        verbose_name_plural = 'Booking Forms'

    def __str__(self):
        return f"{self.booking} - Forms"


class Email(models.Model):
    """Booking email tracking."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='emails')
    
    # Guest emails
    balance_payment = models.BooleanField(blank=True, null=True)
    arrival_questionnaire = models.BooleanField(blank=True, null=True)
    security_deposit_request = models.BooleanField(blank=True, null=True)
    arrival_information = models.BooleanField(blank=True, null=True)
    guest_registration_form = models.BooleanField(blank=True, null=True)
    check_in_instructions = models.BooleanField(blank=True, null=True)
    final_days_reminder = models.BooleanField(blank=True, null=True)
    goodbye = models.BooleanField(blank=True, null=True)
    
    # Management emails
    management = models.BooleanField(blank=True, null=True)
    pay_owner = models.BooleanField(blank=True, null=True)
    security_deposit_return = models.BooleanField(blank=True, null=True)
    airport_transfers = models.BooleanField(blank=True, null=True)
    guest_registration_form_to_owner = models.BooleanField(blank=True, null=True)
    
    # Status
    paused = models.BooleanField(blank=True, null=True)

    class Meta:
        db_table = 'booking_emails'
        verbose_name = 'Booking Email'
        verbose_name_plural = 'Booking Emails'

    def __str__(self):
        return f"{self.booking} - Emails"


class Update(models.Model):
    """Booking update tracking."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='updates')
    date = models.DateField()
    details = models.TextField(blank=True, null=True)
    extras = models.TextField(blank=True, null=True)
    email_sent = models.BooleanField()

    class Meta:
        db_table = 'booking_updates'
        verbose_name = 'Booking Update'
        verbose_name_plural = 'Booking Updates'

    def __str__(self):
        return f"{self.booking} - Update {self.date}"