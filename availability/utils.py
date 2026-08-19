import calendar as calendar_module
from datetime import date, datetime

from bookings.models import Booking, BookingSettings
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


def get_property_calendar(property, months=12, start=None):
    """Build a month-by-month availability grid for a property.

    Returns a list of dicts, one per month, each with a 'label' and
    'weeks' (Monday-first, padded with None for days outside the month).
    Each day cell is a dict with 'day', 'status'
    ('past'/'available'/'provisional'/'booked') and 'is_today'.
    """
    start = start or date.today()
    range_start = date(start.year, start.month, 1)

    end_year, end_month = range_start.year, range_start.month + months
    while end_month > 12:
        end_month -= 12
        end_year += 1
    range_end = date(end_year, end_month, 1)

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

    def status_for(day):
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