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


def scale_rate(rate, percent):
    """rate * (1 + percent/100), rounded to the nearest whole euro (matches this project's
    pricing convention - every Price.rate in the DB is a whole number) then re-quantized to the
    field's 2 decimal places."""
    factor = Decimal('1') + (Decimal(percent) / Decimal('100'))
    return (Decimal(rate) * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(TWO_PLACES)


def gross_up_for_commission(direct_rate, commission_percent):
    """What to list on a platform charging commission_percent so its cut still nets back
    direct_rate - direct_rate / (1 - commission_percent/100), rounded to the nearest cent. Used
    only by the Rate Card's "Platform rates" popup (informational - never written anywhere); a
    flat estimate that deliberately doesn't model per-platform complexity beyond one % (see
    Platform.commission_percent's own docstring for the Booking.com Genius/payment-charge
    caveat). Returns None if there's no commission_percent to gross up with, or it's 100%+
    (division would be zero or negative)."""
    if commission_percent is None:
        return None
    factor = Decimal('1') - (Decimal(commission_percent) / Decimal('100'))
    if factor <= 0:
        return None
    return (Decimal(direct_rate) / factor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def build_price_bulk_plan(mode, percent, properties=None, year=None, source_year=None,
                           target_year=None, scale_extra_rates=False):
    """Computes what Price.bulk-adjust-a-year or clone-a-year-forward would do, without writing
    anything - the logic behind staff/views.py::StaffPriceBulkToolsView. Returns a dict: 'mode',
    'scale_extra_rates', 'updates' (existing Price rows plus their new field values, adjust mode),
    'creates' (unsaved Price instances, clone mode), 'skipped' (clone rows whose shifted dates
    already overlap an existing row in the target year - reported rather than raised, so one
    collision doesn't abort the whole batch) and 'no_data' (properties explicitly selected that
    turned out to have no price rows at all for the relevant year - otherwise they'd just be
    silently absent from the plan with no indication why, since a property with nothing to adjust
    or clone contributes zero rows either way).

    Properties with no booking_company are excluded from scope entirely, not just from 'no_data':
    such a property isn't sold through our own booking flow at all (see Property.booking_company/
    ManagementCompany.bookable_on_website), so it routinely has no current-year pricing and
    flagging that as 'missing data' would just be noise, per Thomas 2026-09-04. An explicit
    selection that includes one is silently narrowed rather than reported - the staff picker
    (staff/views.py::StaffPriceBulkToolsView) doesn't even list them, so this only matters for a
    stale/manually-crafted selection."""
    from properties.models import Price

    properties = [p for p in properties if p.booking_company_id] if properties else None
    plan = {
        'mode': mode, 'scale_extra_rates': scale_extra_rates,
        'updates': [], 'creates': [], 'skipped': [], 'no_data': [],
    }

    if properties:
        relevant_year = year if mode == 'adjust' else source_year
        priced_property_ids = set(
            Price.objects.filter(property__in=properties, start_date__year=relevant_year)
            .values_list('property_id', flat=True).distinct()
        )
        plan['no_data'] = [p for p in properties if p.pk not in priced_property_ids]

    if mode == 'adjust':
        qs = Price.objects.filter(
            start_date__year=year,
        ).exclude(property__booking_company__isnull=True).select_related('property')
        if properties:
            qs = qs.filter(property__in=properties)
        for price in qs.order_by('property__title', 'start_date'):
            new_extra_adult = scale_rate(price.extra_adult_rate, percent) if scale_extra_rates else price.extra_adult_rate
            new_extra_child = scale_rate(price.extra_child_rate, percent) if scale_extra_rates else price.extra_child_rate
            plan['updates'].append({
                'price': price,
                'old_rate': price.rate, 'new_rate': scale_rate(price.rate, percent),
                'old_extra_adult_rate': price.extra_adult_rate, 'new_extra_adult_rate': new_extra_adult,
                'old_extra_child_rate': price.extra_child_rate, 'new_extra_child_rate': new_extra_child,
            })
    else:
        offset = target_year - source_year
        qs = Price.objects.filter(
            start_date__year=source_year,
        ).exclude(property__booking_company__isnull=True).select_related('property')
        if properties:
            qs = qs.filter(property__in=properties)
        for price in qs.order_by('property__title', 'start_date'):
            new_start = price.start_date.replace(year=price.start_date.year + offset)
            new_end = price.end_date.replace(year=price.end_date.year + offset)
            if Price.overlapping(price.property_id, new_start, new_end).exists():
                plan['skipped'].append({'price': price, 'new_start': new_start, 'new_end': new_end})
                continue
            new_extra_adult = scale_rate(price.extra_adult_rate, percent) if scale_extra_rates else price.extra_adult_rate
            new_extra_child = scale_rate(price.extra_child_rate, percent) if scale_extra_rates else price.extra_child_rate
            new_price = Price(
                property=price.property,
                start_date=new_start,
                end_date=new_end,
                rate=scale_rate(price.rate, percent),
                weekly_discount_percent=price.weekly_discount_percent,
                last_minute_discount_percent=price.last_minute_discount_percent,
                last_minute_discount_days=price.last_minute_discount_days,
                monthly_discount_percent=price.monthly_discount_percent,
                extra_adult_rate=new_extra_adult,
                extra_child_rate=new_extra_child,
            )
            plan['creates'].append({'price': new_price, 'old_rate': price.rate, 'new_rate': new_price.rate})
    return plan


def apply_price_bulk_plan(plan):
    """Writes a plan from build_price_bulk_plan(). Returns the number of rows updated/created."""
    from properties.models import Price

    if plan['mode'] == 'adjust':
        rows = []
        for u in plan['updates']:
            row = u['price']
            row.rate = u['new_rate']
            row.extra_adult_rate = u['new_extra_adult_rate']
            row.extra_child_rate = u['new_extra_child_rate']
            rows.append(row)
        Price.objects.bulk_update(rows, ['rate', 'extra_adult_rate', 'extra_child_rate'], batch_size=200)
        return len(rows)
    else:
        Price.objects.bulk_create([c['price'] for c in plan['creates']], batch_size=200)
        return len(plan['creates'])


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