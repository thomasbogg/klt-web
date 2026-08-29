from datetime import timedelta

from django.utils import timezone

from bookings.models import Booking, PaymentSettings
from bookings.payouts import _round, clean_fee, compute_owner_payout, meet_greet_fee
from finance.models import AdHocService, Memo
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


def _payouts_due_in_range(bookings_queryset, start, end):
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

    Returns a list of (booking, payout_dict) tuples, payout_dict always 'available' (unavailable
    bookings are silently excluded - nothing to show or pay out)."""
    payment_settings = PaymentSettings.load()
    candidates = bookings_queryset.filter(
        is_owner=False, arrival_date__range=(start - timedelta(days=45), end),
    ).select_related(
        'property__owner', 'property__booking_company', 'property__cleaning_company', 'property__specs',
        'charges', 'platform_payout', 'departure', 'arrival',
    ).prefetch_related('owner_payments')

    results = []
    for booking in candidates:
        payout = compute_owner_payout(booking, payment_settings)
        if payout['available'] and start <= payout['due_date'] <= end:
            results.append((booking, payout))
    return results


def payouts_due_in_range(start, end):
    """Bookings whose computed owner payout is due within [start, end], on a property whose
    booking_company has finances_managed_internally=True and whose owner is paid regularly - the
    Payouts tab's own query (StaffFinancePayoutsView)."""
    return _payouts_due_in_range(Booking.objects.filter(
        property__owner__is_paid_regularly=True,
        property__booking_company__finances_managed_internally=True,
    ), start, end)


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
