from datetime import timedelta


def get_stay_total_price(property, start_date, end_date):
    """Sum nightly rates covering every night of the stay, or None if any night is unpriced."""
    if not start_date or not end_date or end_date <= start_date:
        return None

    nightly_rates = {}
    for price in property.prices.filter(start_date__lte=end_date, end_date__gte=start_date):
        night = max(price.start_date, start_date)
        last_night = min(price.end_date, end_date - timedelta(days=1))
        while night <= last_night:
            if price.is_special_rate or night not in nightly_rates:
                nightly_rates[night] = price.rate
            night += timedelta(days=1)

    total_nights = (end_date - start_date).days
    if len(nightly_rates) < total_nights:
        return None
    return sum(nightly_rates.values())


def pretty_title(title):
    return ' '.join(word.capitalize() if word.lower() not in ['de', 'do', 'da', 'dos', 'das', 'e'] else word.lower() for word in title.split())


def location_image_path(instance, filename):
    return f'properties/locations/{instance.location.slug}/{filename}'


def property_image_path(instance, filename):
    return f'properties/{instance.property.slug}/{filename}'