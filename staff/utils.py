from django.utils import timezone

import env_settings

# PIMS' own six-stage tab bar - kept as the full list even though 'Open Enquiry' is never
# reachable from klt-web's data (see booking_stage() below), for visual fidelity in the tab row.
STAGE_TABS = (
    'Open Enquiry', 'Provisional Booking', 'Confirmed Booking',
    'Holiday started', 'Holiday ended', 'Closed',
)

# Mirrors the "deliberately excluded from both status tuples" comment in env_settings.py -
# every status that frees the calendar, i.e. a dead/cancelled booking.
CLOSED_STATUSES = (
    'Cancelled by guest', 'Cancelled by platform', 'Payment failed', 'Hold expired',
    'Payment received - needs review',
)


# Home page reservation-list status filter options, in dropdown order - 'Valid' is the default
# (see StaffHomeView), 'All' bypasses bucket filtering entirely rather than being a real
# status_bucket() return value (that function only ever returns the first three).
STATUS_BUCKETS = ('Valid', 'Invalid', 'Ended', 'All')

# Guest list surname-index letters, mirroring PIMS' "Surname begins with" row.
GUEST_LETTERS = tuple('ABCDEFGHIJKLMNOPQRSTUVWXYZ')

# properties.models.Amenity's ~30 boolean fields, in model declaration order, paired with a
# display label - drives both the property detail page's checkbox grid (looped via the
# staff_extras get_attr template filter, rather than hand-writing 30 near-identical <label>
# blocks) and StaffPropertyDetailView._update_amenities' save loop, so the two can't drift apart.
AMENITY_BOOLEAN_FIELDS = (
    ('bathtub_and_shower', 'Bathtub & shower'),
    ('walk_in_shower', 'Walk-in shower'),
    ('hairdryer', 'Hairdryer'),
    ('washing_machine', 'Washing machine'),
    ('dryer', 'Dryer'),
    ('iron', 'Iron'),
    ('ironing_board', 'Ironing board'),
    ('wifi', 'WiFi'),
    ('tv', 'TV'),
    ('iptv', 'IPTV'),
    ('air_conditioning', 'Air conditioning'),
    ('heating', 'Heating'),
    ('kitchen', 'Kitchen'),
    ('oven', 'Oven'),
    ('hob', 'Hob'),
    ('toaster', 'Toaster'),
    ('kettle', 'Kettle'),
    ('microwave', 'Microwave'),
    ('fridge', 'Fridge'),
    ('freezer', 'Freezer'),
    ('dishwasher', 'Dishwasher'),
    ('barbecue', 'Barbecue'),
    ('pool', 'Pool'),
    ('hot_tub', 'Hot tub'),
    ('garden', 'Garden'),
    ('air_conditioning_in_bedrooms', 'A/C in bedrooms'),
    ('air_conditioning_in_living_room', 'A/C in living room'),
    ('heating_in_bedrooms', 'Heating in bedrooms'),
    ('heating_in_living_room', 'Heating in living room'),
    ('sofa_bed', 'Sofa bed'),
    ('safe', 'Safe'),
)


def status_bucket(stage):
    """Valid = still a real, active/upcoming/ongoing booking (Provisional/Confirmed/Holiday
    started); Invalid = a dead/cancelled status (see CLOSED_STATUSES); Ended = a valid booking
    whose holiday has already finished."""
    if stage == 'Closed':
        return 'Invalid'
    if stage == 'Holiday ended':
        return 'Ended'
    return 'Valid'


def booking_stage(booking):
    """Maps a Booking to one of STAGE_TABS - presentational only (no click-to-transition
    workflow yet, see the staff booking detail page plan). 'Open Enquiry' has no klt-web
    equivalent - every Booking row already has committed dates from the moment it's created -
    so it's never returned here; it stays in STAGE_TABS purely so the tab bar renders complete."""
    if booking.enquiry_status in CLOSED_STATUSES:
        return 'Closed'
    if booking.enquiry_status in env_settings.PROVISIONAL_BOOKING_STATUSES:
        return 'Provisional Booking'

    today = timezone.now().date()
    if booking.departure_date <= today:
        return 'Holiday ended'
    if booking.arrival_date <= today:
        return 'Holiday started'
    return 'Confirmed Booking'


def reservation_rows(queryset, status_filter):
    """Applies one of STATUS_BUCKETS to a Booking queryset already scoped to whatever the caller
    cares about (a property, a guest, everything) and returns [{'booking', 'status_label'}, ...]
    ordered by arrival_date. Shared by StaffHomeView and StaffGuestDetailView so the Valid/
    Invalid/Ended/All rules (see STATUS_BUCKETS above) live in exactly one place. 'Invalid' needs
    its own explicit filter and 'All' needs none at all, since .holding() (the base for Valid/
    Ended) never returns a CLOSED_STATUSES booking to begin with."""
    if status_filter == 'Invalid':
        queryset = queryset.filter(enquiry_status__in=CLOSED_STATUSES)
    elif status_filter != 'All':
        queryset = queryset.holding()
    queryset = queryset.select_related('property', 'guest').order_by('arrival_date')

    rows = []
    for booking in queryset:
        stage = booking_stage(booking)
        bucket = status_bucket(stage)
        if status_filter != 'All' and bucket != status_filter:
            continue
        # Bucketing collapses every dead/cancelled status into one "Closed" stage - not useful
        # on its own, so Invalid rows show the real reason (e.g. "Payment failed") instead.
        status_label = booking.enquiry_status if bucket == 'Invalid' else stage
        rows.append({'booking': booking, 'status_label': status_label})
    return rows


def next_step_hint(booking, charge, balance_payment):
    """A short computed "what's next" line - not PIMS' separate configurable reminders system
    (deferred, see the staff booking detail page plan), just a direct read of existing state."""
    if booking.enquiry_status in CLOSED_STATUSES:
        return "No action needed - booking is closed."
    if booking.enquiry_status in env_settings.PROVISIONAL_BOOKING_STATUSES:
        return "Awaiting deposit payment."
    if balance_payment is not None and balance_payment.status != 'paid' and charge and charge.balance_due_date:
        return f"Balance due on {charge.balance_due_date}."

    today = timezone.now().date()
    if booking.arrival_date > today:
        return "Nothing to do yet - waiting for the stay to start."
    if booking.departure_date > today:
        return "Guest is currently on-site."
    return "Stay has ended."
