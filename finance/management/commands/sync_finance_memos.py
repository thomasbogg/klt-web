from django.core.management.base import BaseCommand
from django.utils import timezone

from finance.services import sync_memo_for_turnover_task
from staff.models import CleaningTask


class Command(BaseCommand):
    """Backfills/reconciles Memo rows for existing upcoming turnover CleaningTasks - needed once
    after this feature ships, and again any time finances_managed_internally is toggled on for a
    company after tasks already exist (that toggle doesn't retroactively backfill on its own, same
    convention as cleans_on_calendar/checkins_on_calendar). Bounded to today-or-later turnover
    tasks - no value memo-izing an already-past clean that was never billed contemporaneously."""
    help = "Backfill/reconcile Memo rows for upcoming turnover cleans."

    def handle(self, *args, **options):
        today = timezone.now().date()
        tasks = CleaningTask.objects.filter(task_type='turnover', date__gte=today).select_related('booking')

        count = 0
        for task in tasks:
            sync_memo_for_turnover_task(task.booking)
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Synced Memo rows for {count} turnover cleaning task(s)."))
