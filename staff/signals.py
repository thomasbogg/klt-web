from django.db.models.signals import post_save
from django.dispatch import receiver

from bookings.models import Arrival, Booking, CheckinSettings, Departure, Extra, PaymentSettings
from finance.services import recompute_unsent_memo_fees_for_settings_change, sync_memo_for_turnover_task
from staff.utils import (
    is_block_booking, resync_checkin_times_for_settings_change, sync_checkins_for_booking,
    sync_cleaning_gap_blocks_for_property, sync_cleaning_tasks_for_booking, sync_freshen_tasks_for_property,
)


def _sync_cleaning_tasks_and_related(booking):
    sync_cleaning_tasks_for_booking(booking)
    sync_freshen_tasks_for_property(booking.property)
    sync_memo_for_turnover_task(booking)


@receiver(post_save, sender=Departure)
def _sync_cleaning_tasks_on_departure_save(sender, instance, **kwargs):
    _sync_cleaning_tasks_and_related(instance.booking)


# The only Extra fields sync_cleaning_tasks_for_booking() (staff/utils.py) actually reads off
# Extra - verified against its own docstring/body, which creates/removes the mid-stay CleaningTask
# off exactly these two and nothing else. late_checkout changes are already resynced directly by
# grant_late_checkout()/revoke_late_checkout() (bookings/views.py::_apply_late_checkout_request,
# which both call sync_cleaning_tasks_for_booking() themselves right after changing a grant), and
# welcome_pack/cot/high_chair/request-type fields don't touch cleaning scheduling, Freshen, or the
# turnover Memo at all.
EXTRA_CLEANING_RELEVANT_FIELDS = frozenset({'mid_stay_clean', 'mid_stay_clean_date'})


@receiver(post_save, sender=Extra)
def _sync_cleaning_tasks_on_extra_save(sender, instance, **kwargs):
    # Skipped for a save that names its update_fields and touches neither field above
    # (2026-09-16, after Thomas reported a slow Extras save). The Extras page saves one combined
    # Extra row per apartment on every submit - Welcome Pack, Cot & High Chair, Late Checkout, and
    # Mid-stay Clean together - so a guest just ticking a Welcome Pack option was still paying for
    # a property-wide Freshen sweep and a Memo round trip neither of those touch, on top of the
    # real remote-Postgres latency each already costs on its own (same reasoning as the Booking
    # receiver below, after the same report against Guest List).
    #
    # update_fields is None for a plain .save(), which still runs everything - the conservative
    # default. This narrows only saves that have already declared exactly what changed.
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and not (set(update_fields) & EXTRA_CLEANING_RELEVANT_FIELDS):
        return
    _sync_cleaning_tasks_and_related(instance.booking)


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
