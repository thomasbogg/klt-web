import secrets
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

REFERENCE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ'  # no 0/O/1/I/L/U - avoids transcription errors
REFERENCE_GROUP_LENGTH = 4
REFERENCE_GROUPS = 2

WISE_MONTHS = {11, 12, 1, 2, 3}  # Nov-Mar arrivals


def generate_reference_candidate():
    """One random booking-reference string, e.g. 'K7QX-3H9M'. Not guaranteed unique - the caller checks."""
    groups = [
        ''.join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_GROUP_LENGTH))
        for _ in range(REFERENCE_GROUPS)
    ]
    return '-'.join(groups)


def determine_payment_provider(arrival_date):
    """Which payment provider handles a booking's deposit, decided by arrival month, not guest
    choice. Nov-Mar arrivals go through Wise (a static pay page, no in-progress payment signal);
    Apr-Oct go through Revolut (a Payment Link whose checkout supports card + Open Banking, and
    whose webhooks expose an in-progress signal - see bookings/views.py::BookingPaymentView)."""
    return 'wise' if arrival_date.month in WISE_MONTHS else 'revolut'


def create_booking(property, guest_data, start_date, end_date, guests, currency='EUR'):
    """Create the Guest (if new), Booking, and locked-in Charge for a reservation, all-or-nothing.

    guest_data: dict with first_name, last_name, email, phone.
    guests: dict with adults/children/infants, as returned by availability.utils.guests_string_to_dict.
    currency: 'EUR' or 'GBP' - the quote currency the guest was viewing at booking time, recorded on
    the Charge for staff follow-up. The charge amounts themselves are always locked in EUR.
    Raises django.core.exceptions.ValidationError (from Booking.full_clean()) if the dates are no
    longer available. Returns the created Booking.
    """
    from bookings.models import Booking, BookingSettings, Charge, Payment
    from guests.models import Guest
    from properties.utils import get_stay_total_price

    with transaction.atomic():
        # filter-then-create, not get_or_create: email__iexact isn't a settable field kwarg for the
        # create path. A same-email race can produce a rare duplicate Guest - accepted for now (see plan).
        email = guest_data['email'].strip().lower()
        guest = Guest.objects.filter(email__iexact=email).first()
        if guest is None:
            guest = Guest.objects.create(
                first_name=guest_data.get('first_name', ''),
                last_name=guest_data['last_name'],
                email=email,
                phone=guest_data.get('phone', ''),
            )

        booking_settings = BookingSettings.load()
        rental_total = get_stay_total_price(
            property, start_date, end_date, guests,
            monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
        )
        if rental_total is None:
            raise ValidationError("Pricing is not available for the selected dates.")
        costs = booking_settings.compute_costs(rental_total, arrival_date=start_date)

        provider = determine_payment_provider(start_date)
        if provider == 'wise':
            hold_expires_at = timezone.now() + timedelta(hours=booking_settings.wise_hold_hours)
        else:
            hold_expires_at = timezone.now() + timedelta(minutes=booking_settings.revolut_hold_minutes)

        booking = Booking(
            property=property,
            guest=guest,
            arrival_date=start_date,
            departure_date=end_date,
            is_owner=False,
            enquiry_status='Awaiting payment',
            enquiry_date=date.today(),
            enquiry_source='Website',
            adults=guests.get('adults', 0),
            children=guests.get('children', 0),
            babies=guests.get('infants', 0),
            last_updated=timezone.now(),
            hold_expires_at=hold_expires_at,
        )
        booking.full_clean()
        booking.save()

        Charge.objects.create(
            booking=booking,
            basic_rental=costs['basic_rental'],
            admin=costs['admin_fee'],
            security=costs['security_deposit'],
            due_at_booking=costs['due_at_booking'],
            due_at_balance=costs['due_at_balance'],
            balance_due_date=costs['balance_due_date'],
            currency=currency,
            gbp_conversion_rate=booking_settings.gbp_conversion_rate,
        )

        Payment.objects.create(booking=booking, provider=provider)

    return booking


def booking_confirmation_context(booking):
    """Display context shared by the post-booking redirect and the manage-lookup success state."""
    charge = booking.charges
    return {
        'booking': booking,
        'charge': charge,
        'subtotal': charge.basic_rental + charge.admin,
        'nights': (booking.departure_date - booking.arrival_date).days,
        'costs_gbp': charge.costs_in_gbp(),
    }
