from datetime import date, datetime, timedelta

from django.db.models import Max
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
    'Cancelled by guest', 'Cancelled by platform', 'Cancelled by staff', 'Cancelled by owner',
    'Payment failed', 'Hold expired', 'Payment received - needs review',
)

# The subset of CLOSED_STATUSES a staffer can revive from the booking detail page's "Uncancel
# booking" button - deliberately excludes 'Cancelled by platform' (the platform is the source of
# truth there; reviving it here without the platform agreeing just means the next iCal sync
# re-cancels it) and the payment-failure statuses (those aren't "cancelled" in the sense Thomas
# asked about - a fresh booking attempt is the normal recovery path for those, not a revival).
REVIVABLE_STATUSES = ('Cancelled by guest', 'Cancelled by staff', 'Cancelled by owner')

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
# has to be made, unlike e.g. ManagementCompany's optional contact roles), so the quick-add panel on the
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

# staff.models.StaffRole's page-access boolean fields, same (field, label) pattern as
# AMENITY_BOOLEAN_FIELDS above - drives both the Settings > Roles tab's checkbox grid and
# staff/permissions.py::staff_page_required's field lookup. 'can_view_properties' also covers
# StaffQuickAddView and StaffIcalSyncView, and 'can_view_settings' covers only the Bookings/
# Extras/People/Payments panels - the Staff and Roles panels are superuser-only regardless of
# role, see StaffSettingsView.
STAFF_PAGE_PERMISSION_FIELDS = (
    ('can_view_home', 'Home'),
    ('can_view_bookings', 'Bookings'),
    ('can_view_guests', 'Guests'),
    ('can_view_properties', 'Properties'),
    ('can_view_locations', 'Locations'),
    ('can_view_settings', 'Settings'),
    ('can_view_cleaning_rota', 'Cleaning rota'),
    ('can_view_checkins_calendar', 'Check-ins calendar'),
    ('can_view_finance', 'Finance'),
    ('can_view_reports', 'Reports'),
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


def sync_cleaning_tasks_for_booking(booking):
    """Keeps CleaningTask rows in sync with Departure.clean/Booking.departure_date (turnover) and
    Extra.mid_stay_clean/mid_stay_clean_date (mid-stay), which stay the actual source of truth -
    see CleaningTask's own docstring (staff/models.py) for why this is signal-driven (staff/
    signals.py) rather than called from one view. Never deletes a task whose status is 'done' -
    only a still-pending task is removed when its source flag/date goes away, so a completed
    clean's record survives even if the underlying flag is later unchecked.

    A cancelled booking (CLOSED_STATUSES) never gets a clean synced at all - any of its pending
    tasks (turnover, mid-stay, or freshen - unlike the two branches below, this isn't scoped to
    one task_type, since a cancelled booking needs none of them) are removed outright, matching
    how cancellation already frees the calendar for booking-overlap purposes (staff/views.py::
    StaffBookingDetailView._cancel_booking). A hard delete, not Freshen's dismiss-with-undo -
    reviving via _uncancel_booking() re-triggers this same signal-driven sync and the Freshen
    property sweep (staff/signals.py), which recreate whatever's still actually due from scratch,
    so there's nothing to preserve for an explicit undo the way a gap-closed Freshen dismissal
    needs one.

    Same blanket-pending-delete treatment when the property's cleaning_company has
    cleans_on_calendar=False (2026-08-28, per Thomas) - a company managing its own cleaning
    separately shouldn't have its properties' tasks cluttering this calendar. Only when a company
    is actually set: an untracked property (cleaning_company=None) is unaffected."""
    from staff.models import CleaningTask

    if booking.enquiry_status in CLOSED_STATUSES:
        CleaningTask.objects.filter(booking=booking, status='pending').delete()
        return

    cleaning_company = booking.property.cleaning_company
    if cleaning_company is not None and not cleaning_company.cleans_on_calendar:
        CleaningTask.objects.filter(booking=booking, status='pending').delete()
        return

    departure = getattr(booking, 'departure', None)
    if departure and departure.clean:
        task, _ = CleaningTask.objects.get_or_create(
            booking=booking, task_type='turnover', defaults={'date': booking.departure_date},
        )
        _sync_task_date(task, booking.departure_date)
    else:
        CleaningTask.objects.filter(booking=booking, task_type='turnover', status='pending').delete()

    extra = getattr(booking, 'extras', None)
    if extra and extra.mid_stay_clean and extra.mid_stay_clean_date:
        task, _ = CleaningTask.objects.get_or_create(
            booking=booking, task_type='mid_stay', defaults={'date': extra.mid_stay_clean_date},
        )
        _sync_task_date(task, extra.mid_stay_clean_date)
    else:
        CleaningTask.objects.filter(booking=booking, task_type='mid_stay', status='pending').delete()


def property_last_clean_before(property, booking):
    """The property's most recent recorded clean on or before booking.arrival_date - any
    CleaningTask type, not just 'done', across the property's other active (non-CLOSED_STATUSES,
    non-dismissed) bookings; this booking's own tasks are excluded since they can never be what
    covers its own arrival (a turnover/mid-stay date is always later than its own arrival, and a
    freshen task existing for this same booking would otherwise count itself as covering its own
    gap on a re-sweep). "On or before", not strictly before: a same-day turnover is a normal,
    covering clean (see cleaning_task_valid_range's docstring), not an uncovered gap. Shared by
    sync_freshen_tasks_for_property(), cleaning_task_valid_range()'s freshen branch, and the
    Freshen popup's "Last Clean" display (staff/views.py::StaffCleaningTaskDetailView) so all
    three agree on what counts."""
    from staff.models import CleaningTask

    return CleaningTask.objects.filter(
        booking__property=property,
    ).exclude(
        booking__enquiry_status__in=CLOSED_STATUSES,
    ).exclude(status='dismissed').exclude(booking=booking).filter(
        date__lte=booking.arrival_date,
    ).aggregate(last=Max('date'))['last']


def sync_freshen_tasks_for_property(property):
    """Property-scoped counterpart to sync_cleaning_tasks_for_booking() above - Freshen depends on
    a property's whole booking timeline, not one booking in isolation, so it can't be driven off a
    single Departure/Extra/Booking save the way turnover/mid-stay are. Called from the same
    signals (staff/signals.py) right after sync_cleaning_tasks_for_booking(), so it re-runs on
    every booking create/cancel/uncancel/date-change on this property.

    Walks the property's active (non-CLOSED_STATUSES) bookings in arrival order. For each one,
    the gap since the property's last recorded clean (the latest CleaningTask date on or before
    this arrival - any type, not just 'done', across this property's other active bookings,
    excluding dismissed rows and this booking's own tasks) is compared against
    cleaning_company.freshen_after_days. "On or before", not strictly before: a turnover clean
    dated the same day as the next arrival is a normal same-day turnover (see
    cleaning_task_valid_range's own docstring), not an uncovered gap - excluding it undercounts
    what's actually covering that arrival and, in production, briefly did exactly that for a
    booking whose turnover had been manually dragged onto its next-arrival's own date.
    - Gap qualifies, no freshen task yet -> create one dated the day before arrival.
    - Gap qualifies, an earlier auto-dismissal ('gap_closed') exists -> reinstate it. This is the
      symmetric case Thomas asked for: cancelling whatever booking had closed the gap should bring
      the freshen back, since the sweep re-runs on that cancellation too.
    - Gap qualifies, a manual dismissal exists, or the task is already pending/done -> leave it -
      a manual dismissal is a staff decision and must never be silently overridden by this sweep.
    - Gap doesn't qualify, a pending freshen task exists -> auto-dismiss it (dismissed_reason=
      'gap_closed', dismissed_by=None) - a later booking has closed the gap, so the speculative
      clean is no longer justified. Fires even if staff already assigned a team to it, per
      Thomas's explicit description of the requirement.
    - Gap doesn't qualify, task is 'done' or already dismissed -> leave it.

    Re-queries last-clean on every iteration (rather than precomputing once) so a freshen task
    created earlier in this same walk correctly feeds into the gap calculation for later bookings
    in it. No infinite-loop risk from the signals that call this: CleaningTask itself isn't a
    watched sender, so creating/updating one here doesn't re-trigger sync.

    Two known gaps, not addressed here: a property's very first-ever booking never gets a freshen
    task (nothing to measure a gap against), and platform-synced (iCal) bookings don't get a
    Departure row (bookings/utils.py::sync_ical_link()) so they never register as a "last clean"
    even though the platform's own cleaner may have serviced the property in between."""
    from bookings.models import Booking
    from staff.models import CleaningTask

    cleaning_company = property.cleaning_company
    if cleaning_company is None or not cleaning_company.cleans_on_calendar or cleaning_company.freshen_after_days is None:
        return
    freshen_after_days = cleaning_company.freshen_after_days

    bookings = Booking.objects.filter(property=property).exclude(
        enquiry_status__in=CLOSED_STATUSES,
    ).order_by('arrival_date')

    for booking in bookings:
        last_clean = property_last_clean_before(property, booking)
        existing = CleaningTask.objects.filter(booking=booking, task_type='freshen').first()
        gap_qualifies = last_clean is not None and (booking.arrival_date - last_clean).days >= freshen_after_days

        if gap_qualifies:
            if existing is None:
                CleaningTask.objects.create(
                    booking=booking, task_type='freshen',
                    date=booking.arrival_date - timedelta(days=1),
                )
            elif existing.status == 'dismissed' and existing.dismissed_reason == 'gap_closed':
                existing.status = 'pending'
                existing.dismissed_by = None
                existing.dismissed_at = None
                existing.dismissed_reason = ''
                existing.save(update_fields=['status', 'dismissed_by', 'dismissed_at', 'dismissed_reason'])
        else:
            if existing is not None and existing.status == 'pending':
                existing.status = 'dismissed'
                existing.dismissed_by = None
                existing.dismissed_at = timezone.now()
                existing.dismissed_reason = 'gap_closed'
                existing.save(update_fields=['status', 'dismissed_by', 'dismissed_at', 'dismissed_reason'])


def _sync_task_date(task, computed_date):
    """computed_date = booking.departure_date (turnover) or extra.mid_stay_clean_date (mid_stay) -
    the date this task would auto-place at absent any manual drag (see
    staff/views.py::StaffCleaningTaskMoveView). Three cases:
    1. Not manually_scheduled: always adopt computed_date (today's behaviour).
    2. Manually scheduled but the source date itself moved since the drag (computed_date !=
       task.auto_date): an explicit edit to the departure/mid-stay-clean date should always win
       over an old drag - clear the override and adopt the new computed_date.
    3. Manually scheduled and the source date is unchanged: check the drag is still inside its
       valid window (cleaning_task_valid_range). Still valid -> leave date untouched (the whole
       point of the flag). No longer valid (e.g. a new confirmed booking now arrives before it) ->
       clear the override and adopt computed_date - a stale override is a property-cleanliness
       risk, so this is a hard reset, not a warning."""
    if not task.manually_scheduled:
        if task.date != computed_date:
            task.date = computed_date
            task.save(update_fields=['date'])
        return

    if computed_date != task.auto_date:
        task.date, task.manually_scheduled, task.auto_date = computed_date, False, None
        task.save(update_fields=['date', 'manually_scheduled', 'auto_date'])
        return

    min_date, max_date = cleaning_task_valid_range(task)
    still_valid = task.date >= min_date and (max_date is None or task.date <= max_date)
    if not still_valid:
        task.date, task.manually_scheduled, task.auto_date = computed_date, False, None
        task.save(update_fields=['date', 'manually_scheduled', 'auto_date'])


def cleaning_task_valid_range(task):
    """(min_date, max_date_or_None) this task's date may occupy, both ends inclusive - a turnover
    clean can land on the same day the next guest arrives (a normal same-day turnover: guest
    leaves in the morning, cleaners come, next guest checks in later that day). A mid-stay clean
    is confined to the same ±1-day window around the middle of the stay the guest was shown as
    their estimate (bookings.utils.mid_stay_clean_window) - not the full stay - so a drag on the
    calendar can only ever nudge it a day either way, matching that it's a fixed estimate, not an
    open-ended reschedule. A freshen clean can't be dragged past its own booking's arrival (same
    ceiling logic as turnover, just against this booking rather than the next one) or earlier than
    the property's last actual clean before that arrival - dragging it earlier than that would put
    it before a clean that already covers the gap it exists to fill."""
    from bookings.models import Booking
    from bookings.utils import mid_stay_clean_window

    booking = task.booking
    if task.task_type == 'turnover':
        min_date = booking.departure_date
        return min_date, Booking.objects.next_confirmed_arrival_after(booking.property, min_date)
    if task.task_type == 'freshen':
        last_clean = property_last_clean_before(booking.property, booking)
        min_date = (last_clean + timedelta(days=1)) if last_clean else booking.arrival_date
        return min_date, booking.arrival_date
    _, min_date, max_date = mid_stay_clean_window(booking)
    return min_date, max_date


def apply_manual_task_date(task, new_date):
    """Validates new_date against cleaning_task_valid_range(task), then sets
    date/manually_scheduled/auto_date and saves - exactly what a calendar drag
    (staff/views.py::StaffCleaningTaskMoveView) does, and the same thing the clean-planner popup
    and the booking detail page's embedded planner do when a date is actually changed there too.
    Returns None on success, or a human-readable error string on failure. Callers should only call
    this when new_date differs from task.date - saving an unrelated field (e.g. just ticking a
    cleaning-staff checkbox) must never flip manually_scheduled or touch auto_date.

    Also re-runs sync_freshen_tasks_for_property() for this task's property after a successful
    save, for any task_type - dragging any clean's date changes the property's actual clean
    timeline, which can close or reopen a gap for a *different* booking's Freshen task the same
    way a booking create/cancel does (staff/signals.py normally drives that, but a drag only saves
    the CleaningTask itself, never Booking/Departure/Extra, so those signals never fire for it -
    confirmed live 2026-08-27: dragging a turnover clean later closed a gap for a booking three
    stays down, and it only picked up the change because the reconcile command happened to be
    re-run manually afterwards, not because the drag itself triggered anything)."""
    min_date, max_date = cleaning_task_valid_range(task)
    if new_date < min_date or (max_date is not None and new_date > max_date):
        return "That date is outside this task's valid window."

    if task.task_type == 'turnover':
        computed_date = task.booking.departure_date
    elif task.task_type == 'mid_stay':
        computed_date = task.booking.extras.mid_stay_clean_date
    else:
        computed_date = task.booking.arrival_date - timedelta(days=1)
    task.date = new_date
    task.manually_scheduled = True
    task.auto_date = computed_date
    task.save(update_fields=['date', 'manually_scheduled', 'auto_date'])
    sync_freshen_tasks_for_property(task.booking.property)
    return None


def _standard_checkin_time(booking):
    """The fallback clock time for an arrival with no real ETA to compute from - the booking
    property's booking_company's standard_checkin_time, or the same 14:00 that field itself
    defaults to when no company is tracked for this property at all (Property.booking_company is
    nullable - see ManagementCompany's own docstring)."""
    from datetime import time as time_cls
    company = booking.property.booking_company
    return company.standard_checkin_time if company else time_cls(14, 0)


def compute_arrival_eta(booking):
    """(time, is_all_day) - the calendar-displayed clock time for this booking's arrival.
    Arrival.time means something different per travel method, per the guest-facing form
    (bookings/templates/bookings/_arrival_departure_form.html): a flight's *landing* time, a bus/
    train's own "expected time in Albufeira" (already an at-property estimate, just needing a
    last-mile buffer), or - for driving - the guest's own estimated arrival time at the property
    itself, used as-is with no buffer (explicit choice, not an oversight). method='other' never
    gets a time field on that form at all, so there's nothing to compute - same as time simply
    not having been filled in yet. Either case now falls back to _standard_checkin_time() rather
    than rendering as an all-day event (2026-08-28, per Thomas - a guessed-but-labelled placeholder
    time is more useful on a glance-and-go calendar than a blank all-day slot), so is_all_day is
    always False; kept as part of the return shape since callers already destructure it."""
    from bookings.models import CheckinSettings, TravelMethod

    arrival = getattr(booking, 'arrival', None)
    if arrival is None or arrival.time is None:
        return _standard_checkin_time(booking), False

    if arrival.method == TravelMethod.FLIGHT_FARO:
        buffer_minutes = CheckinSettings.load().faro_buffer_minutes
    elif arrival.method == TravelMethod.FLIGHT_LISBON:
        buffer_minutes = CheckinSettings.load().lisbon_buffer_minutes
    elif arrival.method in (TravelMethod.BUS, TravelMethod.TRAIN):
        buffer_minutes = CheckinSettings.load().transit_buffer_minutes
    elif arrival.method == TravelMethod.DRIVING:
        buffer_minutes = 0
    else:
        return _standard_checkin_time(booking), False

    computed = (datetime.combine(date.today(), arrival.time) + timedelta(minutes=buffer_minutes)).time()
    return computed, False


def _computed_checkin_time(booking, task_type):
    """The 'auto' time for a Checkin of a given task_type - what its time would be absent any
    manual drag. 'arrival' reads compute_arrival_eta(); 'key_box'/'welcome_visit' just read
    CheckinSettings' own fixed policy times directly, unrelated to the guest's own arrival time by
    design. Shared by sync_checkins_for_booking(), apply_manual_checkin_time(), and the
    CheckinSettings post_save cascade, so all three agree on what "computed" means per type."""
    if task_type == 'arrival':
        return compute_arrival_eta(booking)[0]
    from bookings.models import CheckinSettings
    settings_obj = CheckinSettings.load()
    return settings_obj.key_box_prep_time if task_type == 'key_box' else settings_obj.welcome_visit_time


def checkin_valid_range(checkin):
    """(min_time, max_time) a Checkin's time may occupy on a drag - always its own existing date's
    full 00:00-23:59 span. Deliberately date-invariant (unlike cleaning_task_valid_range, which
    computes a real cross-booking window) - a check-in's date is never draggable at all (an
    arrival-date change has to happen from the booking's own page, per Thomas), so the only thing
    a drag can legally do is move the time-of-day within that fixed date."""
    from datetime import time as time_cls
    return time_cls.min, time_cls.max


def apply_manual_checkin_time(checkin, new_time):
    """Same shape as apply_manual_task_date() above, for Checkin.time instead of CleaningTask.date
    - validates, then sets time/manually_scheduled/auto_time. Callers should only call this when
    new_time differs from checkin.time. Returns None on success or an error string on failure."""
    min_time, max_time = checkin_valid_range(checkin)
    if new_time < min_time or new_time > max_time:
        return "That time is outside this check-in's valid window."

    checkin.time = new_time
    checkin.manually_scheduled = True
    checkin.auto_time = _computed_checkin_time(checkin.booking, checkin.task_type)
    checkin.save(update_fields=['time', 'manually_scheduled', 'auto_time'])
    return None


def _sync_checkin_time(checkin, computed_time):
    """Checkin.time counterpart to _sync_task_date() above - same three cases: not manually
    scheduled, always adopt computed_time; manually scheduled but the source time itself changed
    since the drag, an explicit edit should win, clear the override and adopt; manually scheduled
    and the source is unchanged, leave it alone. Unlike _sync_task_date there's no "valid window"
    re-check on the unchanged-source path - a Checkin's valid window (checkin_valid_range) is
    always that same fixed date's full 00:00-23:59 span, so a drag can never fall outside it after
    the fact the way a turnover's next-arrival ceiling can shift."""
    if not checkin.manually_scheduled:
        if checkin.time != computed_time:
            checkin.time = computed_time
            checkin.save(update_fields=['time'])
        return

    if computed_time != checkin.auto_time:
        checkin.time, checkin.manually_scheduled, checkin.auto_time = computed_time, False, None
        checkin.save(update_fields=['time', 'manually_scheduled', 'auto_time'])


def sync_checkins_for_booking(booking):
    """Keeps Checkin rows in sync with Arrival (the 'arrival' row) and, for a self-check-in
    booking, two auto-generated policy tasks ('key_box'/'welcome_visit') - see Checkin's own
    docstring (staff/models.py) for the full reasoning. Never deletes a 'done' row - only a still-
    pending one is removed when its trigger condition disappears, same rule
    sync_cleaning_tasks_for_booking() already applies to turnover/mid-stay above. A cancelled
    booking (CLOSED_STATUSES) gets no check-in tasks at all - one blanket pending-delete across
    every task_type, matching that function's own cancellation branch exactly (not three separate
    per-type branches - a cancelled booking needs none of them, full stop). Same blanket-delete
    when the property's booking_company has checkins_on_calendar=False (2026-08-28, per Thomas) -
    only when a company is actually set, an untracked property is unaffected."""
    from staff.models import Checkin

    if booking.enquiry_status in CLOSED_STATUSES:
        Checkin.objects.filter(booking=booking, status='pending').delete()
        return

    booking_company = booking.property.booking_company
    if booking_company is not None and not booking_company.checkins_on_calendar:
        Checkin.objects.filter(booking=booking, status='pending').delete()
        return

    task, _ = Checkin.objects.get_or_create(
        booking=booking, task_type='arrival',
        defaults={'date': booking.arrival_date, 'time': _computed_checkin_time(booking, 'arrival')},
    )
    if task.date != booking.arrival_date:
        task.date = booking.arrival_date
        task.save(update_fields=['date'])
    _sync_checkin_time(task, _computed_checkin_time(booking, 'arrival'))

    arrival = getattr(booking, 'arrival', None)
    if arrival and arrival.self_check_in:
        for task_type, offset_days in (('key_box', 0), ('welcome_visit', 1)):
            sub_task, _ = Checkin.objects.get_or_create(
                booking=booking, task_type=task_type,
                defaults={
                    'date': booking.arrival_date + timedelta(days=offset_days),
                    'time': _computed_checkin_time(booking, task_type),
                },
            )
            expected_date = booking.arrival_date + timedelta(days=offset_days)
            if sub_task.date != expected_date:
                sub_task.date = expected_date
                sub_task.save(update_fields=['date'])
            _sync_checkin_time(sub_task, _computed_checkin_time(booking, task_type))
    else:
        Checkin.objects.filter(
            booking=booking, task_type__in=['key_box', 'welcome_visit'], status='pending',
        ).delete()


def parsed_date(raw):
    """'YYYY-MM-DD' -> date, or None for anything missing/malformed - shared by every staff/owner
    view that reads a date off a GET/POST param (date-range filters, mark-done forms)."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def last_day_of_month(day):
    next_month = day.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)


def resync_checkin_times_for_settings_change():
    """Called from staff/signals.py on CheckinSettings' own post_save - a changed buffer/policy
    time is a staff-facing correctness issue (a wrong pickup/greet time on the calendar), not just
    a cosmetic staleness, so every future, not-yet-manually-dragged row gets its time recomputed
    immediately rather than waiting for some unrelated save on that booking to happen to trigger
    it. Bounded to future dates and manually_scheduled=False so this is cheap and never clobbers a
    deliberate drag."""
    from staff.models import Checkin

    today = timezone.now().date()
    checkins = Checkin.objects.filter(manually_scheduled=False, date__gte=today).select_related('booking__arrival')
    for checkin in checkins:
        _sync_checkin_time(checkin, _computed_checkin_time(checkin.booking, checkin.task_type))
