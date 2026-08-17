from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal('0.01')
WEEKLY_DISCOUNT_MIN_NIGHTS = 7


def get_stay_total_price(property, start_date, end_date):
    """Nightly rates for the stay, less weekly/monthly/last-minute discounts, or None if any night is unpriced."""
    if not start_date or not end_date or end_date <= start_date:
        return None

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
    days_to_arrival = (start_date - date.today()).days

    basic_total = Decimal('0')
    discount_total = Decimal('0')
    for price in nightly_prices.values():
        basic_total += price.rate
        if total_nights >= price.monthly_discount_min_nights:
            discount_total += price.rate * price.monthly_discount_percent / Decimal('100')
        elif is_weekly_stay:
            discount_total += price.rate * price.weekly_discount_percent / Decimal('100')
        if 0 <= days_to_arrival <= price.last_minute_discount_days:
            discount_total += price.rate * price.last_minute_discount_percent / Decimal('100')

    return (basic_total - discount_total).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def pretty_title(title):
    return ' '.join(word.capitalize() if word.lower() not in ['de', 'do', 'da', 'dos', 'das', 'e'] else word.lower() for word in title.split())


def location_image_path(instance, filename):
    return f'properties/locations/{instance.location.slug}/{filename}'


def property_image_path(instance, filename):
    return f'properties/{instance.property.slug}/{filename}'