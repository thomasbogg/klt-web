from django.db.models.signals import post_save
from django.dispatch import receiver

from bookings.models import Booking, Departure, Extra
from staff.utils import sync_cleaning_tasks_for_booking


@receiver(post_save, sender=Departure)
@receiver(post_save, sender=Extra)
def _sync_cleaning_tasks_on_related_save(sender, instance, **kwargs):
    sync_cleaning_tasks_for_booking(instance.booking)


@receiver(post_save, sender=Booking)
def _sync_cleaning_tasks_on_booking_save(sender, instance, **kwargs):
    # Catches departure_date changing independent of Departure.clean itself, e.g. via
    # bookings/admin.py's BookingDateAdjustmentInline.
    sync_cleaning_tasks_for_booking(instance)
