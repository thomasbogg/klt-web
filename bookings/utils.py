import secrets
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.defaultfilters import slugify
from django.urls import reverse
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


def add_business_days(start, business_days):
    """start + N business days, skipping Saturdays and Sundays entirely. Shared primitive for
    payment_clearing_expiry() below."""
    current = start
    added = 0
    while added < business_days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Monday-Friday
            added += 1
    return current


def payment_clearing_expiry(now, booking_settings):
    """now + payment_clearing_business_days business days (skipping Sat/Sun). Used both for the
    Wise-path initial hold (Wise gives no in-progress signal, so every Wise booking gets this from
    the moment it's made) and for the Revolut-path hold once ORDER_PAYMENT_AUTHENTICATED fires
    (see klt-hooks postgres_bookings.py::mark_payment_authenticated) - bank transfers can take up
    to 2 business days to settle after authentication; card payments settle in seconds so this
    costs them nothing."""
    return add_business_days(now, booking_settings.payment_clearing_business_days)


def create_booking(property, guest_data, start_date, end_date, guests, currency='EUR'):
    """Create the Guest (if new), Booking, and locked-in Charge for a reservation, all-or-nothing.

    guest_data: dict with first_name, last_name, email, phone.
    guests: dict with adults/children/infants, as returned by availability.utils.guests_string_to_dict.
    currency: 'EUR' or 'GBP' - the quote currency the guest was viewing at booking time, recorded on
    the Charge for staff follow-up. The charge amounts themselves are always locked in EUR.
    Raises django.core.exceptions.ValidationError (from Booking.full_clean()) if the dates are no
    longer available. Returns the created Booking.
    """
    from bookings.models import BalancePayment, Booking, BookingSettings, Charge, Payment
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
            hold_expires_at = payment_clearing_expiry(timezone.now(), booking_settings)
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

        if costs['due_at_balance'] > 0:
            BalancePayment.objects.create(booking=booking, provider=provider)

    return booking


def guest_counts_by_age(ages, booking_settings):
    """Ages are each guest's age AT ARRIVAL (the booking-details page makes this explicit to the
    guest - age is entered directly as of time of stay, no birthdate math needed). Buckets into the
    same {'adults', 'children', 'infants'} shape get_stay_total_price()/compute_costs() expect."""
    counts = {'adults': 0, 'children': 0, 'infants': 0}
    for age in ages:
        if age >= booking_settings.adult_min_age:
            counts['adults'] += 1
        elif age >= booking_settings.child_min_age:
            counts['children'] += 1
        else:
            counts['infants'] += 1
    return counts


def recalculate_costs_for_party(booking, ages):
    """Recompute costs from real party ages, reusing get_stay_total_price()/compute_costs() exactly
    as create_booking() does at initial booking time. Returns (new_guests, new_costs, changed) -
    changed compares basic_rental (not due_at_booking, which is a rounded percentage and can
    coincidentally match across different rentals) against the booking's current Charge. Returns
    (None, None, None) if the stay can no longer be priced at all (e.g. a Price row was
    edited/removed since the reservation was made) - the same situation create_booking() raises
    ValidationError for; the caller must handle it explicitly instead. Writes nothing to the DB -
    the caller (bookings/views.py::BookingDetailsView) decides whether/what to persist."""
    from bookings.models import BookingSettings
    from properties.utils import get_stay_total_price

    booking_settings = BookingSettings.load()
    new_guests = guest_counts_by_age(ages, booking_settings)
    rental_total = get_stay_total_price(
        booking.property, booking.arrival_date, booking.departure_date, new_guests,
        monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
    )
    if rental_total is None:
        return None, None, None
    new_costs = booking_settings.compute_costs(rental_total, arrival_date=booking.arrival_date)
    changed = booking.charges.basic_rental is None or new_costs['basic_rental'] != booking.charges.basic_rental
    return new_guests, new_costs, changed


def recalculate_balance_for_party(booking, ages):
    """Balance-stage equivalent of recalculate_costs_for_party(), for a two-stage booking whose
    deposit (due_at_booking) is already paid and collected - editing the guest list here can only
    move due_at_balance, never retroactively redefine what the deposit "should have been".

    Reuses get_stay_total_price()/BookingSettings.compute_costs() the same way for
    basic_rental/admin_fee/subtotal (one formula, not duplicated), but then discards
    compute_costs()'s own due_at_booking/due_at_balance/balance_due_date split (irrelevant here,
    and its internal collapse-within-the-window branch doesn't apply once a deposit already
    exists) in favour of due_at_balance = max(new_subtotal - due_at_booking, 0). Floored at zero
    deliberately: removing guests can only ever reduce what's still owed, never imply refunding the
    deposit already collected - see BalancePayment's docstring / the plan this was built from.

    Returns (new_guests, new_costs, changed); new_costs has the same keys as compute_costs().
    Returns (None, None, None) if the stay can no longer be priced at all - same as
    recalculate_costs_for_party(). Writes nothing to the DB - the caller
    (bookings/views.py::BookingBalanceDetailsView) decides whether/what to persist."""
    from bookings.models import BookingSettings
    from properties.utils import get_stay_total_price

    booking_settings = BookingSettings.load()
    new_guests = guest_counts_by_age(ages, booking_settings)
    rental_total = get_stay_total_price(
        booking.property, booking.arrival_date, booking.departure_date, new_guests,
        monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
    )
    if rental_total is None:
        return None, None, None

    charge = booking.charges
    new_costs = booking_settings.compute_costs(rental_total, arrival_date=booking.arrival_date)
    new_costs['due_at_booking'] = charge.due_at_booking
    new_costs['due_at_balance'] = max(new_costs['subtotal'] - charge.due_at_booking, Decimal('0'))
    new_costs['balance_due_date'] = charge.balance_due_date
    changed = new_costs['basic_rental'] != charge.basic_rental
    return new_guests, new_costs, changed


def expire_stale_holds():
    """Flip 'Awaiting payment' bookings whose hold has lapsed to a distinct 'Hold expired' status,
    purely for admin visibility - availability itself is already correct regardless (see
    Booking.objects.holding()), since an expired hold falls out of that query on its own. Safe to
    call as often as wanted: a booking only matches here if nothing has extended hold_expires_at
    (see klt-hooks' mark_payment_in_progress()), so genuine in-flight payments are never touched,
    and a webhook's paid/failed write always wins regardless of ordering. Cheap - a single bulk
    UPDATE, not a per-row loop. Called from BookingAdmin.get_queryset() for now; reuse this same
    function from a scheduled job too if/when one exists (see automation roadmap discussion).
    """
    from bookings.models import Booking

    Booking.objects.filter(
        enquiry_status='Awaiting payment',
        hold_expires_at__lt=timezone.now(),
    ).update(enquiry_status='Hold expired')


def cancel_booking_hold(booking):
    """Guest-initiated cancellation of their own not-yet-paid hold - e.g. they picked the wrong
    currency and want to redo the reservation, but their own active hold blocks a second attempt
    at the same dates (see Booking.clean()'s overlap guard). Only acts on a booking still genuinely
    awaiting payment - a no-op (returns False) if it's already confirmed/failed/expired/paid, so
    this can't be used to cancel a real booking by guessing at a reference. Deliberately doesn't
    touch any Revolut order that may already exist - cancelling it via the API would trigger an
    ORDER_CANCELLED webhook, which klt-hooks treats as a genuine payment failure; an abandoned,
    never-completed order is harmless to just leave alone.
    """
    if booking.enquiry_status != 'Awaiting payment':
        return False
    booking.enquiry_status = 'Cancelled by guest'
    booking.save(update_fields=['enquiry_status'])
    return True


def reservation_retry_url(booking):
    """Rebuilds the reserve-page URL (with start/end/guests querystring) for a booking's property
    and dates, so a guest can be sent back to redo a reservation after cancel_booking_hold() -
    mirrors the querystring format properties.views.ReserveView/availability.utils expect."""
    property = booking.property
    location = property.location
    base_url = reverse('properties:property/reserve', kwargs={
        'location': location.slug,
        'title': slugify(property.short_title),
    })
    guests = f"{booking.adults} adults,{booking.children} children,{booking.babies} infants"
    query = urlencode({
        'start': booking.arrival_date.strftime('%d/%m/%Y'),
        'end': booking.departure_date.strftime('%d/%m/%Y'),
        'guests': guests,
    })
    return f"{base_url}?{query}"


def extras_summary(booking):
    """Itemised list of everything the guest actually chose in the Extras section of Booking
    Details, all cash-at-check-in (see bookings/views.py::BookingDetailsView._save_extras -
    extras never touch Charge/Payment). Returns {'items': [{'label', 'price'}], 'total': Decimal}.
    Cot/High Chair is a single line even when both are requested, since they're priced as one
    combo charge (see ExtrasSettings.compute_cot_high_chair_price), not two separate amounts."""
    extra = getattr(booking, 'extras', None)
    items = []

    if extra and extra.welcome_pack:
        items.append({
            'label': f"Welcome Pack ({extra.get_welcome_pack_food_display()}, {extra.get_welcome_pack_drinks_display()})",
            'price': extra.welcome_pack_charge or 0,
        })

    if extra and (extra.cot or extra.high_chair):
        parts = [label for wanted, label in ((extra.cot, 'Cot'), (extra.high_chair, 'High Chair')) if wanted]
        items.append({'label': ' & '.join(parts), 'price': extra.cot_high_chair_charge or 0})

    if extra and extra.late_checkout:
        time_label = f" ({extra.late_checkout_time.strftime('%H:%M')})" if extra.late_checkout_time else ''
        items.append({'label': f"Late Checkout{time_label}", 'price': extra.late_checkout_charge or 0})

    for transfer in booking.airport_transfers.all():
        detail = transfer.flight_number or (transfer.time.strftime('%H:%M') if transfer.time else '')
        label = f"Airport Transfer - {transfer.get_direction_display()}"
        items.append({'label': f"{label} ({detail})" if detail else label, 'price': transfer.price_at_request or 0})

    for requested in booking.requested_extras.select_related('request_type').all():
        label = requested.request_type.name
        items.append({
            'label': f"{label} x{requested.quantity}" if requested.quantity != 1 else label,
            'price': requested.price_at_request * requested.quantity,
        })

    return {'items': items, 'total': sum((item['price'] for item in items), start=Decimal('0'))}


def has_completed_previous_stay(guest, exclude_booking_id=None):
    """A genuinely returning guest: another Booking exists for their email with a departure_date
    already in the past and a valid (non-cancelled/failed) status - not just a prior booking that
    hasn't happened yet. Guards against Guest.email being blank (would otherwise match every other
    blank-email guest via the iexact filter)."""
    if not guest.email:
        return False
    from bookings.models import Booking
    from env_settings import VALID_BOOKING_STATUSES

    qs = Booking.objects.filter(
        guest__email__iexact=guest.email, departure_date__lt=date.today(),
        enquiry_status__in=VALID_BOOKING_STATUSES,
    )
    if exclude_booking_id:
        qs = qs.exclude(pk=exclude_booking_id)
    return qs.exists()


def booking_confirmation_context(booking):
    """Display context shared by the post-booking redirect and the manage-lookup success state."""
    charge = booking.charges
    balance_payment = getattr(booking, 'balance_payment', None)
    cancelled = booking.enquiry_status == 'Cancelled by guest'  # mirrors views.py::is_cancelled()
    return {
        'booking': booking,
        'charge': charge,
        'subtotal': charge.basic_rental + charge.admin,
        'nights': (booking.departure_date - booking.arrival_date).days,
        'costs_gbp': charge.costs_in_gbp(),
        'cancelled': cancelled,
        # Self-serve entry point into the balance flow, for a guest who wants to pay early or lost
        # a manually-sent link (no automated reminder email yet - see BalancePayment's docstring).
        # Excludes a cancelled booking - there's nothing to pay toward a cancelled stay, even if
        # the balance was technically never collected.
        'balance_due': balance_payment is not None and balance_payment.status != 'paid' and not cancelled,
        'returning_guest': has_completed_previous_stay(booking.guest, exclude_booking_id=booking.pk),
    }
