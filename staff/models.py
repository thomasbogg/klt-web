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
    mid_stay_clean_date). Departure.clean/Extra.mid_stay_clean stay the source of truth (edited
    from the booking detail page's Booking Info panel, same as always) - this row is kept in sync
    via post_save signals on Departure/Extra/Booking (staff/signals.py, connected from
    StaffConfig.ready() - this app's first use of Django signals), not a call embedded in one view
    method, since bookings/admin.py's ExtraInline/DepartureInline/BookingDateAdjustmentInline are a
    second, admin-side write path that would otherwise silently desync a call-site-based approach.
    See staff/utils.py::sync_cleaning_tasks_for_booking(). date is deliberately denormalized from
    Booking.departure_date/Extra.mid_stay_clean_date at sync time so the rota can query by date
    directly without joining back through Departure/Extra/Booking each time.

    assigned_to/status/completed_by/completed_at/notes are real fields on this row, not on
    Departure/Extra, since assignment and completion tracking are staff-rota concerns distinct
    from travel logistics (Departure) or guest-requested extras (Extra)."""
    TASK_TYPE_CHOICES = [('turnover', 'Turnover'), ('mid_stay', 'Mid-stay')]
    STATUS_CHOICES = [('pending', 'Pending'), ('done', 'Done')]

    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='cleaning_tasks')
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES)
    date = models.DateField()
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cleaning_tasks',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    completed_at = models.DateTimeField(blank=True, null=True)
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
