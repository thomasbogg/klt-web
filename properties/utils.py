from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal('0.01')
WEEKLY_DISCOUNT_MIN_NIGHTS = 7
FREE_ADULTS = 2
DEFAULT_MONTHLY_DISCOUNT_MIN_NIGHTS = 28


def get_stay_total_price(property, start_date, end_date, guests=None, monthly_discount_min_nights=None):
    """Nightly rates for the stay, less weekly/monthly/last-minute discounts, plus extra-guest
    charges (undiscounted), or None if any night is unpriced.

    monthly_discount_min_nights is a site-wide setting (BookingSettings), not per-property,
    so it's passed in by the caller rather than read off each Price row.
    """
    if not start_date or not end_date or end_date <= start_date:
        return None

    if monthly_discount_min_nights is None:
        monthly_discount_min_nights = DEFAULT_MONTHLY_DISCOUNT_MIN_NIGHTS

    nightly_prices = {}
    for price in property.prices.filter(start_date__lte=end_date, end_date__gte=start_date):
        night = max(price.start_date, start_date)
        last_night = min(price.end_date, end_date - timedelta(days=1))
        while night <= last_night:
            if night not in nightly_prices:
                nightly_prices[night] = price
            night += timedelta(days=1)

    total_nights = (end_date - start_date).days
    if len(nightly_prices) < total_nights:
        return None

    is_weekly_stay = total_nights >= WEEKLY_DISCOUNT_MIN_NIGHTS
    is_monthly_stay = total_nights >= monthly_discount_min_nights
    days_to_arrival = (start_date - date.today()).days
    guests = guests or {}
    extra_adults = max(0, guests.get('adults', 0) - FREE_ADULTS)
    extra_children = guests.get('children', 0)

    basic_total = Decimal('0')
    discount_total = Decimal('0')
    extra_guest_total = Decimal('0')
    for price in nightly_prices.values():
        basic_total += price.rate
        if is_monthly_stay:
            discount_total += price.rate * price.monthly_discount_percent / Decimal('100')
        elif is_weekly_stay:
            discount_total += price.rate * price.weekly_discount_percent / Decimal('100')
        if 0 <= days_to_arrival <= price.last_minute_discount_days:
            discount_total += price.rate * price.last_minute_discount_percent / Decimal('100')
        extra_guest_total += extra_adults * price.extra_adult_rate + extra_children * price.extra_child_rate

    return (basic_total - discount_total + extra_guest_total).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def pretty_title(title):
    return ' '.join(word.capitalize() if word.lower() not in ['de', 'do', 'da', 'dos', 'das', 'e'] else word.lower() for word in title.split())


def location_image_path(instance, filename):
    return f'properties/locations/{instance.location.slug}/{filename}'


def property_image_path(instance, filename):
    return f'properties/{instance.property.slug}/{filename}'