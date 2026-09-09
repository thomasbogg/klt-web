import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from finance.models import Memo, OwnerInvoice, SageSettings
from finance.services import (
    create_revolut_order_for_owner_invoice, dispatch_owner_invoice_to_sage, owner_balance_in_range,
)
from properties.models import Owner, Property


def _previous_month_start(today):
    first_of_this_month = today.replace(day=1)
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)
    return last_day_of_prev_month.replace(day=1)


class Command(BaseCommand):
    """Generates this month's (or a given month's) owner Sage invoices for scenarios 1, 2 and 3 of
    the 4-scenario billing matrix (see finance.models.OwnerInvoice's own docstring for the full
    matrix). Scenario 4 is deliberately skipped entirely - its commission is already invoiced
    per-payout (finance/services.py::dispatch_commission_receipt_for_payout) and its cleans/
    meet-greet fee is never formally invoiced at all (see finance.models.Memo.management_fee_paid_at).

    Not scheduled in-app (klt-web has no deployed scheduler yet) - run manually or via an external
    cron, same convention as bookings/management/commands/sync_ical_feeds.py and
    communications/management/commands/send_due_scheduled_emails.py.

    Idempotent via OwnerInvoice's (owner, kind, period_start) unique constraint - a second run for
    an owner/kind/month that's already been billed is a safe no-op, so this can be re-run freely,
    including late with --month for a missed period."""
    help = "Generate/dispatch this month's (or a given month's) owner Sage invoices for scenarios 1, 2 and 3."

    def add_arguments(self, parser):
        parser.add_argument(
            '--month', default=None,
            help="Month to bill, as YYYY-MM (default: the previous calendar month).",
        )
        parser.add_argument('--owner-id', type=int, default=None, help="Only bill this one owner.")
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Print what would be created/dispatched without touching the database, Sage, or Revolut.",
        )

    def handle(self, *args, **options):
        if options['month']:
            try:
                year, month = (int(part) for part in options['month'].split('-'))
                period_start = date(year, month, 1)
            except (ValueError, TypeError):
                raise CommandError("--month must be in YYYY-MM format, e.g. 2026-08")
        else:
            period_start = _previous_month_start(date.today())
        period_end = date(period_start.year, period_start.month, calendar.monthrange(period_start.year, period_start.month)[1])

        owners = Owner.objects.filter(is_paid_regularly=False)
        if options['owner_id'] is not None:
            owners = owners.filter(pk=options['owner_id'])

        sage_settings = SageSettings.load()
        dry_run = options['dry_run']

        for owner in owners:
            if owner.cleans_are_invoiced:
                self._bill_combined(owner, period_start, period_end, sage_settings, dry_run)
            else:
                self._bill_commission_only(owner, period_start, period_end, sage_settings, dry_run)

        self._bill_scenario_1(options, period_start, period_end, sage_settings, dry_run)

    def _bill_scenario_1(self, options, period_start, period_end, sage_settings, dry_run):
        owners = Owner.objects.filter(is_paid_regularly=True, cleans_are_invoiced=True)
        if options['owner_id'] is not None:
            owners = owners.filter(pk=options['owner_id'])
        for owner in owners:
            memos = self._sent_memos(owner, period_start, period_end)
            cleans_amount = sum((memo.total() for memo in memos), Decimal('0'))
            self._create_invoice(
                owner, OwnerInvoice.Kind.CLEANS_MONTHLY, period_start, cleans_amount=cleans_amount,
                memos=memos, sage_settings=sage_settings, dry_run=dry_run, revolut=True,
            )

    def _bill_combined(self, owner, period_start, period_end, sage_settings, dry_run):
        commission_amount, bookings = self._commission_in_range(owner, period_start, period_end)
        memos = self._sent_memos(owner, period_start, period_end)
        cleans_amount = sum((memo.total() for memo in memos), Decimal('0'))
        self._create_invoice(
            owner, OwnerInvoice.Kind.COMBINED_MONTHLY, period_start,
            commission_amount=commission_amount, cleans_amount=cleans_amount,
            bookings=bookings, memos=memos, sage_settings=sage_settings, dry_run=dry_run, revolut=False,
        )

    def _bill_commission_only(self, owner, period_start, period_end, sage_settings, dry_run):
        commission_amount, bookings = self._commission_in_range(owner, period_start, period_end)
        self._create_invoice(
            owner, OwnerInvoice.Kind.COMMISSION_MONTHLY, period_start,
            commission_amount=commission_amount, bookings=bookings,
            sage_settings=sage_settings, dry_run=dry_run, revolut=False,
        )

    def _commission_in_range(self, owner, period_start, period_end):
        """Sums payout['commission'] across every booking due in this period, on every property of
        this owner whose booking_company has finances_managed_internally=True - same gating
        staff/views.py::StaffFinanceStatementView already applies before calling
        owner_balance_in_range for the same reason."""
        total = Decimal('0')
        bookings = []
        properties = Property.objects.filter(owner=owner, booking_company__finances_managed_internally=True)
        for property in properties:
            for booking, payout in owner_balance_in_range(property, period_start, period_end):
                total += payout['commission']
                bookings.append(booking)
        return total, bookings

    def _sent_memos(self, owner, period_start, period_end):
        return list(Memo.objects.filter(
            property__owner=owner, sent_at__date__range=(period_start, period_end),
        ).prefetch_related('ad_hoc_services'))

    def _create_invoice(
        self, owner, kind, period_start, sage_settings, dry_run, revolut,
        commission_amount=Decimal('0'), cleans_amount=Decimal('0'), bookings=(), memos=(),
    ):
        total = commission_amount + cleans_amount
        if total == 0:
            self.stdout.write(f"{owner} {kind.label} for {period_start:%B %Y}: nothing to bill, skipping.")
            return

        if OwnerInvoice.objects.filter(owner=owner, kind=kind, period_start=period_start).exists():
            self.stdout.write(
                f"{owner} {kind.label} for {period_start:%B %Y}: already invoiced, skipping (re-run is safe)."
            )
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[dry run] {owner} {kind.label} for {period_start:%B %Y}: would bill €{total} "
                f"(commission €{commission_amount}, cleans €{cleans_amount})"
            ))
            return

        invoice = OwnerInvoice.objects.create(
            owner=owner, kind=kind, period_start=period_start,
            commission_amount=commission_amount, cleans_amount=cleans_amount,
        )
        if bookings:
            invoice.bookings.set(bookings)
        if memos:
            invoice.memos.set(memos)

        tax_rate_id = sage_settings.commission_tax_rate_id if kind == OwnerInvoice.Kind.COMMISSION_MONTHLY else sage_settings.default_tax_rate_id
        dispatch_owner_invoice_to_sage(
            invoice, tax_rate_id, description=f'{owner} - {kind.label} - {period_start:%B %Y}',
        )
        if revolut:
            create_revolut_order_for_owner_invoice(invoice)

        status = "dispatched" if not invoice.sage_invoice_error else f"Sage error: {invoice.sage_invoice_error}"
        self.stdout.write(self.style.SUCCESS(
            f"{owner} {kind.label} for {period_start:%B %Y}: created €{total} invoice (id={invoice.pk}) - {status}"
        ))
