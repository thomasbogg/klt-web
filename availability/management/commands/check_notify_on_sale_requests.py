from django.core.management.base import BaseCommand

from availability.utils import run_notify_on_sale_check


class Command(BaseCommand):
    """Resolves every pending NotifyOnSaleRequest (availability/models.py) that can now be
    resolved - see run_notify_on_sale_check's own docstring for the two terminal outcomes. Not
    scheduled in-app (klt-web has no deployed scheduler yet) - run manually or via an external
    cron, or from the staff app's "Not on sale yet" list ("Check now" button), which calls the
    exact same function."""
    help = "Email guests whose NotifyOnSaleRequest is now on sale; resolve any since booked out."

    def handle(self, *args, **options):
        notified_count, unavailable_count = run_notify_on_sale_check()
        self.stdout.write(
            f"Notified {notified_count}, {unavailable_count} no longer available "
            f"(now booked by someone else)."
        )
