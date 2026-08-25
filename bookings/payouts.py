import calendar
from datetime import timedelta
from decimal import Decimal

import env_settings
from bookings.models import TWO_PLACES, PaymentSettings

ZERO = Decimal('0')


def _is_platform_booking(booking):
    return booking.enquiry_source in env_settings.PLATFORMS


def _is_high_season(payment_settings, arrival_date):
    start = payment_settings.high_season_start_month
    end = payment_settings.high_season_end_month
    month = arrival_date.month
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end


def _commission_percent(payment_settings, arrival_date):
    if _is_high_season(payment_settings, arrival_date):
        return payment_settings.high_season_commission_percent
    return payment_settings.low_season_commission_percent


def _round(amount):
    return Decimal(amount).quantize(TWO_PLACES)


def _management_fee(payment_settings, booking):
    if not booking.property.we_clean:
        return ZERO
    total = ZERO
    departure = getattr(booking, 'departure', None)
    if departure is not None and departure.clean:
        specs = booking.property.specs
        total += booking.property.standard_cleaning_fee
        if specs.bedrooms == 1:
            total += payment_settings.cleaning_surcharge_one_bedroom
        else:
            total += payment_settings.cleaning_surcharge_multi_bedroom
        if specs.bedrooms and booking.total_guests() / specs.bedrooms > 2:
            total += payment_settings.cleaning_high_occupancy_surcharge
    arrival = getattr(booking, 'arrival', None)
    if arrival is not None and arrival.meet_greet:
        total += payment_settings.meet_greet_fee
    return total


def _due_date(payment_settings, owner, arrival_date):
    if owner.is_paid_regularly:
        return arrival_date + timedelta(days=payment_settings.regular_payout_days_after_arrival)
    _, last_day = calendar.monthrange(arrival_date.year, arrival_date.month)
    return arrival_date.replace(day=last_day)


def _unavailable(reason):
    return {
        'available': False,
        'reason': reason,
        'rental_base': None,
        'commission_percent': None,
        'commission': None,
        'commission_vat': None,
        'platform_fee': None,
        'platform_fee_vat': None,
        'management_fee': None,
        'owner_balance': None,
        'due_date': None,
        'is_regular': None,
    }


def compute_owner_payout(booking, payment_settings=None):
    """What the property owner should receive for this booking, and when - see the "Owner
    payouts: timing + amount calculation" plan for how each figure was reverse-engineered against
    a real legacy Bookings Report export. A read-only, computed reference figure only -
    staff.models.OwnerPayment/Deduction remain the manually-entered record of what was actually
    paid."""
    if booking.is_owner:
        return _unavailable("Owner stay - no payout due.")

    owner = booking.property.owner
    if owner is None:
        return _unavailable("Property has no owner assigned.")

    is_platform = _is_platform_booking(booking)
    if is_platform:
        platform_payout = getattr(booking, 'platform_payout', None)
        if platform_payout is None or platform_payout.payout_amount is None:
            return _unavailable("No PlatformPayout figures recorded yet.")
        rental_base = platform_payout.payout_amount
        platform_fee = platform_payout.platform_commission or ZERO
    else:
        charge = getattr(booking, 'charges', None)
        if charge is None or charge.basic_rental is None:
            return _unavailable("No Charge record for this booking.")
        rental_base = charge.basic_rental
        platform_fee = ZERO

    if payment_settings is None:
        payment_settings = PaymentSettings.load()

    commission_percent = _commission_percent(payment_settings, booking.arrival_date)
    commission = _round(rental_base * commission_percent / Decimal('100'))

    high_season = _is_high_season(payment_settings, booking.arrival_date)
    if high_season:
        charge_commission_vat = True
    elif is_platform:
        charge_commission_vat = payment_settings.charge_vat_on_low_season_platform_commission
    else:
        charge_commission_vat = payment_settings.charge_vat_on_low_season_direct_commission
    commission_vat = _round(commission * payment_settings.vat_rate_percent / Decimal('100')) if charge_commission_vat else ZERO

    platform_fee_vat = _round(platform_fee * payment_settings.vat_rate_percent / Decimal('100')) if is_platform else ZERO

    management_fee = _round(_management_fee(payment_settings, booking))

    owner_balance = rental_base - commission - commission_vat - platform_fee_vat - management_fee

    return {
        'available': True,
        'reason': None,
        'rental_base': rental_base,
        'commission_percent': commission_percent,
        'commission': commission,
        'commission_vat': commission_vat,
        'platform_fee': platform_fee,
        'platform_fee_vat': platform_fee_vat,
        'management_fee': management_fee,
        'owner_balance': owner_balance,
        'due_date': _due_date(payment_settings, owner, booking.arrival_date),
        'is_regular': owner.is_paid_regularly,
    }
