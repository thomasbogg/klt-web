from django.db import models
from django.core.validators import EmailValidator
from properties.models import Property
from guests.models import Guest


class Booking(models.Model):
    """Main booking model."""
    property = models.ForeignKey(Property, on_delete=models.PROTECT)
    guest = models.ForeignKey(Guest, on_delete=models.PROTECT)
    
    # Booking identifiers
    pims_id = models.IntegerField(blank=True, null=True)
    platform_id = models.CharField(max_length=200, blank=True, null=True)
    
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
    manual_guests = models.BooleanField()
    
    # Metadata
    last_updated = models.DateTimeField()

    class Meta:
        db_table = 'bookings'
        verbose_name = 'Booking'
        verbose_name_plural = 'Bookings'

    def __str__(self):
        return f"{self.property.short_name} - {self.guest.last_name} ({self.id})"


class Arrival(models.Model):
    """Booking arrival information."""
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='arrival')
    flight_number = models.CharField(max_length=50, blank=True, null=True)
    is_faro = models.BooleanField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    self_check_in = models.BooleanField(blank=True, null=True)
    meet_greet = models.BooleanField()
    manual_date = models.BooleanField(blank=True, null=True)

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
    currency = models.CharField(max_length=10, blank=True, null=True)
    
    # Charge amounts
    basic_rental = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    admin = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    security = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    security_method = models.CharField(max_length=100, blank=True, null=True)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    extra_nights = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    manual_charges = models.BooleanField(blank=True, null=True)

    class Meta:
        db_table = 'booking_charges'
        verbose_name = 'Charge'
        verbose_name_plural = 'Charges'

    def __str__(self):
        return f"{self.booking} - Charges"


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