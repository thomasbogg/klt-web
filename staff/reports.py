from decimal import Decimal

from bookings.models import Booking, PaymentSettings
from bookings.payouts import clean_fee, compute_owner_payout, meet_greet_fee
from env_settings import VALID_BOOKING_STATUSES
from finance.models import Memo

ZERO = Decimal('0')

REPORT_COLUMNS = (
    ('platform_fee', 'Platform fee'),
    ('platform_fee_vat', 'Platform fee IVA'),
    ('basic_rental', 'Basic rental'),
    ('commission', 'Commission'),
    ('klt_net_commission', 'KLT Net Commission'),
    ('rental_to_owner', 'Rental to Owner'),
    ('clean_cost', 'Clean'),
    ('meet_greet_cost', 'Meet & Greet'),
    ('maintenance_cost', 'Maintenance'),
    ('net_revenue', 'Owner Net Revenue'),
    ('klt_net_revenue', 'KLT Net Revenue'),
)

# commission/klt_net_commission/klt_net_revenue are KLT's own internal figures (the agency's
# Pre-IVA commission, what it actually nets after remitting VAT on that commission, and its own
# total take from this booking) - staff-only, per the same "admin fee is the one figure Thomas
# doesn't want owners to see" principle already applied elsewhere (see owners/views.py::
# OwnerReportView, which scopes its own COLUMNS to this set rather than reusing REPORT_COLUMNS
# directly, now that the two aren't identical any more).
OWNER_SAFE_REPORT_COLUMNS = tuple(
    (key, label) for key, label in REPORT_COLUMNS
    if key not in ('commission', 'klt_net_commission', 'klt_net_revenue')
)


def booking_report_rows(start, end, properties=None):
    """One row per booking arriving within [start, end] (inclusive), each carrying every figure
    on REPORT_COLUMNS alongside fixed identifying columns - mirrors the reference "owner bookings
    report" Thomas provided (see [[project_klt_web_reporting]] in memory). The shared row-builder
    behind both the staff Reports page and the owner-facing one (owners/views.py::
    OwnerReportView), so the two never compute figures two different ways; only the `properties`
    scoping and which columns get rendered differ between them. VALID_BOOKING_STATUSES-only
    (Provisional/Confirmed/Holiday started) - a report is for real business, not stale/cancelled
    enquiries.

    This is a waterfall, each figure building on the last (per Thomas's own framing, 2026-08-30):
    Basic rental (what the guest paid, or what the platform's own payout was - excludes admin fee
    entirely, and excludes our commission) -> Rental to Owner (basic_rental minus our internal
    agency commission - NOT yet minus Clean/Meet & Greet/Maintenance) -> Net Revenue (Rental to
    Owner minus Clean, Meet & Greet and Maintenance). rental_to_owner/basic_rental/platform_fee/
    platform_fee_vat are read off compute_owner_payout()'s own dict (rental_base/commission/
    platform_fee/platform_fee_vat) - None together whenever that dict is 'unavailable' (owner
    stay, no owner assigned, no Charge/PlatformPayout yet), and so is net_revenue, since it's
    derived from rental_to_owner. clean_fee()/meet_greet_fee() are called directly instead (not
    read off that dict) so an owner-stay booking still shows its real cleaning costs, per those
    two functions' own docstrings - a clean happens (and costs real money) independently of
    whether there's a payout to report.

    Deliberately NOT the same "owner_balance" figure Payouts/Statement show elsewhere in Finance -
    this waterfall stops at commission and the three cost lines; it doesn't further deduct
    platform_fee_vat or any manual staff.models.OwnerPayment adjustment the way owner_balance
    does. Two different "bottom line" numbers exist in the app on purpose, for two different
    audiences/purposes - flagged here so a future reader doesn't try to reconcile them.

    maintenance_cost is that booking's own turnover Memo's ad-hoc service total (finance.models.
    Memo.ad_hoc_total()) - zero, not unavailable, when there's no Memo (cleaning_company isn't
    finances_managed_internally, or the clean hasn't synced a Memo yet), matching clean_cost's own
    "no memo needed to know the real number" behaviour.

    `properties`, if given, is an iterable of Property (or pk) to restrict to - plural because the
    owner-facing caller usually has several properties, not the staff caller's single-dropdown
    choice."""
    payment_settings = PaymentSettings.load()
    bookings_qs = Booking.objects.filter(
        enquiry_status__in=VALID_BOOKING_STATUSES, arrival_date__range=(start, end),
    ).select_related(
        'property__owner', 'property__specs', 'guest', 'charges', 'platform_payout', 'departure', 'arrival',
    ).prefetch_related('owner_payments').order_by('arrival_date', 'property__title')
    if properties is not None:
        bookings_qs = bookings_qs.filter(property__in=properties)
    bookings = list(bookings_qs)

    memos_by_booking_id = {
        memo.cleaning_task.booking_id: memo
        for memo in Memo.objects.filter(cleaning_task__booking_id__in=[b.pk for b in bookings])
        .select_related('cleaning_task').prefetch_related('ad_hoc_services')
    }

    rows = []
    for booking in bookings:
        payout = compute_owner_payout(booking, payment_settings)
        memo = memos_by_booking_id.get(booking.pk)
        maintenance_cost = memo.ad_hoc_total() if memo else ZERO
        # A sent Memo is frozen (see Memo's own docstring) - its clean_fee/meet_greet_fee are
        # what the owner was actually billed, so this row must show those, not whatever
        # PaymentSettings/standard_cleaning_fee say today. An unsent (or absent) Memo still
        # live-recomputes, same as maintenance_cost above.
        if memo is not None and memo.sent_at is not None:
            clean_cost = memo.clean_fee
            meet_greet_cost = memo.meet_greet_fee
        else:
            clean_cost = clean_fee(payment_settings, booking)
            meet_greet_cost = meet_greet_fee(payment_settings, booking)

        if payout['available']:
            basic_rental = payout['rental_base']
            platform_fee = payout['platform_fee']
            platform_fee_vat = payout['platform_fee_vat']
            commission = payout['commission']
            # "Post-IVA" - what KLT actually nets after remitting VAT on its own commission
            # income, per Thomas 2026-08-30 (mirrors the reference workbook's Commissions sheet,
            # Maria's own column dropped as a legacy item documented elsewhere). Not part of the
            # owner-facing waterfall below - owner_balance/rental_to_owner only ever deduct the
            # full Pre-IVA commission (see compute_owner_payout's own docstring on commission_vat
            # being "agency-absorbed, not deducted") - this is a separate, KLT-internal figure.
            klt_net_commission = commission - payout['commission_vat']
            rental_to_owner = basic_rental - commission
            net_revenue = rental_to_owner - clean_cost - meet_greet_cost - maintenance_cost
        else:
            # No rental figure to build a waterfall from (owner stay, no owner assigned, or no
            # Charge/PlatformPayout yet) - but clean/meet & greet/maintenance costs are real
            # regardless (see this function's own docstring), so Owner Net Revenue still comes out
            # as a genuine negative number here rather than "unavailable" - a row with only
            # deductions and no income is exactly what a negative Owner Net Revenue is for.
            basic_rental = platform_fee = platform_fee_vat = commission = klt_net_commission = rental_to_owner = None
            net_revenue = -(clean_cost + meet_greet_cost + maintenance_cost)

        # KLT Net Revenue (2026-08-30, per Thomas) - KLT's OWN total take from this booking, a
        # completely separate bottom line from Owner Net Revenue above: its net (Post-IVA)
        # commission plus the Clean/Meet & Greet/Maintenance fees it charges/manages - unlike
        # Owner Net Revenue, those three are real earnings TO KLT here, not deductions FROM
        # anyone, so this is always a genuine figure (never None) even when there's no payout to
        # report a commission from - klt_net_commission itself still shows "-" in that case (see
        # its own column), but 0 is the correct contribution to this sum, not "unavailable".
        klt_net_revenue = (klt_net_commission or ZERO) + clean_cost + meet_greet_cost + maintenance_cost

        rows.append({
            'booking': booking,
            'nights': (booking.departure_date - booking.arrival_date).days,
            'rental_to_owner': rental_to_owner,
            'basic_rental': basic_rental,
            'platform_fee': platform_fee,
            'platform_fee_vat': platform_fee_vat,
            'commission': commission,
            'klt_net_commission': klt_net_commission,
            'clean_cost': clean_cost,
            'meet_greet_cost': meet_greet_cost,
            'maintenance_cost': maintenance_cost,
            'net_revenue': net_revenue,
            'klt_net_revenue': klt_net_revenue,
        })
    return rows


def report_totals(rows):
    """Sums each numeric column across `rows`, treating a None (figure unavailable for that
    booking) as excluded rather than zero - so the total reflects only bookings that actually had
    a figure, not a total silently deflated by owner stays/incomplete data."""
    totals = {}
    for key, _label in REPORT_COLUMNS:
        values = [row[key] for row in rows if row[key] is not None]
        totals[key] = sum(values, ZERO) if values else None
    return totals
