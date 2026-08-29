from datetime import date

from django.conf import settings
from django.db import models


class Memo(models.Model):
    """A billing document for one property's turnover clean, 1:1 with the staff.CleaningTask that
    represents it - kept in sync via finance/services.py::sync_memo_for_turnover_task(), called
    from the same signals that already keep CleaningTask itself in sync (staff/signals.py), not a
    manually-created row. Only ever created for a turnover task on a property whose
    cleaning_company has finances_managed_internally=True (properties.models.ManagementCompany) -
    mid-stay and freshen cleans are out of scope (mid-stay stays guest-billed via the existing
    Extra charge; freshen is unbilled).

    clean_fee/meet_greet_fee are snapshotted from bookings/payouts.py::clean_fee()/
    meet_greet_fee() (the same functions compute_owner_payout() itself uses for its
    management_fee deduction) at sync time, and kept live-recomputed on every resync while unsent
    - see sync_memo_for_turnover_task()'s docstring. Once sent_at is set they freeze permanently:
    a dispatched financial document must never silently change its own numbers later because
    PaymentSettings or the underlying booking changed.

    cleaning_task is nullable/SET_NULL, not CASCADE: a turnover CleaningTask is hard-deleted (not
    dismissed - only Freshen tasks use the dismissed status) when its booking cancels, its
    Departure.clean flag is unticked, or its cleaning_company's cleans_on_calendar flips off (see
    staff/utils.py::sync_cleaning_tasks_for_booking). A Memo that already has sent_at set must
    survive that as a permanent historical record; an unsent Memo also survives (rather than
    cascading away) so it isn't silently lost mid-edit, but becomes "orphaned" - it can never again
    be selected as a property's "current open memo" (see finance/services.py::
    open_memo_for_property), and any AdHocService rows still attached to it are released back to
    memo=None by finance/services.py::sync_memo_for_turnover_task so they roll onto whichever
    memo becomes open next, rather than being silently written off. A fresh CleaningTask created
    later (e.g. the booking is uncancelled) always gets a brand-new Memo row via get_or_create,
    never reuses an orphaned one - mirrors CleaningTask's own "recreate whatever's due from
    scratch on uncancel" convention rather than inventing a "revive" concept.

    property is PROTECT, not SET_NULL: a Memo is real financial data and must block a Property
    delete, same reasoning as bookings.Booking.property. No denormalized date field - the Memos
    tab groups by cleaning_task__date directly, so a manual drag-to-reschedule of the underlying
    CleaningTask (CleaningTask.manually_scheduled) moves the Memo with it for free."""
    property = models.ForeignKey('properties.Property', on_delete=models.PROTECT, related_name='memos')
    cleaning_task = models.OneToOneField(
        'staff.CleaningTask', on_delete=models.SET_NULL, null=True, blank=True, related_name='memo',
    )
    clean_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    meet_greet_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'finance_memos'
        verbose_name = 'Memo'
        verbose_name_plural = 'Memos'
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.property} memo ({self.cleaning_task.date if self.cleaning_task else 'orphaned'})"

    def ad_hoc_total(self):
        """Deliberately a plain method, not @property: this model has a field literally named
        `property` (matching bookings.models.Booking's own field of the same name), which shadows
        the @property decorator itself anywhere later in this class body - see Booking.
        total_guests's own docstring for the same trap."""
        return sum((s.cost for s in self.ad_hoc_services.all()), self.clean_fee.__class__('0'))

    def total(self):
        return self.clean_fee + self.meet_greet_fee + self.ad_hoc_total()


class AdHocService(models.Model):
    """A property-scoped, manually-entered cost (e.g. an AC repair, an extra deep clean) -
    created independently of any booking or Memo, from its own CRUD page (staff/views.py::
    StaffFinanceAdHocServiceListView). Deliberately a different, broader concept from
    staff.models.OwnerPayment (booking-scoped, a pre-existing narrower feature) - this is
    additive, not a replacement.

    memo is nullable/SET_NULL: on creation this attaches itself to the property's "current open
    memo" (finance/services.py::open_memo_for_property) if one exists; if none does yet (no
    upcoming turnover clean scheduled), it's left unattached (memo=None) until a matching Memo
    later appears, at which point finance/services.py::sync_memo_for_turnover_task sweeps it in.
    If its Memo is later orphaned (see Memo's own docstring) it's released back to memo=None by
    the same sweep, rather than being silently lost."""
    property = models.ForeignKey('properties.Property', on_delete=models.PROTECT, related_name='ad_hoc_services')
    memo = models.ForeignKey(Memo, on_delete=models.SET_NULL, null=True, blank=True, related_name='ad_hoc_services')
    description = models.CharField(max_length=200)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=date.today)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'finance_ad_hoc_services'
        verbose_name = 'Ad-hoc Service'
        verbose_name_plural = 'Ad-hoc Services'
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.property} - {self.description} ({self.cost})"

    def save(self, *args, **kwargs):
        if self._state.adding and self.memo_id is None:
            from finance.services import open_memo_for_property
            self.memo = open_memo_for_property(self.property)
        super().save(*args, **kwargs)


class PayoutRecord(models.Model):
    """The only place 'has this booking's owner payout actually been paid out' is recorded -
    bookings/payouts.py::compute_owner_payout() is a pure, unpersisted calculation with no model
    behind it. One row per booking, created only when staff click "Mark as paid" on the Payouts
    tab (staff/views.py::StaffFinancePayoutMarkPaidView); a booking with no row here is simply not
    yet paid - there is no separate "unpaid" state to manage.

    amount snapshots owner_balance at the moment of marking paid, rather than being re-derived
    live later, since compute_owner_payout depends on PaymentSettings/Charge/PlatformPayout data
    that could still be corrected afterwards - what was actually sent must stay fixed for audit/
    Statement purposes even if the live recomputation would now differ. booking is CASCADE,
    matching every other booking-scoped row in this codebase (Deduction, OwnerPayment,
    CleaningTask, Checkin) - a payout record with no booking to belong to is meaningless."""
    booking = models.OneToOneField('bookings.Booking', on_delete=models.CASCADE, related_name='payout_record')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateTimeField(auto_now_add=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'finance_payout_records'
        verbose_name = 'Payout Record'
        verbose_name_plural = 'Payout Records'
        ordering = ('-paid_at',)

    def __str__(self):
        return f"{self.booking} paid {self.amount} on {self.paid_at:%Y-%m-%d}"
