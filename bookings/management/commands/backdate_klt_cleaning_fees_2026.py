from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from bookings.models import Booking, PaymentSettings
from bookings.payouts import clean_fee, meet_greet_fee
from finance.models import Memo
from properties.models import ManagementCompany, Property
from staff.models import CleaningTask

HISTORICAL_MEET_GREET_FEE = Decimal('25.00')
HISTORICAL_HIGH_OCCUPANCY_SURCHARGE = Decimal('10.00')
ONE_BEDROOM_INCREASE = Decimal('10.00')
MULTI_BEDROOM_INCREASE = Decimal('15.00')

HISTORICAL_START = date(2022, 1, 1)
HISTORICAL_END = date(2025, 12, 31)
NEW_REGIME_START = date(2026, 1, 1)
NEW_REGIME_END = date(2026, 8, 31)


class Command(BaseCommand):
    """One-off, per Thomas 2026-08-31 (see memory: project_klt_web_fee_backdate_2026): reconstructs
    real cleaning/meet-greet Memo charges for KLT-cleaned bookings that predate the Memo feature
    entirely, then applies a standing cleaning-fee price increase, then does the equivalent
    backdate for 2026 bookings under the new regime. Three ordered phases - the 2022-2025 figures
    are deliberately computed BEFORE the price increase (they reflect the old rate that was
    actually in effect), the 2026 figures AFTER it (today's live PaymentSettings/
    standard_cleaning_fee IS the new regime, so those just reuse clean_fee()/meet_greet_fee() as
    real bookings already do).

    Every Memo this creates/updates is marked sent immediately (sent_at/sent_by set) - these
    represent cleans that already, genuinely happened, so per Thomas they should appear as real
    historical charges on the owner-facing Payouts & Memos ledger, not sit as unreviewed drafts.
    A Memo that's already sent is never touched (sent_at freezes a Memo permanently - see its own
    docstring) - only the 1 pre-existing Memo in the whole 2022-2025 window and any of the 58
    pre-existing 2026 drafts could possibly hit that path.

    Scope is confirmed 'Booking confirmed' status with a completed turnover clean
    (Departure.clean=True) AND an actual turnover CleaningTask row to attach the Memo to - cancelled/
    failed-enquiry legacy bookings often still carry a stale CleaningTask (the import bypassed the
    live delete-on-cancel signal), so status is checked explicitly rather than inferred from task
    existence alone. A confirmed, cleaned booking with no turnover task at all (65 in the
    2022-2025 window) is skipped - there's nothing to hang a Memo off, and this command doesn't
    invent CleaningTask rows."""
    help = "One-off: backdate KLT cleaning/meet-greet memos for 2022-2025 and 2026, and bump standard_cleaning_fee for the new regime."

    def handle(self, *args, **options):
        klt = ManagementCompany.objects.get(name='KLT Property Services Lda')
        sent_by = User.objects.filter(username='thomasbogg').first()
        now = timezone.now()

        with transaction.atomic():
            hist_stats = self._backdate_period(
                klt, HISTORICAL_START, HISTORICAL_END, now, sent_by, historical=True,
            )
            self.stdout.write(
                f"2022-2025: created {hist_stats['created']}, updated {hist_stats['updated']}, "
                f"skipped (no turnover task) {hist_stats['skipped_no_task']}, "
                f"skipped (already sent) {hist_stats['skipped_sent']}"
            )

            bumped = self._bump_cleaning_fees(klt)
            self.stdout.write(f"standard_cleaning_fee bumped on {bumped} KLT properties")

            new_stats = self._backdate_period(
                klt, NEW_REGIME_START, NEW_REGIME_END, now, sent_by, historical=False,
            )
            self.stdout.write(
                f"2026: created {new_stats['created']}, updated {new_stats['updated']}, "
                f"skipped (no turnover task) {new_stats['skipped_no_task']}, "
                f"skipped (already sent) {new_stats['skipped_sent']}"
            )

    def _backdate_period(self, klt, start, end, now, sent_by, historical):
        bookings = list(
            Booking.objects.filter(
                property__cleaning_company=klt,
                enquiry_status='Booking confirmed',
                arrival_date__range=(start, end),
                departure__clean=True,
            ).select_related('property__specs', 'arrival', 'departure')
        )
        booking_ids = [b.pk for b in bookings]

        tasks_by_booking_id = {
            t.booking_id: t
            for t in CleaningTask.objects.filter(booking_id__in=booking_ids, task_type='turnover')
        }
        existing_memos_by_task_id = {
            m.cleaning_task_id: m
            for m in Memo.objects.filter(cleaning_task_id__in=[t.pk for t in tasks_by_booking_id.values()])
        }

        payment_settings = PaymentSettings.load() if not historical else None

        stats = {'created': 0, 'updated': 0, 'skipped_no_task': 0, 'skipped_sent': 0}
        to_create = []
        to_update = []

        for booking in bookings:
            task = tasks_by_booking_id.get(booking.pk)
            if task is None:
                stats['skipped_no_task'] += 1
                continue

            if historical:
                clean_amount, greet_amount = self._historical_fees(booking)
            else:
                clean_amount = clean_fee(payment_settings, booking)
                greet_amount = meet_greet_fee(payment_settings, booking)

            memo = existing_memos_by_task_id.get(task.pk)
            if memo is None:
                to_create.append(Memo(
                    property=booking.property, cleaning_task=task,
                    clean_fee=clean_amount, meet_greet_fee=greet_amount,
                    sent_at=now, sent_by=sent_by,
                ))
                stats['created'] += 1
            elif memo.sent_at is not None:
                stats['skipped_sent'] += 1
            else:
                memo.clean_fee = clean_amount
                memo.meet_greet_fee = greet_amount
                memo.sent_at = now
                memo.sent_by = sent_by
                to_update.append(memo)
                stats['updated'] += 1

        Memo.objects.bulk_create(to_create, batch_size=500)
        Memo.objects.bulk_update(to_update, ['clean_fee', 'meet_greet_fee', 'sent_at', 'sent_by'], batch_size=500)
        return stats

    def _historical_fees(self, booking):
        specs = getattr(booking.property, 'specs', None)
        clean_amount = booking.property.standard_cleaning_fee
        if specs is not None and specs.bedrooms and booking.total_guests() / specs.bedrooms > 2:
            clean_amount += HISTORICAL_HIGH_OCCUPANCY_SURCHARGE
        arrival = getattr(booking, 'arrival', None)
        greet_amount = HISTORICAL_MEET_GREET_FEE if (arrival is not None and arrival.meet_greet) else Decimal('0')
        return clean_amount, greet_amount

    def _bump_cleaning_fees(self, klt):
        properties = Property.objects.filter(cleaning_company=klt).select_related('specs')
        bumped = 0
        for property in properties:
            specs = getattr(property, 'specs', None)
            bedrooms = specs.bedrooms if specs is not None else None
            if bedrooms == 1:
                increase = ONE_BEDROOM_INCREASE
            elif bedrooms and bedrooms >= 2:
                increase = MULTI_BEDROOM_INCREASE
            else:
                continue
            property.standard_cleaning_fee += increase
            property.save(update_fields=['standard_cleaning_fee'])
            bumped += 1
        return bumped
