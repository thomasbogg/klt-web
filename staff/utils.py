from django.utils import timezone

import env_settings

# PIMS' own tab bar, minus 'Open Enquiry' - every klt-web Booking already has committed dates
# from the moment it's created, so that stage is never reachable from this data (confirmed with
# Thomas 2026-08-24, dropped rather than kept for visual fidelity).
STAGE_TABS = (
    'Provisional Booking', 'Confirmed Booking', 'Holiday started', 'Holiday ended', 'Closed',
)

# Mirrors the "deliberately excluded from both status tuples" comment in env_settings.py -
# every status that frees the calendar, i.e. a dead/cancelled booking.
CLOSED_STATUSES = (
    'Cancelled by guest', 'Cancelled by platform', 'Cancelled by staff', 'Payment failed',
    'Hold expired', 'Payment received - needs review',
)

# The subset of CLOSED_STATUSES a staffer can revive from the booking detail page's "Uncancel
# booking" button - deliberately excludes 'Cancelled by platform' (the platform is the source of
# truth there; reviving it here without the platform agreeing just means the next iCal sync
# re-cancels it) and the payment-failure statuses (those aren't "cancelled" in the sense Thomas
# asked about - a fresh booking attempt is the normal recovery path for those, not a revival).
REVIVABLE_STATUSES = ('Cancelled by guest', 'Cancelled by staff')

# Every enquiry_status string the rest of the app actually gives meaning to, grouped for the
# booking detail page's Outcome/status dropdown (previously free text - a typo there wouldn't
# error, it would just silently stop matching VALID_BOOKING_STATUSES/PROVISIONAL_BOOKING_STATUSES/
# CLOSED_STATUSES and so stop blocking the calendar or bucketing correctly; confirmed at least one
# real booking has already drifted this way - 'Booking cancelled' instead of one of the 'Cancelled
# by ...' strings - so the dropdown/view still has to tolerate a value outside this list rather
# than assume it can't happen). Grouped to match how staff already think about status via the
# STAGE_TABS/STATUS_BUCKETS split above.
ENQUIRY_STATUS_GROUPS = (
    ('Valid', env_settings.VALID_BOOKING_STATUSES),
    ('Provisional', env_settings.PROVISIONAL_BOOKING_STATUSES),
    ('Closed', CLOSED_STATUSES),
)
ENQUIRY_STATUSES = tuple(status for _label, statuses in ENQUIRY_STATUS_GROUPS for status in statuses)


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
    ('vacuum_cleaner', 'Vacuum cleaner'),
    ('mop_and_bucket', 'Mop & bucket'),
    ('clothes_horse', 'Clothes horse'),
    ('coffee_machine', 'Coffee machine'),
)

# properties.models.Owner's boolean fields have no model-level default (a genuine choice always
# has to be made, unlike e.g. Manager's spare contact channels), so the quick-add panel on the
# Create Property page surfaces all of them rather than guessing - same (field, label) pattern as
# AMENITY_BOOLEAN_FIELDS above, driving both the panel's checkboxes and StaffQuickAddView.
OWNER_BOOLEAN_FIELDS = (
    ('default_clean', 'Cleans included by default'),
    ('default_meet_greet', 'Meet & greet included by default'),
    ('takes_euros', 'Takes euros'),
    ('takes_pounds', 'Takes pounds'),
    ('cleans_are_invoiced', 'Cleans are invoiced'),
    ('rental_commissions_are_invoiced', 'Rental commissions are invoiced'),
    ('is_paid_regularly', 'Paid on a regular schedule'),
)

# properties.models.LocationSpec's boolean fields, same (field, label) pattern as
# AMENITY_BOOLEAN_FIELDS - drives the Location detail page's checkbox grid.
LOCATION_SPEC_BOOLEAN_FIELDS = (
    ('sea_views', 'Sea views'),
    ('pool', 'Pool'),
    ('lift', 'Lift'),
    ('beachfront', 'Beachfront'),
    ('wifi', 'WiFi'),
    ('private_parking', 'Private parking'),
    ('street_parking', 'Street parking'),
    ('gym', 'Gym'),
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
    workflow yet, see the staff booking detail page plan). Never returns 'Open Enquiry' - every
    Booking row already has committed dates from the moment it's created - which is also why that
    stage isn't in STAGE_TABS at all any more (see its own comment)."""
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
