from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

import env_settings
from bookings.models import Booking, PaymentSettings
from bookings.payouts import (
    ZERO, _commission_percent, _due_date, _is_platform_booking, _off_platform_cash, _round,
    _unavailable, clean_fee, compute_owner_payout, meet_greet_fee,
)
from bookings.utils import exclude_block_bookings
from finance.models import AdHocService, Memo, OwnerInvoice, PayoutRecord, SageSettings
from properties.models import Owner, Property
from staff.models import CleaningTask


def open_memo_for_property(property):
    """The earliest not-yet-sent Memo for this property whose CleaningTask date is still today or
    in the future - i.e. the next upcoming turnover clean's memo. Excludes orphaned memos
    (cleaning_task=None) by construction, since they have no date to compare. Returns None if no
    such Memo exists yet (nothing scheduled)."""
    return Memo.objects.filter(
        property=property, sent_at__isnull=True, cleaning_task__isnull=False,
        cleaning_task__date__gte=timezone.now().date(),
    ).order_by('cleaning_task__date').first()


def sweep_unattached_ad_hoc_services(property):
    """Attaches every currently-unattached AdHocService for this property onto its current open
    memo, if one exists. Called (a) whenever a Memo becomes/stays the open one during
    sync_memo_for_turnover_task, (b) explicitly from the Send view right after sent_at is set, so
    a stray service immediately rolls onto whatever is now the new open memo."""
    memo = open_memo_for_property(property)
    if memo is not None:
        AdHocService.objects.filter(property=property, memo__isnull=True).update(memo=memo)


def sync_memo_for_turnover_task(booking):
    """Keeps Memo in sync with the booking's turnover CleaningTask - called from the same
    staff/signals.py receivers that already call staff.utils.sync_cleaning_tasks_for_booking
    (plus the Arrival receiver, since a Memo's meet-greet line depends on Arrival.meet_greet,
    which CleaningTask itself doesn't care about), immediately after that call, so it always sees
    the up-to-date CleaningTask state.

    IMPORTANT: Memo.cleaning_task's on_delete=SET_NULL is a queryset-level cascade that does NOT
    trigger Memo.save()/post_save - Django's Collector performs a raw UPDATE for SET_NULL, it
    doesn't re-save the related row. So the "release this orphaned memo's ad-hoc services back to
    memo=None" step can't be hung off a Memo signal; it's done explicitly here, every time this
    function runs, scoped to the property (not to this one booking, since there's no booking FK on
    Memo to look up the specific orphan by - any unsent orphaned memo for the property is swept,
    which is idempotent and correct regardless of which booking's cancellation caused it)."""
    property = booking.property
    cleaning_company = property.cleaning_company

    orphaned = Memo.objects.filter(property=property, sent_at__isnull=True, cleaning_task__isnull=True)
    AdHocService.objects.filter(memo__in=orphaned).update(memo=None)

    if cleaning_company is None or not cleaning_company.finances_managed_internally:
        return  # not opted in - leave any existing Memo rows alone, no retroactive delete

    task = CleaningTask.objects.filter(booking=booking, task_type='turnover').first()
    if task is None:
        sweep_unattached_ad_hoc_services(property)  # a different memo may now be open
        return

    payment_settings = PaymentSettings.load()
    fee = _round(clean_fee(payment_settings, booking))
    greet = _round(meet_greet_fee(payment_settings, booking))

    memo, created = Memo.objects.get_or_create(
        cleaning_task=task, defaults={'property': property, 'clean_fee': fee, 'meet_greet_fee': greet},
    )
    if not created and memo.sent_at is None and (memo.clean_fee, memo.meet_greet_fee) != (fee, greet):
        memo.clean_fee, memo.meet_greet_fee = fee, greet
        memo.save(update_fields=['clean_fee', 'meet_greet_fee'])

    sweep_unattached_ad_hoc_services(property)


def _sage_client():
    """A ready-to-use libraries.accounting.sage.Sage, with a valid (refreshed if necessary)
    access_token - or None if Sage isn't configured/connected yet. Refreshing rewrites
    SageSettings' access_token/refresh_token/token_expires_at immediately (refresh_token itself
    rotates on every use, per Sage's docs - the old one becomes worthless the instant this
    succeeds, so it must be persisted before this function returns, not left for the caller)."""
    from libraries.accounting.sage import Sage, refresh_access_token

    if not (env_settings.SAGE_CLIENT_ID and env_settings.SAGE_CLIENT_SECRET and env_settings.SAGE_SIGNING_SECRET):
        return None

    sage_settings = SageSettings.load()
    if not sage_settings.refresh_token:
        return None  # Thomas hasn't done the one-time OAuth grant yet

    if sage_settings.access_token and sage_settings.token_expires_at and sage_settings.token_expires_at > timezone.now():
        return Sage(access_token=sage_settings.access_token, signing_secret=env_settings.SAGE_SIGNING_SECRET)

    tokens = refresh_access_token(
        env_settings.SAGE_CLIENT_ID, env_settings.SAGE_CLIENT_SECRET, sage_settings.refresh_token,
    )
    if tokens is None:
        return None
    sage_settings.access_token = tokens['access_token']
    sage_settings.refresh_token = tokens['refresh_token']
    sage_settings.token_expires_at = timezone.now() + timedelta(seconds=tokens['expires_in'])
    sage_settings.save(update_fields=['access_token', 'refresh_token', 'token_expires_at'])
    return Sage(access_token=sage_settings.access_token, signing_secret=env_settings.SAGE_SIGNING_SECRET)


def _get_or_create_sage_contact(sage, owner):
    """Finds (by name) or creates the Sage contact for this owner, persisting the id onto
    Owner.sage_contact_id once found so future calls skip the lookup - shared by
    dispatch_memo_to_sage and dispatch_commission_receipt_for_payout/the monthly batch job below,
    extracted 2026-09-10 rather than duplicated a third/fourth time. Returns the contact id, or
    None on failure (caller records the error on its own object, this stays framework-agnostic
    about where that error message goes)."""
    if owner.sage_contact_id:
        return owner.sage_contact_id
    existing = sage.contact.find_by_name(owner.name)
    if existing is not None:
        contact_id = existing['id']
    else:
        created = sage.contact.create(owner.name, email=owner.email, tax_number=owner.nif_number)
        if created is None:
            return None
        contact_id = created['id']
    owner.sage_contact_id = contact_id
    owner.save(update_fields=['sage_contact_id'])
    return contact_id


def dispatch_memo_to_sage(memo):
    """Creates a real Sage One invoice for a single Memo, if its property's owner opted in
    (properties.models.Owner.cleans_are_invoiced - restored 2026-09-09, per Thomas, for exactly
    this).

    NOT currently called from anywhere (2026-09-09) - briefly wired into
    staff/views.py::StaffFinanceMemoSendView the same day, then deliberately unwired once Thomas
    clarified the real billing model: cleans/meet-greet invoicing to Sage happens as one batched
    invoice per owner per month, not one invoice per Memo. This function's contact-lookup/creation
    and error-recording logic will likely be reused/adapted when that monthly batch job is built,
    but as it stands today it would create one incorrect Sage invoice per clean - left in place,
    unused, rather than deleted, for exactly that reuse.

    a no-op (not an error) for an owner who hasn't opted in, or who has no owner at all.

    Getting a real Sage connection working requires a one-time, Thomas-only manual step this
    function cannot perform: registering a developer app in Sage's portal (developers.sageone.com)
    and completing the OAuth2 authorization grant as the Sage One account owner - see
    finance.SageSettings' own docstring for what that populates. Until then, this always ends in
    sage_invoice_error being set, which is expected, not a bug.

    Deliberately never raises - a Sage failure (not yet connected, contact/invoice creation
    rejected, etc.) is recorded on the memo (sage_invoice_error) but never blocks the send action
    itself, which staff already rely on as a plain record-keeping step independent of any
    downstream delivery succeeding (same reasoning StaffFinanceMemoSendView's own docstring
    already gives for why emailing the memo is out of scope there)."""
    owner = getattr(memo.property, 'owner', None)
    if owner is None or not owner.cleans_are_invoiced:
        return

    sage = _sage_client()
    if sage is None:
        memo.sage_invoice_error = 'Sage One is not connected yet.'
        memo.save(update_fields=['sage_invoice_error'])
        return

    sage_settings = SageSettings.load()
    if not sage_settings.default_tax_rate_id:
        memo.sage_invoice_error = 'No default Sage tax rate configured (finance.SageSettings.default_tax_rate_id).'
        memo.save(update_fields=['sage_invoice_error'])
        return

    contact_id = _get_or_create_sage_contact(sage, owner)
    if contact_id is None:
        memo.sage_invoice_error = 'Failed to find or create a Sage contact for this owner.'
        memo.save(update_fields=['sage_invoice_error'])
        return

    invoice_date = memo.cleaning_task.date if memo.cleaning_task else timezone.now().date()
    payment_settings = PaymentSettings.load()
    net_amount = _round(memo.total() / (1 + payment_settings.vat_rate_percent / Decimal('100')))
    invoice = sage.sales_invoice.create(
        contact_id=contact_id, date=invoice_date,
        description=f'{memo.property} - cleaning & meet-greet',
        net_amount=net_amount, tax_rate_id=sage_settings.default_tax_rate_id,
    )
    if invoice is None:
        memo.sage_invoice_error = 'Failed to create the Sage sales invoice.'
        memo.save(update_fields=['sage_invoice_error'])
        return

    memo.sage_invoice_id = invoice['id']
    memo.sage_invoice_error = None
    memo.save(update_fields=['sage_invoice_id', 'sage_invoice_error'])


def dispatch_owner_invoice_to_sage(invoice, description):
    """Creates the real Sage sales invoice for an already-created OwnerInvoice row, recording
    sage_invoice_id/sage_invoice_error on it exactly like dispatch_memo_to_sage does on a Memo.
    Deliberately never raises - same reasoning as dispatch_memo_to_sage's own docstring.

    invoice.total() (commission_amount + cleans_amount) is the real, final VAT-INCLUSIVE amount
    the owner is actually charged/pays - the same figure create_revolut_order_for_owner_invoice
    requests via Revolut. Sage's own `net_amount` field is pre-VAT (confirmed 2026-09-10, per
    Thomas: commission "needs to deduct 23% for VAT anyway" - both commission and cleans/
    meet-greet are VAT-inclusive totals, backed out to a net figure here, then Sage adds the same
    23% back on top when it renders the invoice - so the two must always net back to exactly
    invoice.total(), never a different amount. Uses the one standard tax rate
    (SageSettings.default_tax_rate_id) for everything now - there's no separate 0%/exempt rate in
    this Sage account's catalog at all (confirmed 2026-09-10: only STANDARD 23% and STANDARD_OSS
    cross-border rates exist), and this VAT-inclusive/back-calculated approach makes a separate
    rate unnecessary anyway."""
    sage = _sage_client()
    if sage is None:
        invoice.sage_invoice_error = 'Sage One is not connected yet.'
        invoice.save(update_fields=['sage_invoice_error'])
        return

    sage_settings = SageSettings.load()
    if not sage_settings.default_tax_rate_id:
        invoice.sage_invoice_error = 'No default Sage tax rate configured (finance.SageSettings.default_tax_rate_id).'
        invoice.save(update_fields=['sage_invoice_error'])
        return

    contact_id = _get_or_create_sage_contact(sage, invoice.owner)
    if contact_id is None:
        invoice.sage_invoice_error = 'Failed to find or create a Sage contact for this owner.'
        invoice.save(update_fields=['sage_invoice_error'])
        return

    payment_settings = PaymentSettings.load()
    net_amount = _round(invoice.total() / (1 + payment_settings.vat_rate_percent / Decimal('100')))
    sage_invoice = sage.sales_invoice.create(
        contact_id=contact_id, date=invoice.created_at.date(),
        description=description, net_amount=net_amount, tax_rate_id=sage_settings.default_tax_rate_id,
    )
    if sage_invoice is None:
        invoice.sage_invoice_error = 'Failed to create the Sage sales invoice.'
        invoice.save(update_fields=['sage_invoice_error'])
        return

    invoice.sage_invoice_id = sage_invoice['id']
    invoice.sage_invoice_error = None
    invoice.save(update_fields=['sage_invoice_id', 'sage_invoice_error'])


def create_revolut_order_for_owner_invoice(invoice):
    """Owner-facing sibling of bookings/views.py's four near-identical guest-facing
    _create_revolut_order methods - same call shape, libraries/banking/revolut.py::Revolut.Payment
    is already fully payer-agnostic. Only ever called for OwnerInvoice.Kind.CLEANS_MONTHLY - the
    one kind that's a genuine, live request for payment (see OwnerInvoice's own docstring). Silent
    no-op on failure, same convention as the Sage dispatch functions - staff can see an invoice
    with no checkout link and know to investigate/retry, rather than this blocking anything."""
    from libraries.banking.revolut import Revolut

    owner = invoice.owner
    order = Revolut(secretKey=env_settings.REVOLUT_API_SECRET_KEY).payment
    order.amount = int(invoice.total() * 100)
    order.currency = 'EUR'
    order.description = f'{invoice.get_kind_display()} - {owner.name}'
    order.customerEmail = owner.email
    order.customerName = owner.name
    order.create()
    if order.id and order.has('checkout_url'):
        invoice.provider = 'revolut'
        invoice.status = 'pending'
        invoice.revolut_order_id = order.id
        invoice.revolut_checkout_url = order.checkoutUrl
        invoice.save(update_fields=['provider', 'status', 'revolut_order_id', 'revolut_checkout_url'])


def dispatch_commission_receipt_for_payout(payout_record, payout):
    """Issues OwnerInvoice(kind=COMMISSION_PAYOUT) for a just-created PayoutRecord, already
    marked settled (status='paid', paid_at=now()) - an invoice+receipt pair, not a live request
    for payment, since the commission was already collected via the payout deduction itself (see
    compute_regular_owner_payout - that math is unchanged, still deducts commission). This is
    purely a formal Sage-side record of the charge, avoiding "sending out money part of which
    we're asking back" (Thomas, 2026-09-10). Only for is_paid_regularly=True owners (scenarios 1
    & 4 - commission is always invoiced now, no per-owner opt-out). Idempotent via
    payout_record's OneToOneField - a second call for the same payout is a safe no-op. Never
    raises - a Sage failure is recorded on the invoice, never blocks Mark-as-paid itself."""
    owner = payout_record.booking.property.owner
    if owner is None or not owner.is_paid_regularly or hasattr(payout_record, 'commission_invoice'):
        return None

    invoice = OwnerInvoice.objects.create(
        owner=owner, kind=OwnerInvoice.Kind.COMMISSION_PAYOUT, payout_record=payout_record,
        commission_amount=payout['commission'], status='paid', paid_at=timezone.now(),
    )
    invoice.bookings.add(payout_record.booking)
    dispatch_owner_invoice_to_sage(
        invoice, description=f'{payout_record.booking.property} - rental commission ({payout_record.booking.reference})',
    )
    return invoice


def compute_regular_owner_payout(booking, payment_settings=None):
    """The real, everyday payout figure for a regularly-paid owner (Owner.is_paid_regularly=True)
    - deliberately separate from bookings/payouts.py::compute_owner_payout(), which stays
    untouched as a pure display calculation for the Booking View (2026-09-10, per Thomas: klt-web
    is a brand-new system still being built up to match how the business actually runs, and the
    Booking View's number was never meant to double as the real disbursement figure - "don't be
    afraid of building separate functions... to nail down the perfect business flow").

    Differs from compute_owner_payout in two ways: management_fee (clean + meet-greet) is NEVER
    deducted here, and neither are ad-hoc owner payments (staff.models.OwnerPayment) - Thomas,
    2026-09-10: those are themselves management-fee-category charges (cleans/meet-greet), so they
    fall under the same rule. A regularly-paid owner's payout only ever nets out commission ("it is
    only the rental commission that gets automatically deducted"). Commission itself is still
    deducted exactly as before - see dispatch_commission_receipt_for_payout for why that's fine (a
    pre-settled invoice+receipt documents the charge, it doesn't ask for money twice). Cleans/
    meet-greet fees (ad-hoc or standard) are settled separately - either a real monthly Sage
    invoice (Owner.cleans_are_invoiced=True) or an informal, optional payment tracked via Memo's
    own management_fee_paid_at (cleans_are_invoiced=False) - never via this payout either way.

    Reuses bookings/payouts.py's private helpers directly rather than duplicating them - same
    framework-agnostic "import what's needed" convention already used elsewhere in this codebase."""
    if booking.is_owner:
        return _unavailable("Owner stay - no payout due.")

    owner = booking.property.owner
    if owner is None:
        return _unavailable("Property has no owner assigned.")
    if not owner.is_paid_regularly:
        return _unavailable("Owner is not paid on a regular schedule.")

    is_platform = _is_platform_booking(booking)
    if is_platform:
        platform_payout = getattr(booking, 'platform_payout', None)
        if platform_payout is None or platform_payout.payout_amount is None:
            return _unavailable("No PlatformPayout figures recorded yet.")
        rental_base = platform_payout.payout_amount
        platform_fee = platform_payout.platform_commission or ZERO
        off_platform_cash = _off_platform_cash(booking)
    else:
        charge = getattr(booking, 'charges', None)
        if charge is None or charge.basic_rental is None:
            return _unavailable("No Charge record for this booking.")
        rental_base = charge.total_rental
        platform_fee = ZERO
        off_platform_cash = ZERO

    if payment_settings is None:
        payment_settings = PaymentSettings.load()

    commission_percent = _commission_percent(payment_settings, booking.arrival_date)
    commission = _round((rental_base + off_platform_cash) * commission_percent / Decimal('100'))
    platform_fee_vat = (
        _round(platform_fee * payment_settings.vat_rate_percent / Decimal('100')) if is_platform else ZERO
    )

    owner_balance = rental_base + off_platform_cash - commission - platform_fee_vat

    return {
        'available': True,
        'reason': None,
        'rental_base': rental_base,
        'off_platform_cash': off_platform_cash,
        'commission_percent': commission_percent,
        'commission': commission,
        'platform_fee': platform_fee,
        'platform_fee_vat': platform_fee_vat,
        'owner_balance': owner_balance,
        'due_date': _due_date(payment_settings, owner, booking.arrival_date),
    }


def backfill_memos_for_company(company, start=None):
    """Syncs Memo rows for this company's properties' turnover CleaningTasks dated on/after
    `start` (default: today) - the retroactive half of turning finances_managed_internally on for
    a company after tasks already exist (properties.models.ManagementCompany.
    finances_managed_internally's own docstring: that toggle doesn't backfill on its own). Called
    from StaffSettingsView._update_management_company right after a save that flips the flag from
    False to True. Bounded to `start`-or-later, same "no value memo-izing an already-past clean
    that was never billed contemporaneously" reasoning as finance/management/commands/
    sync_finance_memos.py (which stays in place for a manual/global re-reconcile - this is the
    narrower, automatic, per-company version of the same idea). Returns the count synced."""
    if start is None:
        start = timezone.now().date()
    tasks = CleaningTask.objects.filter(
        task_type='turnover', date__gte=start, booking__property__cleaning_company=company,
    ).select_related('booking')

    count = 0
    for task in tasks:
        sync_memo_for_turnover_task(task.booking)
        count += 1
    return count


def recompute_unsent_memo_fees_for_settings_change():
    """Called from staff/signals.py on PaymentSettings' own post_save - mirrors
    staff/utils.py::resync_checkin_times_for_settings_change() exactly: a changed
    cleaning_surcharge_*/meet_greet_fee is a real money correctness issue for every not-yet-sent
    Memo, not just cosmetic staleness, so every one is recomputed immediately rather than waiting
    for an unrelated save on its booking."""
    payment_settings = PaymentSettings.load()
    memos = Memo.objects.filter(sent_at__isnull=True, cleaning_task__isnull=False).select_related(
        'cleaning_task__booking__property__specs', 'cleaning_task__booking__departure',
        'cleaning_task__booking__arrival',
    )
    for memo in memos:
        booking = memo.cleaning_task.booking
        fee = _round(clean_fee(payment_settings, booking))
        greet = _round(meet_greet_fee(payment_settings, booking))
        if (memo.clean_fee, memo.meet_greet_fee) != (fee, greet):
            memo.clean_fee, memo.meet_greet_fee = fee, greet
            memo.save(update_fields=['clean_fee', 'meet_greet_fee'])


def _payouts_due_in_range(bookings_queryset, start, end, compute_fn=compute_owner_payout):
    """Shared by payouts_due_in_range() and owner_balance_in_range() below - given a bookings
    queryset already scoped to whatever properties/owners the caller cares about, computes each
    booking's owner payout and keeps only the ones whose due_date falls within [start, end].
    due_date isn't a stored column (it depends on PaymentSettings and the owner's
    is_paid_regularly flag via bookings/payouts.py::_due_date), so this can't be a single indexed
    queryset filter - it widens the candidate window on arrival_date (generously enough to cover
    both of _due_date's branches: same-month-end for non-regular owners, or
    +regular_payout_days_after_arrival for regular owners) and then computes/filters in Python.
    Fine at this business's booking volume; not a single indexed query, flagged as a known
    trade-off.

    compute_fn defaults to compute_owner_payout (bookings/payouts.py's untouched Booking-View
    calculation, still correct for owner_balance_in_range's non-regular-owner callers) but
    payouts_due_in_range below passes compute_regular_owner_payout instead, since it's the real
    everyday-payout figure, not a display one - see that function's own docstring.

    Returns a list of (booking, payout_dict) tuples, payout_dict always 'available' (unavailable
    bookings are silently excluded - nothing to show or pay out). Also excludes calendar-blocking
    placeholder bookings (bookings/utils.py::exclude_block_bookings) - these carry no rental
    income and represent nobody's actual stay, but is_owner=False alone no longer keeps them out
    (that flag was corrected 2026-09-09 to mean what it actually says - see that function's own
    docstring); without this they'd otherwise show up here as spurious €0.00 payout rows. And
    restricts to enquiry_status__in=VALID_BOOKING_STATUSES ('Booking confirmed' only) - same
    convention staff/reports.py and staff/monthly_reports.py already use for every other
    financial total, previously missing here entirely, which let a mere 'Open enquiry' (already
    holding a provisional Charge) show up as a real payout due (found live 2026-09-10)."""
    payment_settings = PaymentSettings.load()
    candidates = exclude_block_bookings(bookings_queryset.filter(
        is_owner=False, enquiry_status__in=env_settings.VALID_BOOKING_STATUSES,
        arrival_date__range=(start - timedelta(days=45), end),
    )).select_related(
        'property__owner', 'property__booking_company', 'property__cleaning_company', 'property__specs',
        'charges', 'platform_payout', 'departure', 'arrival',
    ).prefetch_related('owner_payments')

    results = []
    for booking in candidates:
        payout = compute_fn(booking, payment_settings)
        if payout['available'] and start <= payout['due_date'] <= end:
            results.append((booking, payout))
    return results


def payouts_due_in_range(start, end):
    """Bookings whose computed owner payout is due within [start, end], on a property whose
    booking_company has finances_managed_internally=True and whose owner is paid regularly - the
    Payouts tab's own query (StaffFinancePayoutsView). Uses compute_regular_owner_payout, NOT
    compute_owner_payout (2026-09-10) - this is the real everyday-payout system, not the Booking
    View display; see compute_regular_owner_payout's own docstring for why they diverge."""
    return _payouts_due_in_range(Booking.objects.filter(
        property__owner__is_paid_regularly=True,
        property__booking_company__finances_managed_internally=True,
    ), start, end, compute_fn=compute_regular_owner_payout)


def deposits_due_in_range(start, end):
    """Bookings whose cash security deposit is due for return: the arrival Checkin was marked
    deposit_collected, and the turnover CleaningTask has since been completed (both conditions
    per Thomas, 2026-08-29) - grouped by the clean's own completed_at date (when the booking
    actually became eligible), same [start, end] window convention as payouts_due_in_range. There
    is no computed amount here beyond the flat BookingSettings.security_deposit_amount figure
    (applied by the caller/mark-returned view), unlike a payout - so this returns plain
    (booking, completed_date) tuples rather than a payout-dict pair."""
    tasks = CleaningTask.objects.filter(
        task_type='turnover', status='done', completed_at__date__range=(start, end),
        booking__checkins__task_type='arrival', booking__checkins__deposit_collected=True,
    ).select_related('booking__property__owner').distinct()
    return [(task.booking, task.completed_at.date()) for task in tasks]


def owner_balance_in_range(property, start, end):
    """Bookings on this one property whose computed owner payout is due within [start, end] -
    used by Statement generation (StaffFinanceStatementView) for a non-regularly-paid owner, where
    the finances_managed_internally/is_paid_regularly gating has already been checked by the
    caller before calling this, unlike payouts_due_in_range's own baked-in filter (which is
    specifically for the regular-owner Payouts tab and would wrongly exclude this case)."""
    return _payouts_due_in_range(Booking.objects.filter(property=property), start, end)


def _commission_in_range(owner, start, end):
    """Sums payout['commission'] across every booking due in this period, on every property of
    this owner whose booking_company has finances_managed_internally=True - same gating
    StaffFinanceStatementView already applies before calling owner_balance_in_range for the same
    reason. Shared by generate_non_regular_owner_invoice/generate_scenario_1_cleans_invoice below
    (moved out of the management command 2026-09-10 so the CLI batch path and the interactive
    Payouts-tab 'Generate' button use the same math, not two copies of it)."""
    total = ZERO
    bookings = []
    properties = Property.objects.filter(owner=owner, booking_company__finances_managed_internally=True)
    for property in properties:
        for booking, payout in owner_balance_in_range(property, start, end):
            total += payout['commission']
            bookings.append(booking)
    return total, bookings


def _sent_memos_in_range(owner, start, end):
    return list(Memo.objects.filter(
        property__owner=owner, sent_at__date__range=(start, end),
    ).prefetch_related('ad_hoc_services'))


def _create_owner_invoice(
    owner, kind, period_start, dry_run, revolut,
    commission_amount=ZERO, cleans_amount=ZERO, bookings=(), memos=(),
):
    """Shared creation step for every *_MONTHLY OwnerInvoice kind, called only by
    generate_non_regular_owner_invoice and generate_scenario_1_cleans_invoice below. Returns
    (invoice_or_None, status) - status is one of 'created'/'already_invoiced'/'nothing_to_bill'/
    'dry_run', letting each caller (a CLI print, a Django message) format its own report without
    re-deriving what happened. Never raises - dispatch_owner_invoice_to_sage records a Sage
    failure on the invoice itself, same convention as every other dispatch function here."""
    total = commission_amount + cleans_amount
    if total == 0:
        return None, 'nothing_to_bill'

    if OwnerInvoice.objects.filter(owner=owner, kind=kind, period_start=period_start).exists():
        return None, 'already_invoiced'

    if dry_run:
        return None, 'dry_run'

    invoice = OwnerInvoice.objects.create(
        owner=owner, kind=kind, period_start=period_start,
        commission_amount=commission_amount, cleans_amount=cleans_amount,
    )
    if bookings:
        invoice.bookings.set(bookings)
    if memos:
        invoice.memos.set(memos)

    dispatch_owner_invoice_to_sage(invoice, description=f'{owner} - {kind.label} - {period_start:%B %Y}')
    if revolut:
        create_revolut_order_for_owner_invoice(invoice)

    return invoice, 'created'


def generate_non_regular_owner_invoice(owner, period_start, period_end, dry_run=False):
    """Creates + dispatches this month's OwnerInvoice for one is_paid_regularly=False owner
    (scenarios 2 & 3 of the 4-scenario billing matrix - see OwnerInvoice's own docstring). kind is
    COMBINED_MONTHLY (commission + cleans/meet-greet) when owner.cleans_are_invoiced=True
    (scenario 2), else COMMISSION_MONTHLY (scenario 3 - commission only; cleans/meet-greet stays
    informational-only via Memo either way, see Memo.management_fee_paid_at). No Revolut order -
    settlement for these two scenarios is structural, already netted out of the owner's month-end
    payout (see non_regular_owner_balances_due_in_range), not a live request for payment.

    Shared by finance/management/commands/generate_monthly_owner_invoices.py (the CLI/cron batch
    path) and staff/views.py::StaffFinanceOwnerPayoutGenerateView (the interactive month-end
    Payouts-tab 'Generate' button, 2026-09-10) - one place this billing logic lives. Returns
    (None, 'not_applicable') for a regularly-paid owner - see generate_scenario_1_cleans_invoice
    for that case. See _create_owner_invoice for the rest of the return contract."""
    if owner.is_paid_regularly:
        return None, 'not_applicable'

    commission_amount, bookings = _commission_in_range(owner, period_start, period_end)
    cleans_amount = ZERO
    memos = []
    if owner.cleans_are_invoiced:
        kind = OwnerInvoice.Kind.COMBINED_MONTHLY
        memos = _sent_memos_in_range(owner, period_start, period_end)
        cleans_amount = sum((memo.total() for memo in memos), ZERO)
    else:
        kind = OwnerInvoice.Kind.COMMISSION_MONTHLY

    return _create_owner_invoice(
        owner, kind, period_start, dry_run, revolut=False,
        commission_amount=commission_amount, cleans_amount=cleans_amount, bookings=bookings, memos=memos,
    )


def generate_scenario_1_cleans_invoice(owner, period_start, period_end, dry_run=False):
    """Scenario 1 (is_paid_regularly=True, cleans_are_invoiced=True) only - commission for these
    owners is already invoiced per-payout (see dispatch_commission_receipt_for_payout), so this
    only ever bills cleans/meet-greet, and it's the one *_MONTHLY kind that does create a Revolut
    order (a genuine, live request for payment). CLI/cron-only for now - no interactive UI calls
    this (2026-09-10); kept alongside generate_non_regular_owner_invoice purely so all monthly
    billing math lives in one place rather than splitting it between services.py and the command."""
    memos = _sent_memos_in_range(owner, period_start, period_end)
    cleans_amount = sum((memo.total() for memo in memos), ZERO)
    return _create_owner_invoice(
        owner, OwnerInvoice.Kind.CLEANS_MONTHLY, period_start, dry_run, revolut=True,
        cleans_amount=cleans_amount, memos=memos,
    )


def non_regular_owner_balances_due_in_range(start, end):
    """Aggregates compute_owner_payout's owner_balance (the real, final net-payout figure - unlike
    _commission_in_range's commission-only total, which is what gets documented as charged, not
    what gets transferred) per owner, across every is_paid_regularly=False owner's bookings whose
    payout is due within [start, end]. Every such booking's due_date lands on its arrival month's
    last day (bookings/payouts.py::_due_date), so passing one calendar month here returns exactly
    that month's owners - the aggregate, owner-level counterpart to payouts_due_in_range's
    per-booking regular-owner rows. Powers staff/views.py::StaffFinancePayoutsView's month-end
    owner cards (2026-09-10)."""
    rows = _payouts_due_in_range(Booking.objects.filter(
        property__owner__is_paid_regularly=False,
        property__booking_company__finances_managed_internally=True,
    ), start, end)

    by_owner = {}
    for booking, payout in rows:
        owner = booking.property.owner
        entry = by_owner.setdefault(owner.pk, {'owner': owner, 'owner_balance': ZERO, 'bookings': []})
        entry['owner_balance'] += payout['owner_balance']
        entry['bookings'].append(booking)
    return sorted(by_owner.values(), key=lambda entry: entry['owner'].name)


def owner_outstanding_balance(owner, as_of, property=None):
    """The real, current outstanding balance for one owner (or, with `property` given, that one
    property's share of it) - what KLT still owes the owner in payouts that haven't gone out yet,
    against what the owner still owes KLT for services already covered but not yet reimbursed.
    Added 2026-09-10 to answer this directly on the Statement tab (StaffFinanceStatementView),
    replacing the date-range activity snapshot it showed before.

    Looks back up to 730 days before `as_of` - a deliberate, generous-enough bound to keep the
    underlying query bounded, not a claim nothing could ever be older than that.

    For scenarios 2/3 (owner.is_paid_regularly=False), commission/cleans are already netted out of
    compute_owner_payout's owner_balance before it's ever paid - there's no independent
    'owed_by_owner' to track, only whether that month's net payout has itself gone out yet
    (OwnerInvoice.status == 'paid' - one combined invoice per owner per month covering every one of
    their properties together, not per-property). owed_by_owner is None for these two scenarios,
    not zero - callers/templates must treat None as 'not an independent figure', never as 'nothing
    owed'."""
    since = as_of - timedelta(days=730)
    base_queryset = Booking.objects.filter(property__booking_company__finances_managed_internally=True)
    base_queryset = base_queryset.filter(property=property) if property else base_queryset.filter(property__owner=owner)

    if owner.is_paid_regularly:
        rows = _payouts_due_in_range(base_queryset, since, as_of, compute_fn=compute_regular_owner_payout)
        paid_booking_ids = set(PayoutRecord.objects.filter(
            booking_id__in=[booking.pk for booking, _ in rows],
        ).values_list('booking_id', flat=True))
        owed_to_owner_rows = [
            {'booking': booking, 'payout': payout} for booking, payout in rows if booking.pk not in paid_booking_ids
        ]
        owed_to_owner_rows.sort(key=lambda row: row['payout']['due_date'])
        owed_to_owner = sum((row['payout']['owner_balance'] for row in owed_to_owner_rows), ZERO)

        memos_queryset = Memo.objects.filter(property__owner=owner, sent_at__isnull=False)
        if property:
            memos_queryset = memos_queryset.filter(property=property)
        memos = list(memos_queryset.prefetch_related('ad_hoc_services', 'owner_invoices'))
        if owner.cleans_are_invoiced:
            owed_by_owner_rows = [
                memo for memo in memos if not any(
                    invoice.kind == OwnerInvoice.Kind.CLEANS_MONTHLY and invoice.status == 'paid'
                    for invoice in memo.owner_invoices.all()
                )
            ]
        else:
            owed_by_owner_rows = [memo for memo in memos if memo.management_fee_paid_at is None]
        owed_by_owner = sum((memo.total() for memo in owed_by_owner_rows), ZERO)
    else:
        rows = _payouts_due_in_range(base_queryset, since, as_of)
        by_month = {}
        for booking, payout in rows:
            month_start = payout['due_date'].replace(day=1)
            entry = by_month.setdefault(month_start, {'month_start': month_start, 'owner_balance': ZERO, 'bookings': []})
            entry['owner_balance'] += payout['owner_balance']
            entry['bookings'].append(booking)

        paid_months = set(OwnerInvoice.objects.filter(
            owner=owner, kind__in=[OwnerInvoice.Kind.COMMISSION_MONTHLY, OwnerInvoice.Kind.COMBINED_MONTHLY],
            period_start__in=list(by_month.keys()), status='paid',
        ).values_list('period_start', flat=True))

        owed_to_owner_rows = sorted(
            (entry for month_start, entry in by_month.items() if month_start not in paid_months),
            key=lambda entry: entry['month_start'],
        )
        owed_to_owner = sum((entry['owner_balance'] for entry in owed_to_owner_rows), ZERO)
        owed_by_owner = None
        owed_by_owner_rows = []

    return {
        'owed_to_owner': owed_to_owner,
        'owed_to_owner_rows': owed_to_owner_rows,
        'owed_by_owner': owed_by_owner,
        'owed_by_owner_rows': owed_by_owner_rows,
        'net': owed_to_owner - (owed_by_owner or ZERO),
        'is_regular': owner.is_paid_regularly,
    }


def needs_informal_cleans_tracking(owner):
    """Whether this owner needs the Expected Payments tab's individual-Memo-toggle-then-consolidate
    mechanism (2026-09-10) - not just scenario 4. A scenario-3 owner (not regular, not invoiced) has
    their management fee already netted into compute_owner_payout's owner_balance the moment their
    payout goes out, so tracking it again separately here would double-count; a true management-only
    owner (no booking relationship with KLT at all) has nothing netting it anywhere, so needs this
    same mechanism scenario 4 uses."""
    if owner.cleans_are_invoiced:
        return False
    if owner.is_paid_regularly:
        return True
    return not Property.objects.filter(owner=owner, booking_company__finances_managed_internally=True).exists()


def consolidate_informal_cleans_payment(owner):
    """Bundles every currently-unpaid, never-yet-bundled sent Memo for this owner into one
    OwnerInvoice(kind=CLEANS_INFORMAL_MONTHLY) - Thomas's "one markable-paid line", 2026-09-10.
    Extends an existing open (status != 'paid') bundle if one exists rather than creating a second
    one - an owner only ever has at most one open bundle at a time. No Sage/Revolut involvement,
    matching every other needs_informal_cleans_tracking owner's payment mechanism. Returns the
    invoice (created or extended), or None if there was nothing eligible to bundle."""
    if not needs_informal_cleans_tracking(owner):
        return None

    candidates = list(Memo.objects.filter(
        property__owner=owner, sent_at__isnull=False, management_fee_paid_at__isnull=True, owner_invoices__isnull=True,
    ))
    if not candidates:
        return None

    invoice = OwnerInvoice.objects.filter(
        owner=owner, kind=OwnerInvoice.Kind.CLEANS_INFORMAL_MONTHLY,
    ).exclude(status='paid').first()
    if invoice is None:
        invoice = OwnerInvoice.objects.create(owner=owner, kind=OwnerInvoice.Kind.CLEANS_INFORMAL_MONTHLY)

    invoice.memos.add(*candidates)
    invoice.cleans_amount = sum((memo.total() for memo in invoice.memos.all()), ZERO)
    invoice.save(update_fields=['cleans_amount'])
    return invoice


def _memos_before_cutoff(queryset, cutoff):
    """Effective date for a Memo, for cutoff purposes: its clean's own date when it has one
    (cleaning_task can be null - see Memo's own docstring for why), else when the Memo itself was
    created - same fallback the Expected Payments tab already displays
    (finance_expected_payments.html: `cleaning_task.date|default:created_at`)."""
    return queryset.filter(
        Q(cleaning_task__date__lt=cutoff) | Q(cleaning_task__isnull=True, created_at__date__lt=cutoff)
    )


def reset_ledger_before_date(cutoff, dry_run=False):
    """One-off (and re-runnable) pre-launch ledger reset (2026-09-10, per Thomas): klt-web's
    owner-payment system is brand new and not live yet, so rather than trying to reconcile years
    of legacy manual tracking retroactively, everything dated before `cutoff` is presumed already
    settled - every owner starts the real, live system at EUR0.00. Marks the payment-tracking side
    of every mechanism this module has as settled; never touches or deletes the underlying
    Booking/Memo/OwnerInvoice activity rows themselves. `cutoff` is an exclusive upper bound
    throughout (everything strictly before it is settled; `cutoff` itself and later stays live).

    paid_at/management_fee_paid_at throughout are genuinely `now` (when the reset ran), not a
    fabricated backdate - these are honest "settled as part of the pre-launch reset" markers, not
    real historical payment records, and deliberately never touch Sage or Revolut (no real
    invoice/receipt should go out for a reset that isn't a real charge event).

    Called from finance/management/commands/reset_ledger_before_date.py; kept here (not in the
    command) so the exact same function can be re-run one more time right before go-live, same
    "business logic lives in services.py, the command is a thin caller" convention as
    generate_non_regular_owner_invoice/generate_monthly_owner_invoices.py. Returns a dict of
    counts (what was/would be settled) for the command to report."""
    now = timezone.now()
    counts = {'stale_invoices': 0, 'payout_records': 0, 'monthly_invoices': 0, 'cleans_invoices': 0, 'memos': 0}

    # Step 0: any OwnerInvoice already sitting unpaid (a failed/never-followed-up real dispatch,
    # or an existing CLEANS_INFORMAL_MONTHLY bundle) whose relevant date is before the cutoff -
    # period_start when set, else created_at for the null-period_start "rolling bundle" kinds
    # (COMMISSION_PAYOUT/CLEANS_INFORMAL_MONTHLY - see OwnerInvoice.Kind). COMMISSION_PAYOUT rows
    # are always already paid at creation, so this never actually touches them in practice.
    stale_invoices = OwnerInvoice.objects.exclude(status='paid').filter(
        Q(period_start__lt=cutoff) | Q(period_start__isnull=True, created_at__date__lt=cutoff)
    )
    counts['stale_invoices'] = stale_invoices.count()
    if not dry_run:
        stale_invoices.update(status='paid', paid_at=now)

    # Step 1: regular owners' individual booking payouts (PayoutRecord) - the same "Mark as paid"
    # a staff member would click per-booking on the Payouts tab, batched. auto_now_add stamps
    # paid_at as the moment of this bulk_create, which IS `now` - no separate update needed.
    payment_settings = PaymentSettings.load()
    candidate_bookings = exclude_block_bookings(Booking.objects.filter(
        property__owner__is_paid_regularly=True,
        is_owner=False, enquiry_status__in=env_settings.VALID_BOOKING_STATUSES,
        payout_record__isnull=True,
    )).select_related('property__owner', 'charges', 'platform_payout').prefetch_related('owner_payments')
    to_create = [
        PayoutRecord(booking=booking, amount=payout['owner_balance'])
        for booking, payout in (
            (booking, compute_regular_owner_payout(booking, payment_settings)) for booking in candidate_bookings
        )
        if payout['available'] and payout['due_date'] < cutoff
    ]
    counts['payout_records'] = len(to_create)
    if not dry_run and to_create:
        PayoutRecord.objects.bulk_create(to_create)

    # Step 2: non-regular owners' monthly commission/combined invoices - one bookings query and
    # one memos query across the WHOLE range up front (not one per owner per month - this
    # business's history is small enough for _payouts_due_in_range's own single-query-then-Python
    # approach, but a query per owner per month on top of that would still be needless, see
    # feedback_klt_web_prefer_bulk_db_ops), bucketed by (owner, month) in Python, matching
    # generate_non_regular_owner_invoice's own kind selection and commission math exactly - just
    # settled immediately with no Sage dispatch, since these are legacy months, not a live request
    # for payment.
    non_regular_owners = {owner.pk: owner for owner in Owner.objects.filter(is_paid_regularly=False)}
    if non_regular_owners:
        rows = _payouts_due_in_range(Booking.objects.filter(
            property__owner_id__in=non_regular_owners.keys(),
            property__booking_company__finances_managed_internally=True,
        ), date(2000, 1, 1), cutoff - timedelta(days=1))

        commission_by_key, bookings_by_key = {}, {}
        for booking, payout in rows:
            key = (booking.property.owner_id, payout['due_date'].replace(day=1))
            commission_by_key[key] = commission_by_key.get(key, ZERO) + payout['commission']
            bookings_by_key.setdefault(key, []).append(booking)

        invoiced_owner_ids = [pk for pk, owner in non_regular_owners.items() if owner.cleans_are_invoiced]
        cleans_by_key, memos_by_key = {}, {}
        if invoiced_owner_ids:
            memos = Memo.objects.filter(
                property__owner_id__in=invoiced_owner_ids, sent_at__isnull=False, sent_at__date__lt=cutoff,
            ).select_related('property').prefetch_related('ad_hoc_services')
            for memo in memos:
                key = (memo.property.owner_id, memo.sent_at.date().replace(day=1))
                cleans_by_key[key] = cleans_by_key.get(key, ZERO) + memo.total()
                memos_by_key.setdefault(key, []).append(memo)

        already_invoiced = set(OwnerInvoice.objects.filter(
            owner_id__in=non_regular_owners.keys(),
            kind__in=[OwnerInvoice.Kind.COMMISSION_MONTHLY, OwnerInvoice.Kind.COMBINED_MONTHLY],
            period_start__isnull=False,
        ).values_list('owner_id', 'kind', 'period_start'))

        for owner_id, month_start in set(commission_by_key) | set(cleans_by_key):
            owner = non_regular_owners[owner_id]
            kind = OwnerInvoice.Kind.COMBINED_MONTHLY if owner.cleans_are_invoiced else OwnerInvoice.Kind.COMMISSION_MONTHLY
            key = (owner_id, month_start)
            commission_amount = commission_by_key.get(key, ZERO)
            cleans_amount = cleans_by_key.get(key, ZERO) if owner.cleans_are_invoiced else ZERO
            if commission_amount + cleans_amount == 0 or (owner_id, kind, month_start) in already_invoiced:
                continue
            counts['monthly_invoices'] += 1
            if dry_run:
                continue
            invoice = OwnerInvoice.objects.create(
                owner=owner, kind=kind, period_start=month_start,
                commission_amount=commission_amount, cleans_amount=cleans_amount,
                status='paid', paid_at=now,
            )
            invoice.bookings.set(bookings_by_key.get(key, []))
            invoice.memos.set(memos_by_key.get(key, []))

    # Step 3: regular+invoiced owners' (scenario 1) cleans/meet-greet - any sent Memo before the
    # cutoff never yet attached to any invoice gets bundled into one lump, already-paid
    # CLEANS_MONTHLY invoice per owner (period_start=None, same "rolling bundle" convention
    # CLEANS_INFORMAL_MONTHLY already uses). period_start doesn't matter for CLEANS_MONTHLY's own
    # exclusion check - owner_outstanding_balance checks per-Memo invoice linkage, not month
    # membership - so there's no need to split this into separate months the way step 2 must.
    for owner in Owner.objects.filter(is_paid_regularly=True, cleans_are_invoiced=True):
        memos = list(_memos_before_cutoff(
            Memo.objects.filter(property__owner=owner, sent_at__isnull=False, owner_invoices__isnull=True), cutoff,
        ))
        if not memos:
            continue
        counts['cleans_invoices'] += 1
        if dry_run:
            continue
        invoice = OwnerInvoice.objects.create(
            owner=owner, kind=OwnerInvoice.Kind.CLEANS_MONTHLY,
            cleans_amount=sum((memo.total() for memo in memos), ZERO), status='paid', paid_at=now,
        )
        invoice.memos.set(memos)

    # Step 4: everyone tracked informally (scenario 4 + a true management-only owner, see
    # needs_informal_cleans_tracking) - Memo.management_fee_paid_at IS the source of truth here,
    # no invoice involved either way.
    informal_owner_ids = [owner.pk for owner in Owner.objects.all() if needs_informal_cleans_tracking(owner)]
    informal_memos = _memos_before_cutoff(Memo.objects.filter(
        property__owner_id__in=informal_owner_ids, sent_at__isnull=False, management_fee_paid_at__isnull=True,
    ), cutoff)
    counts['memos'] = informal_memos.count()
    if not dry_run:
        informal_memos.update(management_fee_paid_at=now)

    return counts
