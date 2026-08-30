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
    ('rental_to_owner', 'Rental to Owner'),
    ('clean_cost', 'Clean'),
    ('meet_greet_cost', 'Meet & Greet'),
    ('maintenance_cost', 'Maintenance'),
    ('net_revenue', 'Net Revenue'),
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
        clean_cost = clean_fee(payment_settings, booking)
        meet_greet_cost = meet_greet_fee(payment_settings, booking)

        if payout['available']:
            basic_rental = payout['rental_base']
            platform_fee = payout['platform_fee']
            platform_fee_vat = payout['platform_fee_vat']
            rental_to_owner = basic_rental - payout['commission']
            net_revenue = rental_to_owner - clean_cost - meet_greet_cost - maintenance_cost
        else:
            basic_rental = platform_fee = platform_fee_vat = rental_to_owner = net_revenue = None

        rows.append({
            'booking': booking,
            'nights': (booking.departure_date - booking.arrival_date).days,
            'rental_to_owner': rental_to_owner,
            'basic_rental': basic_rental,
            'platform_fee': platform_fee,
            'platform_fee_vat': platform_fee_vat,
            'clean_cost': clean_cost,
            'meet_greet_cost': meet_greet_cost,
            'maintenance_cost': maintenance_cost,
            'net_revenue': net_revenue,
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
