import calendar as calendar_module
from datetime import date, datetime

from bookings.models import Booking, BookingSettings, SupplementaryPayment
from env_settings import PROVISIONAL_BOOKING_STATUSES, VALID_BOOKING_STATUSES
from properties.models import Location, Property, PropertySpec


def date_string_to_date(date_string):
    return datetime.strptime(date_string, '%d/%m/%Y').date()


def guests_string_to_dict(guests_string):
    guests = {}
    for guest in guests_string.split(','):
        value, key = guest.split()
        guests[key] = int(value)
    return guests


def even_split_guests(properties, guests):
    """`guests` divided as evenly as possible across len(properties) legs - each category
    floor-divided, with any remainder given to the earliest properties. A reasonable starting
    point/estimate, not a real allocation the guest has chosen yet - shared by SearchView's
    combo_suggestions estimated price and MultiPropertyReserveView's own split form default, so
    both start from the same arithmetic instead of two independent implementations."""
    count = len(properties)
    splits = [{} for _ in range(count)]
    for category in ('adults', 'children', 'infants'):
        total = guests.get(category, 0)
        share, remainder = divmod(total, count)
        for index in range(count):
            splits[index][category] = share + (1 if index < remainder else 0)
    return splits


def find_property_combo_suggestions(start_date, end_date, guests):
    """When no single property can seat this whole party, suggest pairs of properties at the same
    location that, booked together, can - e.g. two adjacent Clube do Monaco apartments for a group
    of 8 (2026-09-13, Stage 2 of multi-property booking - see bookings/models.py::
    ReservationGroup for what this feeds into). Only called by SearchView when its own normal
    single-property search comes back empty - a party that already fits one property has no reason
    to be offered two.

    Returns one best-fit suggestion per location (the pair with the smallest combined max_guests
    that still fits, not just any fitting pair - the guest should see the closest match, not an
    arbitrarily large one), as a list of {'location', 'properties': [a, b], 'combined_max_guests'}
    dicts, sorted smallest-combined-capacity first. Only pairs, not larger groups, for now - a
    third+ leg is a straightforward extension of this same function later, not a design change.
    Each property is confirmed individually available for these exact dates - a location listing
    three units doesn't mean any two of them are actually free right now.

    Checks combined max_adults as well as combined max_guests (2026-09-13, found via a real
    example - Clube do Monaco 4 + AE sum to 8 max_guests but only 6 max_adults, so an 8-adult
    party doesn't actually fit either one despite the max_guests total suggesting it does; see
    Booking.clean()'s own comment on the same distinction). This is a necessary check for whether a
    split is even possible, not a guarantee any specific split works - PropertyGuestSplitForm still
    validates the guest's actual chosen split against each leg's own caps."""
    party_size = guests.get('adults', 0) + guests.get('children', 0) + guests.get('infants', 0)
    adults = guests.get('adults', 0)
    suggestions = []
    for location in Location.objects.all():
        candidates = [
            property for property in Property.objects.bookable_on_website()
            .filter(location=location).select_related('specs')
            if getattr(property, 'specs', None)
            and not Booking.objects.overlapping(property, start_date, end_date).exists()
        ]
        best = None
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                combined_max_guests = a.specs.max_guests + b.specs.max_guests
                combined_max_adults = a.specs.max_adults + b.specs.max_adults
                if combined_max_guests < party_size or combined_max_adults < adults:
                    continue
                if best is None or combined_max_guests < best['combined_max_guests']:
                    best = {'location': location, 'properties': [a, b], 'combined_max_guests': combined_max_guests}
        if best is not None:
            suggestions.append(best)
    suggestions.sort(key=lambda suggestion: (suggestion['combined_max_guests'], suggestion['location'].title))
    return suggestions


def full_toolbar_context(start_date=None, end_date=None, guests=None):
    guests = guests or {}
    booking_settings = BookingSettings.load()
    adult_min_age = booking_settings.adult_min_age
    child_min_age = booking_settings.child_min_age
    return {
        'toolbar_date_picker_start_name': 'start',
        'toolbar_date_picker_end_name': 'end',
        'toolbar_date_picker_start_value': start_date.strftime('%d/%m/%Y') if start_date else '',
        'toolbar_date_picker_end_value': end_date.strftime('%d/%m/%Y') if end_date else '',
        'toolbar_guests_picker_name': 'guests',
        'toolbar_guests_picker_groups': [
            ('adults', str(guests.get('adults', 2)), '1', '10', 'Adults', f'Ages {adult_min_age} or above'),
            ('children', str(guests.get('children', 0)), '0', '10', 'Children', f'Ages {child_min_age} – {adult_min_age - 1}'),
            ('infants', str(guests.get('infants', 0)), '0', '10', 'Infants (Cots)', f'Under {child_min_age}'),
        ],
        'toolbar_location_picker_name': 'location',
        'toolbar_location_picker_list': Location.objects.order_by('title'),
        'toolbar_bedrooms_picker_name': 'bedrooms',
        'toolbar_bedrooms_picker_list': PropertySpec.objects.order_by('bedrooms').values_list('bedrooms', flat=True).distinct(),
    }


def calendar_date_range(months, start=None):
    """The (range_start, range_end) a `months`-wide calendar starting at `start` (default today)
    covers - the same month-rollover arithmetic get_property_calendar uses internally, exposed so
    a caller building calendars for many properties at once (see StaffHomeView) can compute it
    once and bulk-fetch bookings for the whole range itself, instead of get_property_calendar
    re-querying per property."""
    start = start or date.today()
    range_start = date(start.year, start.month, 1)

    end_year, end_month = range_start.year, range_start.month + months
    while end_month > 12:
        end_month -= 12
        end_year += 1
    range_end = date(end_year, end_month, 1)
    return range_start, range_end


def get_property_calendar(property, months=12, start=None, mine_range=None, booked_ranges=None, provisional_ranges=None):
    """Build a month-by-month availability grid for a property.

    Returns a list of dicts, one per month, each with a 'label' and
    'weeks' (Monday-first, padded with None for days outside the month).
    Each day cell is a dict with 'day', 'status'
    ('past'/'available'/'provisional'/'booked'/'mine') and 'is_today'.

    mine_range, if given, is an (arrival_date, departure_date) tuple (departure exclusive) that
    takes priority over booked/provisional - a guest viewing their own stay's dates on
    BookingManageDatesView (see bookings/views.py) should see it called out distinctly from a
    generic 'booked' day, even though it's the exact same underlying Booking row.

    booked_ranges/provisional_ranges, if given, are used as-is instead of querying the DB here -
    for a caller (StaffHomeView) that already bulk-fetched bookings for several properties at once
    via calendar_date_range() and is calling this per property just to build the grid.
    """
    range_start, range_end = calendar_date_range(months, start)

    if booked_ranges is None and provisional_ranges is None:
        bookings = Booking.objects.holding().filter(
            property=property,
            arrival_date__lt=range_end,
            departure_date__gt=range_start,
        )
        booked_ranges = [
            (booking.arrival_date, booking.departure_date)
            for booking in bookings if booking.enquiry_status in VALID_BOOKING_STATUSES
        ]
        provisional_ranges = [
            (booking.arrival_date, booking.departure_date)
            for booking in bookings if booking.enquiry_status in PROVISIONAL_BOOKING_STATUSES
        ]
        # A pending date-change's requested new dates hold the calendar the same way a not-yet-paid
        # new reservation does (see SupplementaryPayment.hold_expires_at's own docstring) - folded into
        # the same 'provisional' bucket rather than a distinct status, since both mean the same thing
        # to a browsing guest: not certain yet, but not open either.
        provisional_ranges += list(
            SupplementaryPayment.objects.overlapping_dates(property, range_start, range_end)
            .values_list('new_arrival_date', 'new_departure_date')
        )

    def status_for(day):
        if mine_range and mine_range[0] <= day < mine_range[1]:
            return 'mine'
        if any(arrival <= day < departure for arrival, departure in booked_ranges):
            return 'booked'
        if any(arrival <= day < departure for arrival, departure in provisional_ranges):
            return 'provisional'
        return 'available'

    today = date.today()
    calendar_grid = calendar_module.Calendar(firstweekday=0)  # Monday-first

    month_grids = []
    year, month = range_start.year, range_start.month
    for _ in range(months):
        weeks = []
        for week in calendar_grid.monthdayscalendar(year, month):
            cells = []
            for day_num in week:
                if day_num == 0:
                    cells.append(None)
                    continue
                day = date(year, month, day_num)
                cells.append({
                    'day': day_num,
                    'status': status_for(day) if day >= today else 'past',
                    'is_today': day == today,
                })
            weeks.append(cells)
        month_grids.append({
            'label': date(year, month, 1).strftime('%B %Y'),
            'weeks': weeks,
        })
        month += 1
        if month > 12:
            month = 1
            year += 1

    return month_grids