from datetime import date
from decimal import Decimal

from django.db.models import Max, Min, Q

import env_settings
from bookings.models import AirportTransfer, Booking, Extra
from env_settings import VALID_BOOKING_STATUSES
from staff.utils import last_day_of_month

ZERO = Decimal('0')
TWO_PLACES = Decimal('0.01')

# Column order matches the reference "Monthly Business Report" workbook's own Revenue sheet
# exactly (Total, then one column-group per source) - Total is inserted first in
# monthly_revenue_rows() below, not listed here, since it's a derived sum rather than a booking
# grouping of its own.
REVENUE_GROUPS = ('Direct',) + env_settings.PLATFORMS


def _round(amount):
    return Decimal(amount).quantize(TWO_PLACES)


def _group_for_booking(booking):
    return booking.enquiry_source if booking.enquiry_source in env_settings.PLATFORMS else 'Direct'


def _revenue_totals_for_month(year, month):
    """{group: {'paid_by_guest': Decimal, 'rcvd_by_klt': Decimal}} for every REVENUE_GROUPS entry,
    summed across every real guest booking (is_owner=False) arriving in this month. Owner stays
    are excluded entirely - they generate no rental revenue by definition (same reasoning as
    bookings/payouts.py::compute_owner_payout's own owner-stay short-circuit), matching the
    reference workbook's own Revenue sheet, which has no Owner-stay column at all (unlike the
    Stays sheet below, which does support an owner-stay toggle).

    Direct bookings: 'paid by guest' and 'received by KLT' are identical - the guest pays KLT
    directly, no platform intermediary (Charge.total_rental for both). Platform bookings: sourced
    from PlatformPayout.gross_amount ('Total the guest paid the platform') and .payout_amount
    ('Net amount actually paid out to us') respectively - see that model's own docstring. A
    booking missing the relevant money record yet (no Charge, or a PlatformPayout row with a null
    figure) is skipped for whichever side of paid/received it's missing, same
    "not yet available, don't guess" convention as compute_owner_payout()."""
    start = date(year, month, 1)
    end = last_day_of_month(start)
    bookings = Booking.objects.filter(
        is_owner=False, enquiry_status__in=VALID_BOOKING_STATUSES, arrival_date__range=(start, end),
    ).select_related('charges', 'platform_payout')

    totals = {group: {'paid_by_guest': ZERO, 'rcvd_by_klt': ZERO} for group in REVENUE_GROUPS}
    for booking in bookings:
        group = _group_for_booking(booking)
        if group == 'Direct':
            charge = getattr(booking, 'charges', None)
            if charge is None or charge.basic_rental is None:
                continue
            totals[group]['paid_by_guest'] += charge.total_rental
            totals[group]['rcvd_by_klt'] += charge.total_rental
        else:
            payout = getattr(booking, 'platform_payout', None)
            if payout is None:
                continue
            if payout.gross_amount is not None:
                totals[group]['paid_by_guest'] += payout.gross_amount
            if payout.payout_amount is not None:
                totals[group]['rcvd_by_klt'] += payout.payout_amount
    return totals


def _percent(part, whole):
    """Works for both Decimal (money) and plain int (counts) inputs - always via Decimal division
    rather than Python's own `/`, which would return a float for two ints and risks the usual
    binary-float-imprecision artifacts once that float is fed into Decimal.quantize()."""
    return _round(Decimal(part) / Decimal(whole) * 100) if whole else ZERO


def monthly_revenue_rows(year):
    """One row per calendar month of `year` - Total plus each REVENUE_GROUPS entry, each carrying
    paid_by_guest/rcvd_by_klt totals, that group's % share of the month's Total, and a
    year-over-year delta (this year's figure minus the same calendar month the year before) -
    mirrors the reference workbook's Revenue sheet exactly (Tot/%/Lst Yr per money column, per
    group). 'Lst Yr' is a genuine delta (can be negative), not the prior year's raw figure, same
    as the workbook."""
    rows = []
    for month in range(1, 13):
        this_year = _revenue_totals_for_month(year, month)
        last_year = _revenue_totals_for_month(year - 1, month)

        this_year['Total'] = {
            'paid_by_guest': sum((v['paid_by_guest'] for v in this_year.values()), ZERO),
            'rcvd_by_klt': sum((v['rcvd_by_klt'] for v in this_year.values()), ZERO),
        }
        last_year['Total'] = {
            'paid_by_guest': sum((v['paid_by_guest'] for v in last_year.values()), ZERO),
            'rcvd_by_klt': sum((v['rcvd_by_klt'] for v in last_year.values()), ZERO),
        }

        total_paid = this_year['Total']['paid_by_guest']
        total_rcvd = this_year['Total']['rcvd_by_klt']
        groups = {}
        for group in ('Total',) + REVENUE_GROUPS:
            paid = this_year[group]['paid_by_guest']
            rcvd = this_year[group]['rcvd_by_klt']
            groups[group] = {
                'paid_by_guest': paid,
                'paid_by_guest_pct': _percent(paid, total_paid),
                'paid_by_guest_delta': paid - last_year[group]['paid_by_guest'],
                'rcvd_by_klt': rcvd,
                'rcvd_by_klt_pct': _percent(rcvd, total_rcvd),
                'rcvd_by_klt_delta': rcvd - last_year[group]['rcvd_by_klt'],
            }
        rows.append({'month': date(year, month, 1), 'groups': groups})
    return rows


def _stays_totals_for_month(year, month):
    """{group: {'arrivals': int, 'nights': int}} for every REVENUE_GROUPS entry (guest stays, by
    platform) plus 'Owner' (every owner stay that month, ungrouped by platform - an owner stay
    isn't sourced from a booking platform, so there's nothing to split it by) - unlike
    _revenue_totals_for_month, owner stays are NOT excluded here, since this sheet covers both
    (see monthly_stays_rows()'s own docstring for how the two get combined). Nights =
    departure_date - arrival_date in days, same convention as staff.reports.py's own per-booking
    'nights' figure - counted regardless of whether Charge/PlatformPayout money records exist
    yet, unlike the revenue figures above, since a stay happened (and used a night)
    independently of whether it's been priced/reconciled yet (owner stays never have either)."""
    start = date(year, month, 1)
    end = last_day_of_month(start)
    bookings = Booking.objects.filter(enquiry_status__in=VALID_BOOKING_STATUSES, arrival_date__range=(start, end))
    totals = {group: {'arrivals': 0, 'nights': 0} for group in REVENUE_GROUPS + ('Owner',)}
    for booking in bookings:
        group = 'Owner' if booking.is_owner else _group_for_booking(booking)
        totals[group]['arrivals'] += 1
        totals[group]['nights'] += (booking.departure_date - booking.arrival_date).days
    return totals


def monthly_stays_rows(year, include_owner=False):
    """One row per calendar month of `year` - Total plus each REVENUE_GROUPS entry (Direct/
    Airbnb/Booking.com/Vrbo), each carrying Arrivals/Nights counts, that group's % share of the
    month's Total, and a year-over-year delta - same shape as monthly_revenue_rows() above with
    Arrivals/Nights in place of Paid by Guest/Rcvd by KLT. One merged sheet rather than the
    reference workbook's separate Guest Stays/All Stays sheets, per Thomas 2026-08-30: with
    include_owner=False this reads as Guest Stays (Owner excluded from both the table and Total);
    with include_owner=True it reads as All Stays instead (an Owner column appears, and Total
    grows to include it) - unlike the Direct/Airbnb/Booking.com/Vrbo split, which is always fully
    summed into Total regardless of which of those four columns a caller chooses to *display*,
    the Owner toggle genuinely changes what Total means, not just what's shown - unifying the two
    reference sheets was only possible because Owner has nowhere else to be split by."""
    rows = []
    for month in range(1, 13):
        this_year = _stays_totals_for_month(year, month)
        last_year = _stays_totals_for_month(year - 1, month)

        total_groups = REVENUE_GROUPS + (('Owner',) if include_owner else ())
        this_year['Total'] = {
            'arrivals': sum(this_year[g]['arrivals'] for g in total_groups),
            'nights': sum(this_year[g]['nights'] for g in total_groups),
        }
        last_year['Total'] = {
            'arrivals': sum(last_year[g]['arrivals'] for g in total_groups),
            'nights': sum(last_year[g]['nights'] for g in total_groups),
        }

        total_arrivals = this_year['Total']['arrivals']
        total_nights = this_year['Total']['nights']
        display_groups = ('Total',) + (('Owner',) if include_owner else ()) + REVENUE_GROUPS
        groups = {}
        for group in display_groups:
            arrivals = this_year[group]['arrivals']
            nights = this_year[group]['nights']
            groups[group] = {
                'arrivals': arrivals,
                'arrivals_pct': _percent(arrivals, total_arrivals),
                'arrivals_delta': arrivals - last_year[group]['arrivals'],
                'nights': nights,
                'nights_pct': _percent(nights, total_nights),
                'nights_delta': nights - last_year[group]['nights'],
            }
        rows.append({'month': date(year, month, 1), 'groups': groups})
    return rows


def _month_range(start_date, end_date):
    """Yields (year, month) from start_date's month through end_date's month, inclusive."""
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        yield year, month
        month += 1
        if month > 12:
            year, month = year + 1, 1


def revenue_trend_rows():
    """One row per calendar month from the earliest to the latest real guest booking's arrival
    date on record (inclusive, spans into already-booked future months too, not capped at
    today) - Total paid_by_guest/rcvd_by_klt only, no group breakdown or year-over-year delta
    (those stay table-only concepts on the Revenue tab itself) - a single continuous
    "since records began" series for the growth-over-time chart, per Thomas 2026-08-30. Empty
    list if there's no guest booking data at all yet."""
    bounds = Booking.objects.filter(
        is_owner=False, enquiry_status__in=VALID_BOOKING_STATUSES,
    ).aggregate(earliest=Min('arrival_date'), latest=Max('arrival_date'))
    if bounds['earliest'] is None:
        return []

    rows = []
    for year, month in _month_range(bounds['earliest'], bounds['latest']):
        totals = _revenue_totals_for_month(year, month)
        rows.append({
            'month': date(year, month, 1),
            'paid_by_guest': sum((v['paid_by_guest'] for v in totals.values()), ZERO),
            'rcvd_by_klt': sum((v['rcvd_by_klt'] for v in totals.values()), ZERO),
        })
    return rows


def stays_trend_rows(include_owner=False):
    """Same shape as revenue_trend_rows() above but Arrivals/Nights, honouring the Stays tab's
    own include_owner toggle so the trend chart never shows a different scope than the table
    sitting right above it. Bounds are taken across every booking (owner stays included) rather
    than guest-only, since "since records began" should reflect the earliest stay of any kind on
    record, not just guest ones - if the resulting range includes months with only owner stays
    while include_owner=False, those months simply show zero, which is correct, not misleading."""
    bounds = Booking.objects.filter(enquiry_status__in=VALID_BOOKING_STATUSES).aggregate(
        earliest=Min('arrival_date'), latest=Max('arrival_date'),
    )
    if bounds['earliest'] is None:
        return []

    groups = REVENUE_GROUPS + (('Owner',) if include_owner else ())
    rows = []
    for year, month in _month_range(bounds['earliest'], bounds['latest']):
        totals = _stays_totals_for_month(year, month)
        rows.append({
            'month': date(year, month, 1),
            'arrivals': sum(totals[g]['arrivals'] for g in groups),
            'nights': sum(totals[g]['nights'] for g in groups),
        })
    return rows


def _bookings_totals_for_month(year, month):
    """{group: {'bookings': int, 'enquiries': int}} for every REVENUE_GROUPS entry, counted
    across every real guest Booking row (is_owner=False) arriving in this month, mirroring the
    reference workbook's Bookings sheet. 'enquiries' = every such row regardless of status (no
    status filter at all); 'bookings' = only the confirmed subset (VALID_BOOKING_STATUSES). The
    two genuinely diverge for both direct and platform sources, just via different routes: an
    abandoned/expired/failed direct reservation attempt still created a real Booking row that
    never reached 'Booking confirmed' (see env_settings.py's own status-tuple docstrings for the
    full list), and a platform reservation that was later cancelled on the platform itself still
    has its row here too, as 'Cancelled by platform' (bookings/utils.py::sync_ical_link(), when a
    previously-imported UID disappears from that platform's feed) - counting toward Enquiries but
    not Bookings, same as the direct case. Per Thomas 2026-08-30: only a platform enquiry that
    never became a reservation at all (no booking ever made) leaves no row here - that's the one
    genuine invisible case, not cancellations."""
    start = date(year, month, 1)
    end = last_day_of_month(start)
    bookings = Booking.objects.filter(is_owner=False, arrival_date__range=(start, end))
    totals = {group: {'bookings': 0, 'enquiries': 0} for group in REVENUE_GROUPS}
    for booking in bookings:
        group = _group_for_booking(booking)
        totals[group]['enquiries'] += 1
        if booking.enquiry_status in VALID_BOOKING_STATUSES:
            totals[group]['bookings'] += 1
    return totals


def monthly_bookings_rows(year):
    """One row per calendar month of `year` - Total plus each REVENUE_GROUPS entry, each carrying
    Bookings/Enquiries counts, that group's % share of the month's Total, and a year-over-year
    delta - same shape as monthly_revenue_rows()/monthly_stays_rows() above, mirroring the
    reference workbook's Bookings sheet."""
    rows = []
    for month in range(1, 13):
        this_year = _bookings_totals_for_month(year, month)
        last_year = _bookings_totals_for_month(year - 1, month)

        this_year['Total'] = {
            'bookings': sum(v['bookings'] for v in this_year.values()),
            'enquiries': sum(v['enquiries'] for v in this_year.values()),
        }
        last_year['Total'] = {
            'bookings': sum(v['bookings'] for v in last_year.values()),
            'enquiries': sum(v['enquiries'] for v in last_year.values()),
        }

        total_bookings = this_year['Total']['bookings']
        total_enquiries = this_year['Total']['enquiries']
        groups = {}
        for group in ('Total',) + REVENUE_GROUPS:
            bookings_count = this_year[group]['bookings']
            enquiries_count = this_year[group]['enquiries']
            groups[group] = {
                'bookings': bookings_count,
                'bookings_pct': _percent(bookings_count, total_bookings),
                'bookings_delta': bookings_count - last_year[group]['bookings'],
                'enquiries': enquiries_count,
                'enquiries_pct': _percent(enquiries_count, total_enquiries),
                'enquiries_delta': enquiries_count - last_year[group]['enquiries'],
            }
        rows.append({'month': date(year, month, 1), 'groups': groups})
    return rows


def bookings_trend_rows():
    """Same shape as revenue_trend_rows()/stays_trend_rows() above - Total Bookings/Enquiries
    only, one continuous "since records began" series for the Bookings tab's own growth-over-time
    chart."""
    bounds = Booking.objects.filter(is_owner=False).aggregate(
        earliest=Min('arrival_date'), latest=Max('arrival_date'),
    )
    if bounds['earliest'] is None:
        return []

    rows = []
    for year, month in _month_range(bounds['earliest'], bounds['latest']):
        totals = _bookings_totals_for_month(year, month)
        rows.append({
            'month': date(year, month, 1),
            'bookings': sum(v['bookings'] for v in totals.values()),
            'enquiries': sum(v['enquiries'] for v in totals.values()),
        })
    return rows


# (key, label) - mirrors the reference workbook's Extras sheet column order exactly.
EXTRAS_METRICS = (
    ('airport_transfers', 'Airport Transfers'),
    ('welcome_packs', 'Welcome Packs'),
    ('cots', 'Cots'),
    ('high_chairs', 'High Chairs'),
    ('mid_stay_cleans', 'Mid-stay Cleans'),
    ('late_checkouts', 'Late Check-outs'),
)


def _extras_totals_for_month(year, month):
    """{metric_key: int} for every EXTRAS_METRICS entry, counted across every booking (owner
    stays included this time - unlike Revenue/Stays/Bookings, extras are relevant regardless of
    who's staying, and the reference workbook's own Extras sheet has no Direct/Airbnb/etc split
    to exclude them from) whose ARRIVAL falls in this month and whose status is confirmed
    (VALID_BOOKING_STATUSES). Airport Transfers count by the row itself (many per booking);
    every other metric is a single boolean on that booking's one-to-one Extra, so counting
    Extra rows with that flag set is equivalent to counting bookings with it requested."""
    start = date(year, month, 1)
    end = last_day_of_month(start)
    booking_scope = Q(booking__enquiry_status__in=VALID_BOOKING_STATUSES, booking__arrival_date__range=(start, end))
    return {
        'airport_transfers': AirportTransfer.objects.filter(booking_scope).count(),
        'welcome_packs': Extra.objects.filter(booking_scope, welcome_pack=True).count(),
        'cots': Extra.objects.filter(booking_scope, cot=True).count(),
        'high_chairs': Extra.objects.filter(booking_scope, high_chair=True).count(),
        'mid_stay_cleans': Extra.objects.filter(booking_scope, mid_stay_clean=True).count(),
        'late_checkouts': Extra.objects.filter(booking_scope, late_checkout=True).count(),
    }


def monthly_extras_rows(year):
    """One row per calendar month of `year` - each of the six EXTRAS_METRICS with a Tot count and
    a year-over-year Lst Yr delta only, no % column - unlike every other Monthly-tab sheet, there
    is no Total-vs-group breakdown here to take a share of, just six independent counts side by
    side, matching the reference workbook's own Extras sheet exactly."""
    rows = []
    for month in range(1, 13):
        this_year = _extras_totals_for_month(year, month)
        last_year = _extras_totals_for_month(year - 1, month)
        metrics = {
            key: {'total': this_year[key], 'delta': this_year[key] - last_year[key]}
            for key, _label in EXTRAS_METRICS
        }
        rows.append({'month': date(year, month, 1), 'metrics': metrics})
    return rows


def extras_trend_rows():
    """Same shape as revenue_trend_rows()/stays_trend_rows()/bookings_trend_rows() above - one
    continuous "since records began" series per EXTRAS_METRICS entry, for the Extras tab's own
    growth-over-time chart. Bounds are taken across every confirmed booking (owner included, same
    scope as the table above)."""
    bounds = Booking.objects.filter(enquiry_status__in=VALID_BOOKING_STATUSES).aggregate(
        earliest=Min('arrival_date'), latest=Max('arrival_date'),
    )
    if bounds['earliest'] is None:
        return []

    rows = []
    for year, month in _month_range(bounds['earliest'], bounds['latest']):
        totals = _extras_totals_for_month(year, month)
        row = {'month': date(year, month, 1)}
        row.update(totals)
        rows.append(row)
    return rows
