from datetime import date

from django.conf import settings
from django.db import models


class Deduction(models.Model):
    """A charge deducted from a booking (e.g. against the security deposit), recorded from the
    staff booking detail page - see staff/views.py::StaffBookingDetailView."""
    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='deductions')
    date = models.DateField(default=date.today)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'staff_deductions'
        verbose_name = 'Deduction'
        verbose_name_plural = 'Deductions'
        ordering = ('-date', '-created_at')

    def __str__(self):
        return f"{self.booking} - {self.description} ({self.amount})"


class OwnerPayment(models.Model):
    """Ad-hoc payment recorded against a booking's owner payout - deliberately booking-scoped and
    manually entered, not a full owner-ledger/statement system (that's a separate, larger feature -
    see staff booking detail page plan). Feeds directly into
    bookings/payouts.py::compute_owner_payout(), which subtracts these from the computed owner
    balance - always EUR, matching that calculation."""
    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='owner_payments')
    date = models.DateField(default=date.today)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'staff_owner_payments'
        verbose_name = 'Owner Payment'
        verbose_name_plural = 'Owner Payments'
        ordering = ('-date', '-created_at')

    def __str__(self):
        return f"{self.booking} - {self.amount} {self.currency} to owner"


class TaskHistoryEntry(models.Model):
    """A short log line on a booking's timeline. Manually added from the staff booking detail
    page, plus auto-logged at the few events that page itself causes (a charge edit, a status
    change made there) - deliberately not an automatic audit log of every field change across the
    whole app, and deliberately doesn't retrofit logging into existing call sites elsewhere
    (create_booking(), cancel_booking_hold(), extras saves, or klt-hooks' payment-webhook writes,
    which happen in a separate deployed service entirely).

    description is a short stub shown directly in the list (e.g. "Status changed"); detail is the
    fuller "what actually happened" text (e.g. "From 'Booking confirmed' to 'Cancelled by staff'")
    - both are shown together, along with created_at (to the minute) and created_by, in an on-hover
    tooltip rather than inline, to keep the list itself scannable (2026-08-25). created_by is
    nullable/SET_NULL since a staff account can be deleted later without losing the history row."""
    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='task_history')
    description = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'staff_task_history_entries'
        verbose_name = 'Task History Entry'
        verbose_name_plural = 'Task History Entries'
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.booking} - {self.description}"


class StaffRole(models.Model):
    """A named set of staff pages a non-superuser account may access - see
    staff/permissions.py::staff_page_required and staff/utils.py::STAFF_PAGE_PERMISSION_FIELDS.
    Page-level only (not per-panel within a page) and visibility-only (a role that can see a page
    can also edit whatever that page already lets any staff user edit) - deliberate v1 scope,
    2026-08-26. Superusers bypass this entirely and always have full access."""
    name = models.CharField(max_length=100, unique=True)
    can_view_home = models.BooleanField(default=False)
    can_view_bookings = models.BooleanField(default=False)
    can_view_guests = models.BooleanField(default=False)
    can_view_properties = models.BooleanField(default=False)
    can_view_locations = models.BooleanField(default=False)
    can_view_settings = models.BooleanField(default=False)
    can_view_cleaning_rota = models.BooleanField(default=False)
    can_view_checkins_calendar = models.BooleanField(default=False)
    # Assignability, not page visibility - deliberately excluded from STAFF_PAGE_PERMISSION_FIELDS
    # (staff/utils.py), which drives the generic per-page-flag loop in views.py's
    # _add_role/_update_role and the settings.html role table. This flag instead means "a user
    # holding this role can be ticked as a cleaner" (StaffCleaningRotaView/the cleaning calendar's
    # assignable-users queries), independent of whether they can even see the rota page.
    is_cleaning_staff = models.BooleanField(default=False)
    # Same "assignability, not page visibility" shape as is_cleaning_staff above, but a distinct
    # flag rather than reuse - check-in duty is a different pool of people from cleaning staff
    # (Thomas: "the check-ins manager needs to see these distinctly").
    is_checkin_staff = models.BooleanField(default=False)

    class Meta:
        db_table = 'staff_roles'
        verbose_name = 'Staff Role'
        verbose_name_plural = 'Staff Roles'
        ordering = ('name',)

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    """Extends the built-in User with the one Role it holds (see StaffRole) - deliberately a
    single optional FK, not a many-to-many, per Thomas's explicit choice of one role per user.
    role is SET_NULL on delete: removing a Role just leaves its former holders with no role
    (locked out of every page until reassigned), not a hard block on deleting the Role."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_profile')
    role = models.ForeignKey(StaffRole, on_delete=models.SET_NULL, null=True, blank=True, related_name='profiles')

    class Meta:
        db_table = 'staff_profiles'
        verbose_name = 'Staff Profile'
        verbose_name_plural = 'Staff Profiles'

    def __str__(self):
        return f"{self.user} ({self.role or 'no role'})"


class CleaningTask(models.Model):
    """A rota-visible clean for one booking - either the turnover clean tied to its departure
    (Departure.clean/Booking.departure_date) or its optional mid-stay clean (Extra.mid_stay_clean/
    mid_stay_clean_date). Departure.clean/Extra.mid_stay_clean stay the source of truth -
    Departure.clean from the booking detail page's Booking Info panel same as always, but
    Extra.mid_stay_clean is guest-selected only (BookingFormMixin._save_extras/
    _parse_mid_stay_clean; 2026-08-27, a duplicate staff-side checkbox for it on Booking Info was
    removed after it was found silently overwriting the guest's real choice) - this row is kept in
    sync via post_save signals on Departure/Extra/Booking (staff/signals.py, connected from
    StaffConfig.ready() - this app's first use of Django signals), not a call embedded in one view
    method, since bookings/admin.py's ExtraInline/DepartureInline/BookingDateAdjustmentInline are a
    second, admin-side write path that would otherwise silently desync a call-site-based approach.
    See staff/utils.py::sync_cleaning_tasks_for_booking(). date is deliberately denormalized from
    Booking.departure_date/Extra.mid_stay_clean_date at sync time so the rota can query by date
    directly without joining back through Departure/Extra/Booking each time.

    assigned_to/status/completed_by/completed_at/notes are real fields on this row, not on
    Departure/Extra, since assignment and completion tracking are staff-rota concerns distinct
    from travel logistics (Departure) or guest-requested extras (Extra). assigned_to is a
    many-to-many (not a single FK) since different staff can share a day's cleans - membership is
    what the rota/calendar's "my tasks" filters and mark-done permission check against, not a
    single owner.

    A third task_type, 'freshen', covers a vacant stretch between bookings that would otherwise go
    uncleaned - auto-inserted/auto-dismissed by staff/utils.py::sync_freshen_tasks_for_property()
    against ManagementCompany.freshen_after_days, called from the same signals as
    sync_cleaning_tasks_for_booking() above. Unlike turnover/mid-stay it's speculative, so it's
    dismissible without losing the row: dismissed_by/dismissed_at/dismissed_reason mirror
    completed_by/completed_at below, and 'dismissed' is a real status rather than a delete, so
    undo is just clearing those fields back to 'pending' - no reconstruction needed. dismissed_by
    is null for an automatic dismissal (the gap closed) and set for a staff-initiated one;
    dismissed_reason distinguishes the two so the automatic sweep never silently reinstates a
    dismissal a human made on purpose."""
    TASK_TYPE_CHOICES = [('turnover', 'Turnover'), ('mid_stay', 'Mid-stay'), ('freshen', 'Freshen')]
    STATUS_CHOICES = [('pending', 'Pending'), ('done', 'Done'), ('dismissed', 'Dismissed')]
    TEAM_CHOICES = [(1, 'Group 1'), (2, 'Group 2'), (3, 'Group 3')]
    DISMISSED_REASON_CHOICES = [('manual', 'Manual'), ('gap_closed', 'Gap closed')]

    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='cleaning_tasks')
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES)
    date = models.DateField()
    assigned_to = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='cleaning_tasks')
    # Which crew this clean belongs to, for the calendar's visual banding when more than one team
    # works the same day - meaningless until assigned_to is non-empty (the calendar always puts
    # unassigned tasks in their own section ahead of any team, regardless of this value), but
    # still defaults to 1 so the field always has a sane value to show in the planner dropdown.
    team = models.PositiveSmallIntegerField(choices=TEAM_CHOICES, default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    completed_at = models.DateTimeField(blank=True, null=True)
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    dismissed_at = models.DateTimeField(blank=True, null=True)
    dismissed_reason = models.CharField(max_length=20, choices=DISMISSED_REASON_CHOICES, blank=True)
    notes = models.TextField(blank=True)
    # Set when a superuser drags this task to a new date on the cleaning calendar view (staff/
    # views.py::StaffCleaningTaskMoveView) - tells sync_cleaning_tasks_for_booking() (staff/
    # utils.py) not to blindly overwrite `date` from Departure/Extra on the next unrelated save.
    # auto_date records what the auto-computed date was at the moment of the drag, so the sync can
    # tell "source date moved since the drag" (always resync) apart from "still the same source,
    # just check the drag is still inside its valid window" - see cleaning_task_valid_range().
    # Only meaningful while manually_scheduled=True; null otherwise, no backfill needed.
    manually_scheduled = models.BooleanField(default=False)
    auto_date = models.DateField(blank=True, null=True)

    class Meta:
        db_table = 'staff_cleaning_tasks'
        verbose_name = 'Cleaning Task'
        verbose_name_plural = 'Cleaning Tasks'
        # One turnover clean and one mid-stay clean per booking - matches the 1-per-booking
        # ceiling Departure/Extra's own OneToOne-with-Booking shape already imposes.
        unique_together = ('booking', 'task_type')
        ordering = ('date', 'booking__property__title')

    def __str__(self):
        return f"{self.booking} - {self.get_task_type_display()} clean ({self.date})"


class Checkin(models.Model):
    """A check-ins-calendar-visible task for one booking's arrival - either the arrival itself
    (task_type='arrival', always exactly one per booking) or, for a self-check-in booking, two
    auto-generated staff prep/policy tasks: setting the key box before the guest arrives, and a
    next-day in-person welcome visit (company policy: everyone gets met in person eventually, even
    a guest who let themselves in). Kept in sync via post_save signals on Arrival/Booking/
    CheckinSettings (staff/signals.py), same reasoning as CleaningTask's own docstring gives for
    being signal-driven rather than call-site-driven - bookings/admin.py's ArrivalInline/
    BookingDateAdjustmentInline are a second write path a call-site approach would silently miss.
    See staff/utils.py::sync_checkins_for_booking().

    time is deliberately denormalized from Arrival.time (adjusted by CheckinSettings' per-method
    buffer - staff/utils.py::compute_arrival_eta) for 'arrival' rows, or from CheckinSettings'
    fixed key_box_prep_time/welcome_visit_time for the other two - not read live, so the calendar
    can query/sort by it directly. manually_scheduled/auto_time are CleaningTask.manually_scheduled
    /auto_date's exact counterpart: a dragged time needs to survive an unrelated resync (an
    unrelated field on the same booking being saved) the same way a dragged clean date already
    does, without silently eating a real Arrival.time edit either.

    extras_collected/deposit_collected/deposit_returned are only meaningful for task_type=
    'arrival' - key_box/welcome_visit rows just use status/completed_by/completed_at for their own
    done tracking, same "conditional meaning per task_type" shape as CleaningTask's own
    dismissed_by/dismissed_at/dismissed_reason. deposit_returned genuinely belongs to a later,
    post-departure moment, not arrival - kept on this same row anyway since there's no departure-
    side calendar/task feature yet and building one solely to host one boolean isn't warranted."""
    TASK_TYPE_CHOICES = [('arrival', 'Arrival'), ('key_box', 'Key box'), ('welcome_visit', 'Welcome visit')]
    STATUS_CHOICES = [('pending', 'Pending'), ('done', 'Done')]

    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='checkins')
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES)
    date = models.DateField()
    # None means "no time to show" (Arrival.method='other', which the guest-facing form never
    # shows a time field for, or Arrival.time simply not filled in yet) - rendered as an all-day
    # event on the calendar rather than a guessed time.
    time = models.TimeField(blank=True, null=True)
    assigned_to = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='checkins')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    completed_at = models.DateTimeField(blank=True, null=True)
    manually_scheduled = models.BooleanField(default=False)
    auto_time = models.TimeField(blank=True, null=True)
    extras_collected = models.BooleanField(default=False)
    deposit_collected = models.BooleanField(default=False)
    deposit_returned = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'staff_checkins'
        verbose_name = 'Check-in'
        verbose_name_plural = 'Check-ins'
        # One arrival, one key-box prep, one welcome visit per booking - matches CleaningTask's
        # own one-row-per-task_type-per-booking shape.
        unique_together = ('booking', 'task_type')
        ordering = ('date', 'time', 'booking__property__title')

    def __str__(self):
        return f"{self.booking} - {self.get_task_type_display()} ({self.date})"
