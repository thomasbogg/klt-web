from datetime import date

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
    which happen in a separate deployed service entirely)."""
    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='task_history')
    description = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'staff_task_history_entries'
        verbose_name = 'Task History Entry'
        verbose_name_plural = 'Task History Entries'
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.booking} - {self.description}"
