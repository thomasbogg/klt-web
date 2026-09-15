from django.db.models.signals import post_save
from django.dispatch import receiver

from bookings.models import Arrival, Booking, CheckinSettings, Departure, Extra, PaymentSettings
from finance.services import recompute_unsent_memo_fees_for_settings_change, sync_memo_for_turnover_task
from staff.utils import (
    is_block_booking, resync_checkin_times_for_settings_change, sync_checkins_for_booking,
    sync_cleaning_gap_blocks_for_property, sync_cleaning_tasks_for_booking, sync_freshen_tasks_for_property,
)


@receiver(post_save, sender=Departure)
@receiver(post_save, sender=Extra)
def _sync_cleaning_tasks_on_related_save(sender, instance, **kwargs):
    sync_cleaning_tasks_for_booking(instance.booking)
    sync_freshen_tasks_for_property(instance.booking.property)
    sync_memo_for_turnover_task(instance.booking)


# The only Booking fields any of the resyncs below actually read (verified 2026-09-15 against
# staff/utils.py and finance/services.py - none of them look at adults/children/babies or the
# party at all). A save that touches none of these cannot change their output.
CLEANING_RELEVANT_BOOKING_FIELDS = frozenset({
    'arrival_date', 'departure_date', 'enquiry_status', 'property', 'property_id',
})


@receiver(post_save, sender=Booking)
def _sync_cleaning_tasks_on_booking_save(sender, instance, **kwargs):
    # Catches departure_date changing independent of Departure.clean itself, e.g. via
    # bookings/admin.py's BookingDateAdjustmentInline. Also the only place that picks up
    # enquiry_status changes (booking cancel/uncancel), which is what drives Freshen's cascade -
    # see sync_freshen_tasks_for_property()'s docstring (staff/utils.py). Also covers a booking's
    # arrival_date changing independent of Arrival itself, and enquiry_status changes for the
    # check-ins cascade (staff/utils.py::sync_checkins_for_booking's own cancellation branch).
    #
    # Skipped entirely for a save that names its update_fields and touches none of the fields these
    # resyncs read (2026-09-15, after Thomas reported a slow Guest List save). Saving a guest list
    # writes only adults/children/babies/last_updated, but was still triggering five property-wide
    # resyncs per apartment - cleaning tasks, freshen, check-ins, the turnover Memo and gap blocks -
    # none of which depend on who is staying, only on when and where. On this project's remote
    # Postgres each of those is real round-trip latency the guest waits through.
    #
    # update_fields is None for a plain .save(), which still runs everything - the conservative
    # default. This narrows only saves that have already declared exactly what they changed.
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and not (set(update_fields) & CLEANING_RELEVANT_BOOKING_FIELDS):
        return

    sync_cleaning_tasks_for_booking(instance)
    sync_freshen_tasks_for_property(instance.property)
    sync_checkins_for_booking(instance)
    sync_memo_for_turnover_task(instance)
    # Guard against unbounded recursion: sync_cleaning_gap_blocks_for_property() creates/resizes
    # 'BLOCK - Unbookable' Booking rows, which are themselves instances of the model this signal
    # watches - see that function's own docstring (staff/utils.py) for why skipping it here for a
    # block booking's own save is load-bearing, not incidental.
    #
    # This one is the expensive member of the set, not the cheap one: it walks EVERY booking ever
    # made for the property (staff/utils.py, the `Booking.objects.filter(property=property)` loop).
    # Leaving it running on party-only saves put a Guest List save back to ~15s on real data.
    if not is_block_booking(instance):
        sync_cleaning_gap_blocks_for_property(instance.property)


@receiver(post_save, sender=Arrival)
def _sync_checkins_on_arrival_save(sender, instance, **kwargs):
    sync_checkins_for_booking(instance.booking)
    # A Memo's meet-greet line depends on Arrival.meet_greet, which CleaningTask itself doesn't
    # care about (no sync_cleaning_tasks_for_booking call in this receiver) - so this call is
    # needed here specifically, or toggling meet & greet on an existing booking would never
    # update an already-created Memo.
    sync_memo_for_turnover_task(instance.booking)


@receiver(post_save, sender=CheckinSettings)
def _resync_checkin_times_on_settings_save(sender, instance, **kwargs):
    resync_checkin_times_for_settings_change()


@receiver(post_save, sender=PaymentSettings)
def _resync_memo_fees_on_payment_settings_save(sender, instance, **kwargs):
    recompute_unsent_memo_fees_for_settings_change()
