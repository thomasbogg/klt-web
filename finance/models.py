from datetime import date

from django.conf import settings
from django.db import models

from bookings.models import PAYMENT_STATUS_CHOICES, PROVIDER_CHOICES


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
    # Set by finance/services.py::dispatch_memo_to_sage - NOT currently called from anywhere
    # (2026-09-09): cleans/meet-greet Sage invoicing is batched monthly per owner, not per Memo, so
    # these stay null on every Memo until that monthly batch mechanism exists and sets them itself
    # (see dispatch_memo_to_sage's own docstring for why the per-Memo version was reverted the same
    # day it shipped). Exactly one of these two is ever set once that mechanism is live. A Sage
    # failure should never block the send action itself, so sage_invoice_error existing would be a
    # visible "this still needs fixing", not something that undoes sent_at.
    sage_invoice_id = models.CharField(max_length=50, blank=True, null=True)
    sage_invoice_error = models.TextField(blank=True, null=True)

    # Scenario 4 only (Owner.is_paid_regularly=True, cleans_are_invoiced=False, see
    # OwnerInvoice's own docstring for the full 4-scenario matrix): these owners are never
    # formally invoiced for cleans/meet-greet and it's never deducted from their payout either -
    # some simply choose to pay as soon as they receive this Memo. A genuine toggle (settable and
    # unsettable), not a create-only record like PayoutRecord/DepositReturn - those model
    # irreversible real-world events; this models a staff *belief* about an informal payment that
    # may need correcting. Purely informational - no Sage/Revolut involvement ("no API connection
    # for this type of payment endpoint", Thomas, 2026-09-10) and no payout-math interaction
    # (a regular owner's payout never deducts management fee regardless of this flag).
    management_fee_paid_at = models.DateTimeField(null=True, blank=True)
    management_fee_paid_by = models.ForeignKey(
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


class DepositReturn(models.Model):
    """The only place 'has this booking's cash security deposit actually been returned' is
    recorded - same role as PayoutRecord above, one row per booking, created only when staff click
    "Mark as returned" on the Deposits tab (staff/views.py::
    StaffFinanceDepositReturnMarkReturnedView). A booking with no row here simply hasn't had its
    deposit returned yet - no separate "not returned" state to manage.

    amount snapshots BookingSettings.security_deposit_amount at the moment of marking returned,
    rather than being re-derived live, since that setting could change later - what was actually
    handed back must stay fixed for audit purposes even if the live setting differs afterward.
    booking is CASCADE, matching PayoutRecord."""
    booking = models.OneToOneField('bookings.Booking', on_delete=models.CASCADE, related_name='deposit_return')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    returned_at = models.DateTimeField(auto_now_add=True)
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'finance_deposit_returns'
        verbose_name = 'Deposit Return'
        verbose_name_plural = 'Deposit Returns'
        ordering = ('-returned_at',)

    def __str__(self):
        return f"{self.booking} deposit returned {self.amount} on {self.returned_at:%Y-%m-%d}"


class OwnerInvoice(models.Model):
    """A real Sage One invoice charged to a property owner for commission and/or cleans/meet-greet
    fees - added 2026-09-10 for the real "everyday owner payout and payment handling" system,
    covering the 4-scenario billing matrix Thomas defined (Owner.is_paid_regularly x
    Owner.cleans_are_invoiced):

      1. Regular + invoiced:     commission invoice per payout (pre-settled, see below) +
                                  ONE real monthly Sage invoice for cleans/meet-greet (Revolut link)
      2. Not regular + invoiced: ONE combined monthly Sage invoice (commission + cleans/meet-greet)
      3. Not regular + not inv.: monthly Sage invoice for commission only
      4. Regular + not invoiced: commission invoice per payout (pre-settled); cleans/meet-greet
                                  never formally invoiced - see Memo.management_fee_paid_at instead

    NOT finance.Memo (an informational per-clean record) and NOT finance.PayoutRecord (an
    outflow: money KLT sent the owner) - this is the inflow side, money charged to the owner.

    One table with a `kind` discriminator rather than four separate models - they differ only in
    which figures got summed and whether Revolut applies, not in what the row represents.

    COMMISSION_PAYOUT rows (scenarios 1 & 4) are issued already settled (status='paid', paid_at
    set at creation) - an invoice+receipt pair, not a live request for payment, since the
    commission was already collected via the owner's payout deduction (that math is unchanged,
    see finance/services.py::compute_regular_owner_payout). No Revolut fields ever get populated
    for this kind. CLEANS_MONTHLY (scenario 1) is the one genuine, live request for payment - a
    Revolut order gets created and its fields populated. COMMISSION_MONTHLY/COMBINED_MONTHLY
    (scenarios 2/3) get neither - settlement there is structural, already netted out of the
    owner's end-of-month payout, same as it is today.

    CLEANS_INFORMAL_MONTHLY (2026-09-10) is a fifth kind, for owners never formally invoiced for
    cleans/meet-greet at all - not limited to scenario 4, also a true management-only owner with no
    booking relationship (see finance/services.py::_needs_informal_cleans_tracking). Neither Sage
    nor Revolut - a manually-consolidated bundle of individually-unpaid Memos
    (finance/services.py::consolidate_informal_cleans_payment), manually mark-paid on the Expected
    Payments tab."""

    class Kind(models.TextChoices):
        COMMISSION_PAYOUT = 'commission_payout', 'Rental commission (per payout)'
        CLEANS_MONTHLY = 'cleans_monthly', 'Cleans & meet-greet (monthly)'
        COMMISSION_MONTHLY = 'commission_monthly', 'Rental commission (monthly)'
        COMBINED_MONTHLY = 'combined_monthly', 'Commission + cleans (monthly)'
        # A consolidated bundle of individually-unpaid Memos (finance/services.py::
        # consolidate_informal_cleans_payment, 2026-09-10) for an owner who isn't formally invoiced
        # for cleans/meet-greet at all (see _needs_informal_cleans_tracking - not just scenario 4,
        # also a true management-only owner with no booking relationship). No Sage dispatch, no
        # Revolut order - purely an internal record, manually mark-paid on the Expected Payments
        # tab. period_start stays null (like COMMISSION_PAYOUT) - this is a rolling bundle, not one
        # fixed calendar month.
        CLEANS_INFORMAL_MONTHLY = 'cleans_informal_monthly', 'Cleans & meet-greet (informal monthly)'

    owner = models.ForeignKey('properties.Owner', on_delete=models.PROTECT, related_name='invoices')
    kind = models.CharField(max_length=30, choices=Kind.choices)

    # COMMISSION_PAYOUT only - the one PayoutRecord this bills for. The OneToOne is the
    # idempotency guard for the per-payout trigger: a second attempt for the same PayoutRecord
    # fails fast (hasattr check in finance/services.py) rather than creating a duplicate.
    payout_record = models.OneToOneField(
        'finance.PayoutRecord', on_delete=models.PROTECT, null=True, blank=True,
        related_name='commission_invoice',
    )
    # *_MONTHLY kinds only - always the 1st of the billed month. Paired with the unique
    # constraint below - the idempotency guard the monthly batch command depends on to be safely
    # re-runnable without double-invoicing.
    period_start = models.DateField(null=True, blank=True)

    # Audit trail - which bookings' commission / which sent Memos' cleans fees fed this invoice.
    bookings = models.ManyToManyField('bookings.Booking', blank=True, related_name='owner_invoices')
    memos = models.ManyToManyField('finance.Memo', blank=True, related_name='owner_invoices')

    # Both VAT-INCLUSIVE totals - the real, final amount the owner is charged/pays (confirmed
    # 2026-09-10, per Thomas). finance/services.py::dispatch_owner_invoice_to_sage backs a net
    # (pre-VAT) figure out of total() before the Sage API call, using
    # PaymentSettings.vat_rate_percent - Sage adds the same rate back on top when it renders the
    # invoice, so the two must always net back to exactly total(), never a different amount.
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cleans_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Sage - same shape/semantics as Memo.sage_invoice_id/sage_invoice_error.
    sage_invoice_id = models.CharField(max_length=50, blank=True, null=True)
    sage_invoice_error = models.TextField(blank=True, null=True)

    # Revolut - see class docstring for which kinds ever populate these. Field shape copied
    # field-for-field from bookings.models.Payment.
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES, blank=True, null=True)
    status = models.CharField(max_length=15, choices=PAYMENT_STATUS_CHOICES, blank=True, null=True)
    revolut_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    revolut_checkout_url = models.URLField(blank=True, null=True)
    last_event_type = models.CharField(max_length=100, blank=True, null=True)
    in_progress_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'finance_owner_invoices'
        verbose_name = 'Owner Invoice'
        verbose_name_plural = 'Owner Invoices'
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'kind', 'period_start'], name='unique_owner_kind_period',
                condition=models.Q(period_start__isnull=False),
            ),
        ]
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.owner} - {self.get_kind_display()} (€{self.total()})"

    def total(self):
        return self.commission_amount + self.cleans_amount


class SageSettings(models.Model):
    """Singleton (pk always 1, same load()/save() pattern as bookings.models.BookingSettings) -
    the LIVE, rotating half of the Sage One connection. The registered app's own credentials
    (client_id/client_secret/signing_secret) are static config and live in env_settings.py, same
    convention as every other external API key in this codebase; access_token/refresh_token are
    the opposite - refresh_token rotates on every use (per Sage's docs), so they have to be
    read/written from somewhere mutable, not a .env file. Empty (all-null) until Thomas completes
    the one-time developer-portal registration and OAuth grant - see finance/services.py::
    dispatch_memo_to_sage's own docstring for what that involves.

    default_tax_rate_id is separate from the token pair - the Sage-side ID for standard Portuguese
    VAT (23%), looked up once via GET /accounts/v2/tax_rates and set by Thomas, since finance.Memo's
    clean_fee/meet_greet_fee don't carry their own VAT breakdown today. Used for EVERY OwnerInvoice
    kind, including commission ones - a separate 0%/exempt rate was considered (and briefly added
    as commission_tax_rate_id, 2026-09-10) but dropped the same day once Thomas clarified
    commission_amount/cleans_amount are both VAT-INCLUSIVE totals: finance/services.py::
    dispatch_owner_invoice_to_sage backs the pre-VAT net figure out of that total using this same
    rate, rather than needing a distinct exempt one - which is just as well, since this Sage
    account's tax_rates catalog has no 0%/exempt entry at all (confirmed 2026-09-10 - only
    STANDARD 23% and STANDARD_OSS cross-border rates exist)."""
    access_token = models.CharField(max_length=200, blank=True, null=True)
    refresh_token = models.CharField(max_length=200, blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    default_tax_rate_id = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'finance_sage_settings'
        verbose_name = 'Sage Settings'
        verbose_name_plural = 'Sage Settings'

    def __str__(self):
        return "Sage Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings
