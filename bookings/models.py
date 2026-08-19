from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from bookings.utils import generate_reference_candidate
from env_settings import VALID_BOOKING_STATUSES, PROVISIONAL_BOOKING_STATUSES
from properties.models import Property
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

    # Cost dict keys from compute_costs() that represent a money amount and are shown converted to
    # GBP when a guest toggles the currency display. security_deposit is deliberately excluded: it's
    # cash collected locally in Portugal at check-in and stays in EUR regardless of quote currency.
    GBP_DISPLAY_COST_KEYS = ('basic_rental', 'admin_fee', 'subtotal', 'due_at_booking', 'due_at_balance')

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

    def compute_costs(self, basic_rental, arrival_date=None):
        """Cost breakdown for a stay's basic rental total: admin fee, booking/balance split, balance due date.

        The security deposit is reported separately since it's collected in cash at check-in,
        not part of the online rental/admin payment split.
        """
        basic_rental = Decimal(basic_rental)
        admin_fee = (basic_rental * self.admin_fee_percent / Decimal('100')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        subtotal = basic_rental + admin_fee
        due_at_booking = (subtotal * self.deposit_percent_at_booking / Decimal('100')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        due_at_balance = subtotal - due_at_booking
        balance_due_date = arrival_date - timedelta(days=self.balance_due_days_before_arrival) if arrival_date else None
        return {
            'basic_rental': basic_rental,
            'admin_fee': admin_fee,
            'subtotal': subtotal,
            'due_at_booking': due_at_booking,
            'due_at_balance': due_at_balance,
            'balance_due_date': balance_due_date,
            'security_deposit': self.security_deposit_amount,
        }

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


class Booking(models.Model):
    """Main booking model."""
    property = models.ForeignKey(Property, on_delete=models.PROTECT)
    guest = models.ForeignKey(Guest, on_delete=models.PROTECT)

    # Booking identifiers
    reference = models.CharField(max_length=20, unique=True, blank=True, null=True, db_index=True)
    pims_id = models.IntegerField(blank=True, null=True)
    platform_id = models.CharField(max_length=200, blank=True, null=True)
    ical_uid = models.URLField(blank=True, null=True)

    # Booking details
    arrival_date = models.DateField()
    departure_date = models.DateField()
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

    def clean(self):
        super().clean()
        if self.property_id and self.arrival_date and self.departure_date:
            overlap = Booking.objects.overlapping(
                self.property_id, self.arrival_date, self.departure_date
            ).exclude(pk=self.pk).first()
            if overlap:
                message = f"These dates overlap an existing booking ({overlap.arrival_date} to {overlap.departure_date})."
                raise ValidationError({'arrival_date': message, 'departure_date': message})

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

    class Meta:
        db_table = 'booking_guests'
        verbose_name = 'Booking Guest'
        verbose_name_plural = 'Booking Guests'
        ordering = ('-is_lead', 'id')

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.booking})"


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


class Arrival(models.Model):
    """Booking arrival information."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='arrival')
    flight_number = models.CharField(max_length=50, blank=True, null=True)
    is_faro = models.BooleanField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    self_check_in = models.BooleanField(blank=True, null=True)
    meet_greet = models.BooleanField()

    class Meta:
        db_table = 'booking_arrivals'
        verbose_name = 'Arrival'
        verbose_name_plural = 'Arrivals'

    def __str__(self):
        return f"{self.booking} - Arrival {self.date}"


class Departure(models.Model):
    """Booking departure information."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='departure')
    flight_number = models.CharField(max_length=50, blank=True, null=True)
    is_faro = models.BooleanField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    clean = models.BooleanField()
    manual_date = models.BooleanField(blank=True, null=True)

    class Meta:
        db_table = 'booking_departures'
        verbose_name = 'Departure'
        verbose_name_plural = 'Departures'

    def __str__(self):
        return f"{self.booking} - Departure {self.date}"


class Charge(models.Model):
    """Booking charges and payments."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='charges')
    
    # Payment methods
    bank_transfer = models.BooleanField(blank=True, null=True)
    credit_card = models.BooleanField(blank=True, null=True)
    currency = models.CharField(max_length=3, blank=True, null=True, choices=CURRENCY_CHOICES)
    
    # Charge amounts
    basic_rental = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    admin = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
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

    def costs_in_gbp(self):
        """GBP-converted view of the locked EUR charge amounts, using the rate frozen at booking time."""
        if self.gbp_conversion_rate is None:
            return None
        return {
            'basic_rental': self.to_gbp(self.basic_rental),
            'admin_fee': self.to_gbp(self.admin),
            'subtotal': self.to_gbp(self.basic_rental + self.admin),
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


class Extra(models.Model):
    """Booking extras and additional services."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='extras')
    
    # Baby/child items
    cot = models.BooleanField(blank=True, null=True)
    high_chair = models.BooleanField(blank=True, null=True)
    
    # Services
    welcome_pack = models.BooleanField(blank=True, null=True)
    welcome_pack_modifications = models.TextField(blank=True, null=True)
    mid_stay_clean = models.BooleanField(blank=True, null=True)
    late_checkout = models.BooleanField(blank=True, null=True)
    other_requests = models.TextField(blank=True, null=True)
    extra_nights = models.BooleanField(blank=True, null=True)
    
    # Transport
    airport_transfers = models.BooleanField(blank=True, null=True)
    airport_transfer_inbound_only = models.BooleanField(blank=True, null=True)
    airport_transfer_outbound_only = models.BooleanField(blank=True, null=True)
    child_seats = models.CharField(max_length=200, blank=True, null=True)
    excess_baggage = models.CharField(max_length=200, blank=True, null=True)
    
    # Payment
    owner_is_paying = models.BooleanField(blank=True, null=True)

    class Meta:
        db_table = 'booking_extras'
        verbose_name = 'Extra'
        verbose_name_plural = 'Extras'

    def __str__(self):
        return f"{self.booking} - Extras"


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