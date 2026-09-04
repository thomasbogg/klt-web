from django.core.management.base import BaseCommand
from django.utils import timezone

from communications.models import ScheduledEmail
from communications.services.scheduling import sync_event_triggered_emails
from communications.services.sending import send_scheduled_email


class Command(BaseCommand):
    """Sends every ScheduledEmail whose date has arrived. Not scheduled in-app (klt-web has no
    deployed scheduler yet) - run manually or via an external cron for now, Railway Cron once
    deployed, same convention as bookings/management/commands/sync_ical_feeds.py.

    Runs sync_event_triggered_emails() first - the reconciliation half of the deposit/balance
    payment-received confirmations' belt-and-braces design (see that function's own docstring):
    it catches any booking whose Payment/BalancePayment cleared without a Django ORM save ever
    firing communications/signals.py's post_save handler (in particular, klt-hooks' raw-SQL
    webhook writes, which bypass the ORM entirely and so can never trigger a signal) - a real gap
    this run closes, not a redundant safety net.

    Eligibility is rechecked live inside send_scheduled_email() itself for every row this command
    touches - never duplicated here - so this command and a staff "Send now" click can never
    disagree about whether a row is still actually due."""
    help = "Send every ScheduledEmail whose scheduled_for date has arrived."

    def add_arguments(self, parser):
        parser.add_argument(
            '--booking', default=None,
            help="Only process ScheduledEmail rows for this booking reference (default: all due rows).",
        )

    def handle(self, *args, **options):
        sync_event_triggered_emails()

        today = timezone.now().date()
        due = ScheduledEmail.objects.filter(
            status=ScheduledEmail.Status.PENDING, scheduled_for__lte=today,
        ).select_related('booking', 'template')
        if options['booking']:
            due = due.filter(booking__reference__iexact=options['booking'])

        counts = {'sent': 0, 'skipped': 0, 'failed': 0}
        for scheduled_email in due:
            label = f"{scheduled_email.template.slug} for {scheduled_email.booking.reference}"
            try:
                send_scheduled_email(scheduled_email, actor=None)
            except Exception as error:
                self.stderr.write(self.style.ERROR(f"{label}: {error}"))
                continue
            counts[scheduled_email.status] = counts.get(scheduled_email.status, 0) + 1
            if scheduled_email.status == ScheduledEmail.Status.FAILED:
                self.stderr.write(self.style.ERROR(f"{label}: {scheduled_email.error_message}"))

        self.stdout.write(self.style.SUCCESS(
            f"{counts['sent']} sent, {counts['skipped']} skipped, {counts['failed']} failed."
        ))
