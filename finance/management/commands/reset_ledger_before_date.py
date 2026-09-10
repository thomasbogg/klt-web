from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from finance.services import reset_ledger_before_date


class Command(BaseCommand):
    """Thin wrapper around finance/services.py::reset_ledger_before_date - see that function's
    own docstring for exactly what gets settled and why. Re-runnable: a second run is a safe
    no-op for anything already settled by the first, and this is deliberately meant to be run
    again right before go-live with the real launch date."""
    help = "Marks every owner payment-tracking mechanism as settled for anything dated before --cutoff."

    def add_arguments(self, parser):
        parser.add_argument('--cutoff', required=True, help="YYYY-MM-DD, exclusive upper bound.")
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Report counts without writing anything.",
        )

    def handle(self, *args, **options):
        cutoff = parse_date(options['cutoff'])
        if cutoff is None:
            raise CommandError(f"--cutoff must be YYYY-MM-DD, got {options['cutoff']!r}")

        counts = reset_ledger_before_date(cutoff, dry_run=options['dry_run'])

        label = "Would settle" if options['dry_run'] else "Settled"
        self.stdout.write(self.style.SUCCESS(
            f"{label} everything before {cutoff}:\n"
            f"  {counts['stale_invoices']} existing unpaid/stuck owner invoice(s)\n"
            f"  {counts['payout_records']} regular-owner booking payout(s)\n"
            f"  {counts['monthly_invoices']} non-regular owner monthly invoice(s) (backfilled)\n"
            f"  {counts['cleans_invoices']} scenario-1 owner cleans bundle(s) (backfilled)\n"
            f"  {counts['memos']} informally-tracked cleans/meet-greet Memo(s)"
        ))
