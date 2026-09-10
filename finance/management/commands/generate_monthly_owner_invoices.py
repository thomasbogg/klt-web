from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from finance.services import generate_non_regular_owner_invoice, generate_scenario_1_cleans_invoice
from properties.models import Owner
from staff.utils import last_day_of_month


def _previous_month_start(today):
    first_of_this_month = today.replace(day=1)
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)
    return last_day_of_prev_month.replace(day=1)


_STATUS_MESSAGES = {
    'created': "created €{total} invoice (id={pk}) - {sage_status}",
    'already_invoiced': "already invoiced, skipping (re-run is safe).",
    'nothing_to_bill': "nothing to bill, skipping.",
    'not_applicable': "not applicable for this owner, skipping.",
    'dry_run': "[dry run] would bill this owner/period.",
}


class Command(BaseCommand):
    """Generates this month's (or a given month's) owner Sage invoices for scenarios 1, 2 and 3 of
    the 4-scenario billing matrix (see finance.models.OwnerInvoice's own docstring for the full
    matrix). Scenario 4 is deliberately skipped entirely - its commission is already invoiced
    per-payout (finance/services.py::dispatch_commission_receipt_for_payout) and its cleans/
    meet-greet fee is never formally invoiced at all (see finance.models.Memo.management_fee_paid_at).

    The actual billing math (finance/services.py::generate_non_regular_owner_invoice for scenarios
    2/3, generate_scenario_1_cleans_invoice for scenario 1) is shared with
    staff/views.py::StaffFinanceOwnerPayoutGenerateView - the interactive Payouts-tab 'Generate'
    button added 2026-09-10 for scenarios 2/3, which this command remains the only way to trigger
    for scenario 1, and the only way to trigger any of them without opening a browser (e.g. via an
    external cron, once klt-web is deployed).

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
        period_end = last_day_of_month(period_start)
        dry_run = options['dry_run']

        non_regular_owners = Owner.objects.filter(is_paid_regularly=False)
        if options['owner_id'] is not None:
            non_regular_owners = non_regular_owners.filter(pk=options['owner_id'])
        for owner in non_regular_owners:
            invoice, status = generate_non_regular_owner_invoice(owner, period_start, period_end, dry_run=dry_run)
            self._report(owner, invoice, status, period_start)

        scenario_1_owners = Owner.objects.filter(is_paid_regularly=True, cleans_are_invoiced=True)
        if options['owner_id'] is not None:
            scenario_1_owners = scenario_1_owners.filter(pk=options['owner_id'])
        for owner in scenario_1_owners:
            invoice, status = generate_scenario_1_cleans_invoice(owner, period_start, period_end, dry_run=dry_run)
            self._report(owner, invoice, status, period_start)

    def _report(self, owner, invoice, status, period_start):
        prefix = f"{owner} for {period_start:%B %Y}: "
        if status == 'created':
            sage_status = "dispatched" if not invoice.sage_invoice_error else f"Sage error: {invoice.sage_invoice_error}"
            self.stdout.write(self.style.SUCCESS(
                prefix + _STATUS_MESSAGES['created'].format(total=invoice.total(), pk=invoice.pk, sage_status=sage_status)
            ))
        elif status == 'dry_run':
            self.stdout.write(self.style.WARNING(prefix + _STATUS_MESSAGES['dry_run']))
        elif status == 'not_applicable':
            pass
        else:
            self.stdout.write(prefix + _STATUS_MESSAGES[status])
