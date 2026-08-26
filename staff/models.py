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
