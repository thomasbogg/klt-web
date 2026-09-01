import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal('0.01')
WEEKLY_DISCOUNT_MIN_NIGHTS = 7
FREE_ADULTS = 2
DEFAULT_MONTHLY_DISCOUNT_MIN_NIGHTS = 28


def get_stay_total_price(property, start_date, end_date, guests=None, monthly_discount_min_nights=None):
    """Nightly rates for the stay, split into components rather than combined into one figure -
    {'basic_total', 'discount_total', 'extra_guest_total'} (all Decimal), or None if any night is
    unpriced. discount_total is a euro amount rather than a percentage: a stay can span more than
    one Price row (different seasonal periods with potentially different discount percentages on
    different nights), so there's no single well-defined "discount %" for the whole stay in
    general - only a well-defined aggregate euro total. The final rental total a caller should
    actually charge/store is basic_total - discount_total + extra_guest_total.

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

    return {
        'basic_total': basic_total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        'discount_total': discount_total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        'extra_guest_total': extra_guest_total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
    }


def pretty_title(title):
    """Title-cases a location/property name, lowercasing Portuguese connector words - except the
    unit code after a trailing ' - ' (e.g. 'CLUBE DO MONACO - AE', 'PARQUE DA CORCOVADA - 43-G'),
    which is kept exactly as stored rather than run through capitalize() - that would turn 'AE'
    into 'Ae' and '43-G' into '43-g', since capitalize() only uppercases a string's very first
    character. A non-code suffix (e.g. 'QUINTA DA BARRACUDA - Penthouse') is unaffected: it's
    already stored mixed-case in the DB rather than all-caps like every real unit code, so the
    all-upper check below routes it through the normal word-by-word capitalization instead."""
    def capitalize_words(text):
        return ' '.join(word.capitalize() if word.lower() not in ['de', 'do', 'da', 'dos', 'das', 'e'] else word.lower() for word in text.split())

    if ' - ' in title:
        location_part, _, code_part = title.rpartition(' - ')
        code_display = code_part if code_part == code_part.upper() else capitalize_words(code_part)
        return f'{capitalize_words(location_part)} - {code_display}'
    return capitalize_words(title)


def natural_sort_key(text):
    """Splits text into digit/non-digit chunks, comparing digit chunks by numeric value - so a
    unit code list sorts '2', '4', '8', '19' in that order rather than plain string order, which
    would put '19' before '2'. Used to order properties by door code within a location."""
    return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in re.split(r'(\d+)', text)]


def location_image_path(instance, filename):
    return f'properties/locations/{instance.location.slug}/{filename}'


def property_image_path(instance, filename):
    return f'properties/{instance.property.slug}/{filename}'