from django.db.models.signals import post_save
from django.dispatch import receiver

from bookings.models import Arrival, Booking, CheckinSettings, Departure, Extra, PaymentSettings
from finance.services import recompute_unsent_memo_fees_for_settings_change, sync_memo_for_turnover_task
from staff.utils import (
    resync_checkin_times_for_settings_change, sync_checkins_for_booking, sync_cleaning_tasks_for_booking,
    sync_freshen_tasks_for_property,
)


@receiver(post_save, sender=Departure)
@receiver(post_save, sender=Extra)
def _sync_cleaning_tasks_on_related_save(sender, instance, **kwargs):
    sync_cleaning_tasks_for_booking(instance.booking)
    sync_freshen_tasks_for_property(instance.booking.property)
    sync_memo_for_turnover_task(instance.booking)


@receiver(post_save, sender=Booking)
def _sync_cleaning_tasks_on_booking_save(sender, instance, **kwargs):
    # Catches departure_date changing independent of Departure.clean itself, e.g. via
    # bookings/admin.py's BookingDateAdjustmentInline. Also the only place that picks up
    # enquiry_status changes (booking cancel/uncancel), which is what drives Freshen's cascade -
    # see sync_freshen_tasks_for_property()'s docstring (staff/utils.py). Also covers a booking's
    # arrival_date changing independent of Arrival itself, and enquiry_status changes for the
    # check-ins cascade (staff/utils.py::sync_checkins_for_booking's own cancellation branch).
    sync_cleaning_tasks_for_booking(instance)
    sync_freshen_tasks_for_property(instance.property)
    sync_checkins_for_booking(instance)
    sync_memo_for_turnover_task(instance)


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
