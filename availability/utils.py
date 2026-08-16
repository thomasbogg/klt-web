import calendar as calendar_module
from datetime import date

from bookings.models import Booking
from env_settings import PROVISIONAL_BOOKING_STATUSES, VALID_BOOKING_STATUSES
from properties.models import Location, Property

def full_toolbar_context():
    return {
        'toolbar_date_picker_start_name': 'start',
        'toolbar_date_picker_end_name': 'end',
        'toolbar_guests_picker_name': 'guests',
        'toolbar_guests_picker_groups': [
            ('adults', '2', '1', '10'),
            ('children', '0', '0', '10'),
            ('infants', '0', '0', '10'),
        ],
        'toolbar_location_picker_name': 'location',
        'toolbar_location_picker_list': Location.objects.order_by('title'),
        'toolbar_bedrooms_picker_name': 'bedrooms',
        'toolbar_bedrooms_picker_groups': [
            ('bedrooms', '1', '1', '3')
        ],
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

    bookings = Booking.objects.filter(
        property=property,
        arrival_date__lt=range_end,
        departure_date__gt=range_start,
        enquiry_status__in=VALID_BOOKING_STATUSES + PROVISIONAL_BOOKING_STATUSES,
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