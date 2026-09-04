import re
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone

import env_settings
from libraries.utils import logerror

REFERENCE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ'  # no 0/O/1/I/L/U - avoids transcription errors
REFERENCE_GROUP_LENGTH = 4
REFERENCE_GROUPS = 2

WISE_MONTHS = {11, 12, 1, 2, 3}  # Nov-Mar arrivals

# The two legacy PIMS calendar-block categories (an owner/admin marking a property unbookable, or
# holding a late check-out) - not a real guest, so never a real arrival/departure for anything
# downstream (a check-in, a "next arrival" a turnover clean is racing to be ready for) to key off.
# Identified by guest.last_name, lowercased - the only signal available (no dedicated flag on
# Booking) - matching canonical spellings guests/management/commands/
# consolidate_block_guest_records.py already normalized legacy casing variants onto. Lives here
# (not staff/utils.py, its original home) so bookings/models.py's BookingQuerySet methods can
# filter on it too without staff importing back into bookings - keep this the one definition,
# don't duplicate the strings elsewhere.
BLOCK_UNBOOKABLE_LAST_NAME = 'block - unbookable'
BLOCK_LATE_CHECK_OUT_LAST_NAME = 'block - late check-out'
BLOCK_GUEST_LAST_NAMES = {BLOCK_UNBOOKABLE_LAST_NAME, BLOCK_LATE_CHECK_OUT_LAST_NAME}


def generate_reference_candidate():
    """One random booking-reference string, e.g. 'K7QX-3H9M'. Not guaranteed unique - the caller checks."""
    groups = [
        ''.join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_GROUP_LENGTH))
        for _ in range(REFERENCE_GROUPS)
    ]
    return '-'.join(groups)


def _apply_manual_discount(basic_total, discount_total, manual_discount_percent):
    """discount_total with a staff-granted manual_discount_percent (if any) folded in as a % of
    basic_total. Shared by create_booking() and both recalculate_*_for_party() below, so a manual
    discount granted on a staff-offer booking (staff/views.py::StaffGuestOfferCreateView) survives
    every later party-size recalculation - without this, the guest-list step's price-recalculation
    would recompute discount_total from the automatic Price-row discount alone, silently dropping
    the manual discount the moment a guest confirms an otherwise-unchanged party (2026-09-04, found
    via manual end-to-end verification of the staff-offer feature, not requested independently)."""
    if not manual_discount_percent:
        return discount_total
    manual_discount_amount = (
        Decimal(basic_total) * Decimal(manual_discount_percent) / Decimal('100')
    ).quantize(Decimal('0.01'))
    return discount_total + manual_discount_amount


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


def create_booking(property, guest_data, start_date, end_date, guests, currency='EUR',
                    enquiry_source='Website', manual_discount_percent=None, manual_discount_reason=''):
    """Create the Guest (if new), Booking, and locked-in Charge for a reservation, all-or-nothing.

    guest_data: dict with first_name, last_name, email, phone, country.
    guests: dict with adults/children/infants, as returned by availability.utils.guests_string_to_dict.
    currency: 'EUR' or 'GBP' - the quote currency the guest was viewing at booking time, recorded on
    the Charge for staff follow-up. The charge amounts themselves are always locked in EUR.

    enquiry_source/manual_discount_percent/manual_discount_reason exist for
    staff/views.py::StaffGuestOfferCreateView (a staff-created "offer" booking, enquiry_source=
    'Staff offer') - defaults preserve the original guest-funnel behavior exactly (ReserveView,
    the only other caller, never passes them). manual_discount_percent is a one-off % of
    basic_total staff apply on top of the normal Price-row-driven discount (weekly/monthly/last-
    minute) - folded straight into the Charge's discount_total (with manual_discount_reason stored
    alongside purely for display/audit) so Charge.total_rental and everything that reads it need
    no special-casing.

    Raises django.core.exceptions.ValidationError (from Booking.full_clean()) if the dates are no
    longer available. Returns the created Booking.
    """
    from bookings.models import BalancePayment, Booking, BookingSettings, Charge, Departure, Payment
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
                country=guest_data.get('country') or None,
            )

        booking_settings = BookingSettings.load()
        pricing = get_stay_total_price(
            property, start_date, end_date, guests,
            monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
        )
        if pricing is None:
            raise ValidationError("Pricing is not available for the selected dates.")

        discount_total = _apply_manual_discount(
            pricing['basic_total'], pricing['discount_total'], manual_discount_percent,
        )
        rental_total = pricing['basic_total'] - discount_total + pricing['extra_guest_total']
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
            enquiry_source=enquiry_source,
            adults=guests.get('adults', 0),
            children=guests.get('children', 0),
            babies=guests.get('infants', 0),
            last_updated=timezone.now(),
            hold_expires_at=hold_expires_at,
        )
        booking.full_clean()
        booking.save()

        # One-time waiver-aware calculation, right here at first submission while guest.country/
        # booking.is_owner/enquiry_source are all in their final initial state (2026-09-02, per
        # Thomas) - Charge.security is the actual source of truth for what's owed at check-in from
        # this point on, not a live recomputation. Nothing re-derives it after this (see the
        # matching comments in bookings/views.py's party-size recompute views) - if a guest's
        # circumstances change later (e.g. edits their country), that's on staff to notice and
        # adjust directly on the Booking page, not something the system chases automatically.
        security_deposit = Decimal('0.00') if compute_deposit_waiver(booking)['waived'] else costs['security_deposit']

        Charge.objects.create(
            booking=booking,
            basic_rental=pricing['basic_total'],
            discount_total=discount_total,
            extra_guest_total=pricing['extra_guest_total'],
            admin=costs['admin_fee'],
            security=security_deposit,
            due_at_booking=costs['due_at_booking'],
            due_at_balance=costs['due_at_balance'],
            balance_due_date=costs['balance_due_date'],
            currency=currency,
            gbp_conversion_rate=booking_settings.gbp_conversion_rate,
            manual_discount_percent=manual_discount_percent,
            manual_discount_reason=manual_discount_reason,
        )

        Payment.objects.create(booking=booking, provider=provider)

        if costs['due_at_balance'] > 0:
            BalancePayment.objects.create(booking=booking, provider=provider)

        # Created eagerly (clean defaults to True) so an end-of-stay clean is scheduled for every
        # booking from the moment it's made, not only once staff happen to open its Booking Info
        # panel - see Departure's own docstring.
        Departure.objects.create(booking=booking)

        from communications.services.scheduling import create_scheduled_emails_for_booking
        create_scheduled_emails_for_booking(booking)

    return booking


def guest_for_owner(owner):
    """The Guest row representing a properties.models.Owner's own identity for their self-booked
    stays (Owner Suite) - get_or_create by email, the same dedup convention create_booking() uses
    for a real guest, so an owner who books more than once always reuses the same Guest row.
    Owner.name isn't reliably splittable into first/last (it can be a company-style name), so the
    whole thing goes into last_name rather than guessing a split that could mangle it."""
    from guests.models import Guest

    email = owner.email.strip().lower()
    guest = Guest.objects.filter(email__iexact=email).first()
    if guest is None:
        guest = Guest.objects.create(last_name=owner.name, email=email)
    return guest


def create_owner_booking(property, owner, start_date, end_date, adults, children, babies, clean=True, meet_greet=True):
    """Creates a new is_owner=True Booking for `owner`'s own stay at their own `property` - the
    Owner Suite's self-service reservation flow (owners/views.py::OwnerBookingCreateView).
    Unlike create_booking(), there's no pricing/Charge/Payment at all - an owner stay is never
    charged. Both Departure and Arrival are created eagerly (unlike create_booking(), which only
    creates Departure up front) - the owner sets clean/meet_greet as part of this same reservation
    form (2026-08-30, per Thomas), so there's a real initial value to give them from the start
    rather than leaving Arrival to be lazily get_or_create'd on first edit. Raises ValidationError
    (via Booking.full_clean(), same as create_booking()) if the dates overlap an existing booking,
    if `start_date` is in the past (server-side backstop for the same "no past dates" rule the
    guest-facing search picker already enforces client-side), or if `property` doesn't actually
    belong to `owner` (a defense-in-depth check - the caller should already be constraining the
    property choice to the owner's own properties)."""
    from bookings.models import Arrival, Booking, Departure

    if property.owner_id != owner.pk:
        raise ValidationError("This property doesn't belong to this owner.")
    if start_date < date.today():
        raise ValidationError("Arrival date can't be in the past.")

    booking = Booking(
        property=property,
        guest=guest_for_owner(owner),
        arrival_date=start_date,
        departure_date=end_date,
        is_owner=True,
        enquiry_status='Booking confirmed',
        enquiry_date=date.today(),
        enquiry_source='Owner Suite',
        adults=adults,
        children=children,
        babies=babies,
        last_updated=timezone.now(),
    )
    booking.full_clean()
    booking.save()
    Departure.objects.create(booking=booking, clean=clean)
    Arrival.objects.create(booking=booking, meet_greet=meet_greet)
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


def compute_tourist_tax(booking, booking_settings=None):
    """Municipal tourist tax: qualifying_guests x min(nights, max_nights) x per_night. Qualifying
    guests are named party members at/above tourist_tax_min_age, computed from their real ages
    (BookingGuest.age) - deliberately not the adults/children/babies headcount split, since that's
    keyed to a different (pricing) age cutoff, see BookingSettings.tourist_tax_min_age's docstring.
    Returns (total, qualifying_guests, nights) so callers can show a full breakdown."""
    from bookings.models import BookingSettings

    booking_settings = booking_settings or BookingSettings.load()
    nights = min((booking.departure_date - booking.arrival_date).days, booking_settings.tourist_tax_max_nights)
    qualifying_guests = booking.party.filter(age__gte=booking_settings.tourist_tax_min_age).count()
    total = Decimal(qualifying_guests) * Decimal(nights) * booking_settings.tourist_tax_per_night
    return total, qualifying_guests, nights


def recalculate_costs_for_party(booking, ages):
    """Recompute costs from real party ages, reusing get_stay_total_price()/compute_costs() exactly
    as create_booking() does at initial booking time. Returns (new_guests, new_costs, changed) -
    changed compares the final rental total, i.e. new_costs['rental_total'] against the booking's
    current Charge.total_rental (not due_at_booking, which is a rounded percentage and can
    coincidentally match across different rentals). Returns
    (None, None, None) if the stay can no longer be priced at all (e.g. a Price row was
    edited/removed since the reservation was made) - the same situation create_booking() raises
    ValidationError for; the caller must handle it explicitly instead. Writes nothing to the DB -
    the caller (bookings/views.py::BookingDetailsView) decides whether/what to persist.

    Re-applies the booking's own Charge.manual_discount_percent (if any) on top of the freshly
    recomputed automatic discount, same as create_booking() - see _apply_manual_discount()'s own
    docstring for why this matters: without it, a staff-offer booking's discount would silently
    vanish the moment a guest confirms their (even unchanged) party on the guest-list step."""
    from bookings.models import BookingSettings
    from properties.utils import get_stay_total_price

    booking_settings = BookingSettings.load()
    new_guests = guest_counts_by_age(ages, booking_settings)
    pricing = get_stay_total_price(
        booking.property, booking.arrival_date, booking.departure_date, new_guests,
        monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
    )
    if pricing is None:
        return None, None, None
    discount_total = _apply_manual_discount(
        pricing['basic_total'], pricing['discount_total'], booking.charges.manual_discount_percent,
    )
    rental_total = pricing['basic_total'] - discount_total + pricing['extra_guest_total']
    new_costs = booking_settings.compute_costs(rental_total, arrival_date=booking.arrival_date)
    new_costs['basic_rental'] = pricing['basic_total']
    new_costs['discount_total'] = discount_total
    new_costs['extra_guest_total'] = pricing['extra_guest_total']
    changed = booking.charges.total_rental is None or new_costs['rental_total'] != booking.charges.total_rental
    return new_guests, new_costs, changed


def recalculate_balance_for_party(booking, ages):
    """Balance-stage equivalent of recalculate_costs_for_party(), for a two-stage booking whose
    deposit (due_at_booking) is already paid and collected - editing the guest list here can only
    move due_at_balance, never retroactively redefine what the deposit "should have been".

    Also re-applies Charge.manual_discount_percent, same as recalculate_costs_for_party() - see
    _apply_manual_discount()'s own docstring.

    Reuses get_stay_total_price()/BookingSettings.compute_costs() the same way for
    rental_total/admin_fee/subtotal (one formula, not duplicated), but then discards
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
    pricing = get_stay_total_price(
        booking.property, booking.arrival_date, booking.departure_date, new_guests,
        monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
    )
    if pricing is None:
        return None, None, None

    charge = booking.charges
    discount_total = _apply_manual_discount(
        pricing['basic_total'], pricing['discount_total'], charge.manual_discount_percent,
    )
    rental_total = pricing['basic_total'] - discount_total + pricing['extra_guest_total']
    new_costs = booking_settings.compute_costs(rental_total, arrival_date=booking.arrival_date)
    new_costs['basic_rental'] = pricing['basic_total']
    new_costs['discount_total'] = discount_total
    new_costs['extra_guest_total'] = pricing['extra_guest_total']
    new_costs['due_at_booking'] = charge.due_at_booking
    new_costs['due_at_balance'] = max(new_costs['subtotal'] - charge.due_at_booking, Decimal('0'))
    new_costs['balance_due_date'] = charge.balance_due_date
    changed = new_costs['rental_total'] != charge.total_rental
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
    combo charge (see ExtrasSettings.compute_cot_high_chair_price), not two separate amounts.

    Also includes one line per BookingDateAdjustment with a nonzero additional_charge (2026-09-02,
    per Thomas) - a platform-derived booking's off-platform stay extension is the same kind of
    cash-at-check-in money as everything else here (see that model's own docstring: deliberately
    not wired into Charge, same as Extras itself), and this is the one function both the booking
    detail page's Extras panel and the check-in calendar popup's Extras section (staff/views.py,
    both call this) already share - so surfacing it here is what puts it in front of whoever's
    actually collecting cash from the guest on arrival, not just the Owner Payout figures."""
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

    if extra and extra.mid_stay_clean:
        # No date in the label (2026-08-27) - it's no longer guest-chosen (see
        # bookings.utils.mid_stay_clean_window), so surfacing one here read as more of a
        # commitment than the "estimate" it actually is.
        items.append({'label': "Mid-stay Clean", 'price': extra.mid_stay_clean_charge or 0})

    for transfer in booking.airport_transfers.all():
        detail = transfer.flight_number or (transfer.time.strftime('%H:%M') if transfer.time else '')
        label = f"Airport Transfer - {transfer.get_direction_display()}"
        items.append({'label': f"{label} ({detail})" if detail else label, 'price': transfer.price_at_request or 0})

    # Bare .all(), not .select_related('request_type') - a caller that's already
    # prefetch_related('requested_extras__request_type') (e.g. StaffCleaningRotaView, batching
    # extras_summary() across many bookings, 2026-09-03) needs a bare .all() to hit that cache;
    # any further queryset method here (including select_related) builds a fresh, uncached
    # queryset instead, silently reintroducing one query per booking. A caller that hasn't
    # prefetched still only pays one lazy query per requested_extra row accessed below - never
    # more than a handful for a single booking, which is the normal case everywhere except the
    # one place that now prefetches.
    for requested in booking.requested_extras.all():
        label = requested.request_type.name
        items.append({
            'label': f"{label} x{requested.quantity}" if requested.quantity != 1 else label,
            'price': requested.price_at_request * requested.quantity,
        })

    for adjustment in booking.date_adjustments.all():
        if not adjustment.additional_charge:
            continue
        previous_nights = (adjustment.previous_departure_date - adjustment.previous_arrival_date).days
        new_nights = (adjustment.new_departure_date - adjustment.new_arrival_date).days
        nights_delta = new_nights - previous_nights
        sign = '+' if nights_delta >= 0 else ''
        night_word = "night" if abs(nights_delta) == 1 else "nights"
        items.append({
            'label': f"Stay extension ({sign}{nights_delta} {night_word}, cash on arrival)",
            'price': adjustment.additional_charge,
        })

    return {'items': items, 'total': sum((item['price'] for item in items), start=Decimal('0'))}


def mid_stay_clean_window(booking):
    """(default_date, min_date, max_date) for a mid-stay clean on this booking - the default
    lands as close to the middle of the stay as an integer day allows (rounding toward the
    earlier half on an even split), and min/max are one day either side of it, clamped so neither
    ever reaches the arrival/departure day itself (those are the checkout/check-in cleans, not a
    mid-stay one). Not guest-editable (see BookingFormMixin._mid_stay_clean_default_date, which
    just wraps this) - a date this small cleaning team can't reliably staff around is a fixed
    estimate, not a guest negotiation. The min/max window still matters for staff: it's what
    staff/utils.py::cleaning_task_valid_range lets a mid-stay CleaningTask be dragged within on
    the cleaning calendar, one day either side of the estimate the guest was shown. The clamp only
    bites for a stay right at ExtrasSettings.mid_stay_clean_minimum_nights, where the default sits
    on (or one day from) a boundary day and the ±1 window would otherwise spill onto it."""
    nights = (booking.departure_date - booking.arrival_date).days
    default_date = booking.arrival_date + timedelta(days=nights // 2)
    min_date = max(default_date - timedelta(days=1), booking.arrival_date + timedelta(days=1))
    max_date = min(default_date + timedelta(days=1), booking.departure_date - timedelta(days=1))
    return default_date, min_date, max_date


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


def compute_deposit_waiver(booking):
    """Whether this booking's cash security deposit should be waived, and why - a ONE-TIME
    calculation, used only by create_booking() to seed Charge.security at the moment a guest first
    submits their details (2026-09-02, per Thomas - see Charge.security's own docstring,
    bookings/models.py, for the full design: that field is the actual source of truth for what's
    owed from that point on, never re-derived, staff-editable directly for anything this function
    can't catch). NOT used for live display anywhere - the check-ins popup, the guest-facing
    confirmation page, and the manage-booking sidebar's deposit gate all read Charge.security
    directly instead of calling this again.

    Four independent conditions, any one is enough:
    - The property owner themself or a family/friend of theirs staying (booking.is_owner) - an
      owner booking is never charged a deposit, full stop.
    - A returning guest (has_completed_previous_stay).
    - A platform whose own terms mean we don't take one directly (properties.models.Platform.
      take_security_deposits=False, matched by booking.enquiry_source - 2026-08-28, per Thomas).
    - A guest whose country of residence is outside the UK/EU (env_settings.UK_EU_COUNTRY_CODES -
      2026-08-29, per Thomas: the cash-in/bank-transfer-back process has extra cost/hassle for
      those). A guest with no country on record is treated as NOT outside the UK/EU - unknown
      isn't the same as confirmed-international, so this never silently waives a deposit that
      would otherwise be taken."""
    from properties.models import Platform

    by_owner_booking = booking.is_owner
    platform = Platform.objects.filter(name=booking.enquiry_source).first()
    by_platform = platform is not None and not platform.take_security_deposits
    by_returning_guest = has_completed_previous_stay(booking.guest, exclude_booking_id=booking.pk)
    guest_country = booking.guest.country
    by_country = bool(guest_country) and guest_country.code not in env_settings.UK_EU_COUNTRY_CODES
    return {
        'waived': by_owner_booking or by_platform or by_returning_guest or by_country,
        'by_owner_booking': by_owner_booking,
        'by_platform': by_platform and not by_owner_booking,
        'by_country': by_country and not by_platform and not by_owner_booking,
        'by_returning_guest': by_returning_guest,
    }


def booking_confirmation_context(booking):
    """Display context shared by the post-booking redirect and the manage-lookup success state."""
    charge = booking.charges
    balance_payment = getattr(booking, 'balance_payment', None)
    cancelled = booking.enquiry_status == 'Cancelled by guest'  # mirrors views.py::is_cancelled()
    # Deliberately keyed off the BalancePayment's own paid status, not 'balance_due' below - a
    # cancelled booking whose balance was genuinely paid before the cancellation should still show
    # that money as paid; 'balance_due' folds in "and not cancelled" for a different purpose (hiding
    # the Pay Balance button on a stay there's nothing left to buy toward), which would otherwise
    # double-count due_at_balance into 'Paid' for a cancelled-but-never-paid booking.
    balance_paid = balance_payment is not None and balance_payment.status == 'paid'
    paid_amount = charge.due_at_booking + (charge.due_at_balance if balance_paid else 0)
    return {
        'booking': booking,
        'charge': charge,
        'subtotal': charge.total_rental + charge.admin,
        'nights': (booking.departure_date - booking.arrival_date).days,
        'costs_gbp': charge.costs_in_gbp(),
        'cancelled': cancelled,
        'paid_amount': paid_amount,
        'paid_amount_gbp': charge.to_gbp(paid_amount),
        # Self-serve entry point into the balance flow, for a guest who wants to pay early or lost
        # a manually-sent link (no automated reminder email yet - see BalancePayment's docstring).
        # Excludes a cancelled booking - there's nothing to pay toward a cancelled stay, even if
        # the balance was technically never collected.
        'balance_due': balance_payment is not None and balance_payment.status != 'paid' and not cancelled,
        # Charge.security is the actual source of truth for what's owed (see its own docstring,
        # bookings/models.py) - not recomputed here, just read directly.
        'deposit_due': bool(charge.security),
    }


def sync_ical_link(link, ics_text):
    """Sync one iCalLink's already-fetched feed text against our Bookings - pure function (no HTTP
    of its own) so tests can hand it a canned .ics string directly. Called from
    bookings/management/commands/sync_ical_feeds.py, which does the actual fetch and lets any
    fetch/parse exception propagate up to its own per-property try/except (this function assumes
    ics_text is a feed that parsed enough to be worth reading, not resilient to garbage).

    Only ever touches Bookings this exact mechanism created (matched by ical_uid, property, and
    the platform's own enquiry_source) - never a manually-entered platform booking without a UID.
    A feed event whose dates would overlap an existing holding booking (a direct booking, or
    another platform's own already-imported one) is skipped entirely rather than risk creating a
    double-booked calendar entry - both for a brand new event and for an existing matched one whose
    dates changed. manual_override (see Booking's own docstring) blocks date updates but not
    cancellation-on-disappearance - it means "don't overwrite dates automatically", not "never let
    sync touch this booking again". A previously-cancelled booking whose UID reappears in the feed
    is resurrected back to 'Booking confirmed' - the platform un-cancelled it.

    Returns a dict of counts (created/updated/resurrected/cancelled/excluded) plus a 'conflicts'
    list ({'uid', 'start', 'end'} per skipped overlap) for the caller to report. Also returns an
    'events' list - one entry per feed event ({'uid', 'start', 'end', 'result', 'booking'},
    'result' one of created/updated/resurrected/unchanged/manual_override/conflict) - and a
    'cancelled_bookings' list of the Booking objects cancelled because they'd disappeared from the
    feed (these aren't feed events, so they don't get an 'events' entry of their own) - both added
    for the staff "Sync now" popup (staff/views.py::StaffIcalSyncView) to report a per-booking
    breakdown, matching PIMS' own manual-sync popup rather than just an aggregate count.

    A VEVENT whose SUMMARY matches link.excluded_summary_terms() (properties/models.py::iCalLink,
    e.g. Airbnb's "Not Available" blocks, Vrbo's "Tentative" enquiries) is dropped before any of
    the above even sees it - counted in 'excluded', never appears in 'events', and never enters
    feed_events at all. That last part matters for the disappearance-cancellation pass below: if a
    Booking was already created from a now-excluded UID before this filter existed, it's treated
    exactly like any other booking that vanished from the feed and gets cancelled - the correct
    outcome, since it was never a real stay."""
    from icalendar import Calendar

    from bookings.models import Arrival, Booking, Departure
    from guests.models import Guest

    summary = {
        'created': 0, 'updated': 0, 'resurrected': 0, 'cancelled': 0, 'excluded': 0, 'conflicts': [],
        'events': [], 'cancelled_bookings': [],
    }

    platform_name = link.platform.name if link.platform_id else None
    if platform_name is None:
        logerror(f"iCal link {link.pk} for {link.property} has no platform set - skipped.")
        return summary

    def as_date(value):
        return value.date() if isinstance(value, datetime) else value

    exclude_terms = [term.lower() for term in link.excluded_summary_terms()]

    def is_excluded(component):
        if not exclude_terms:
            return False
        summary = str(component.get('summary') or '').lower()
        return any(term in summary for term in exclude_terms)

    calendar = Calendar.from_ical(ics_text)
    feed_events = {}
    for component in calendar.walk('VEVENT'):
        if is_excluded(component):
            summary['excluded'] += 1
            continue
        uid = str(component.get('uid'))
        feed_events[uid] = (as_date(component.get('dtstart').dt), as_date(component.get('dtend').dt))

    for uid, (start, end) in feed_events.items():
        existing = Booking.objects.filter(
            property=link.property, enquiry_source=platform_name, ical_uid=uid,
        ).first()

        if existing is not None:
            result = 'unchanged'
            dates_changed = (existing.arrival_date, existing.departure_date) != (start, end)
            if existing.manual_override:
                if dates_changed:
                    result = 'manual_override'
            elif dates_changed:
                if Booking.objects.overlapping(link.property, start, end).exclude(pk=existing.pk).exists():
                    summary['conflicts'].append({'uid': uid, 'start': start, 'end': end})
                    result = 'conflict'
                else:
                    existing.arrival_date = start
                    existing.departure_date = end
                    existing.save(update_fields=['arrival_date', 'departure_date'])
                    summary['updated'] += 1
                    result = 'updated'
            if existing.enquiry_status == 'Cancelled by platform':
                existing.enquiry_status = 'Booking confirmed'
                existing.save(update_fields=['enquiry_status'])
                summary['resurrected'] += 1
                result = 'resurrected'
            summary['events'].append(
                {'uid': uid, 'start': start, 'end': end, 'result': result, 'booking': existing}
            )
            continue

        if Booking.objects.overlapping(link.property, start, end).exists():
            summary['conflicts'].append({'uid': uid, 'start': start, 'end': end})
            summary['events'].append(
                {'uid': uid, 'start': start, 'end': end, 'result': 'conflict', 'booking': None}
            )
            continue

        guest = Guest.objects.create(last_name=f"{platform_name} Guest")
        booking = Booking.objects.create(
            property=link.property, guest=guest,
            arrival_date=start, departure_date=end,
            # is_owner_link (properties.models.iCalLink) is the source of truth for a feed the
            # owner runs themselves rather than one we manage - see that field's own docstring.
            is_owner=link.is_owner_link, enquiry_status='Booking confirmed',
            enquiry_date=date.today(), enquiry_source=platform_name,
            adults=1, children=0, babies=0,
            last_updated=timezone.now(), ical_uid=uid,
        )
        # Same defaults every other Arrival/Departure.get_or_create() call site already uses
        # (bookings/views.py::_save_arrival, StaffBookingDetailView._update_booking(),
        # owners/views.py) - without this, a booking created here had no Arrival/Departure at all
        # until staff happened to open and save its detail page, leaving the check-ins calendar
        # popup showing blank Method/Time in the meantime (found 2026-09-02, see
        # sync_arrival_departure_legacy_data.py for the one-off backfill this gap needed).
        Arrival.objects.create(booking=booking, self_check_in=False, meet_greet=True)
        Departure.objects.create(booking=booking, clean=True)
        summary['created'] += 1
        summary['events'].append(
            {'uid': uid, 'start': start, 'end': end, 'result': 'created', 'booking': booking}
        )

    today = date.today()
    previously_imported = Booking.objects.filter(
        property=link.property, enquiry_source=platform_name, ical_uid__isnull=False,
        departure_date__gte=today,
    ).exclude(enquiry_status='Cancelled by platform')
    for booking in previously_imported:
        if booking.ical_uid not in feed_events:
            booking.enquiry_status = 'Cancelled by platform'
            booking.save(update_fields=['enquiry_status'])
            summary['cancelled'] += 1
            summary['cancelled_bookings'].append(booking)

    link.last_synced = timezone.now()
    link.save(update_fields=['last_synced'])

    return summary


FLIGHT_NUMBER_RE = re.compile(r'^(?=.*[A-Za-z])[A-Za-z0-9]{1,3}[ -]?\d{3,5}$')
FLIGHT_NUMBER_HINT = "That doesn't look like a flight number (e.g. TP1234) - please double-check it."


def parsed_travel_method(raw):
    """Falls back to FLIGHT_FARO if raw isn't a real TravelMethod value - this form has never
    hard-required a method and shouldn't start now, guest or staff side."""
    from bookings.models import TravelMethod
    return raw if raw in TravelMethod.values else TravelMethod.FLIGHT_FARO


def valid_flight_number(method, flight_number):
    """1-3 letters/digits (at least one letter, so real IATA codes like easyJet's "U2" - which
    mixes a digit into the airline code - still pass) then 3-5 digits, e.g. TP1234 or U21234. Only
    enforced for the two flight TravelMethods, and only when non-blank. Shared by every
    Arrival/Departure entry point (guest-facing BookingManageArrivalDepartureView/
    BookingBalanceDetailsView and the staff booking detail page) so the rule can't drift between
    them."""
    from bookings.models import TravelMethod
    if method not in (TravelMethod.FLIGHT_FARO, TravelMethod.FLIGHT_LISBON) or not flight_number:
        return True
    return bool(FLIGHT_NUMBER_RE.match(flight_number))


def parsed_arrival_departure_time(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%H:%M').time()
    except ValueError:
        return None
