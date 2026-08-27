from django.db.models.signals import post_save
from django.dispatch import receiver

from bookings.models import Arrival, Booking, CheckinSettings, Departure, Extra
from staff.utils import (
    resync_checkin_times_for_settings_change, sync_checkins_for_booking, sync_cleaning_tasks_for_booking,
    sync_freshen_tasks_for_property,
)


@receiver(post_save, sender=Departure)
@receiver(post_save, sender=Extra)
def _sync_cleaning_tasks_on_related_save(sender, instance, **kwargs):
    sync_cleaning_tasks_for_booking(instance.booking)
    sync_freshen_tasks_for_property(instance.booking.property)


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


@receiver(post_save, sender=Arrival)
def _sync_checkins_on_arrival_save(sender, instance, **kwargs):
    sync_checkins_for_booking(instance.booking)


@receiver(post_save, sender=CheckinSettings)
def _resync_checkin_times_on_settings_save(sender, instance, **kwargs):
    resync_checkin_times_for_settings_change()
