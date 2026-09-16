from django.db import models

from properties.models import Property

# Create your models here.


class NotifyOnSaleRequest(models.Model):
    """A guest's non-binding "let me know" for a property/date/guest combo that's not currently
    on sale (see properties/utils.py::property_is_on_sale) - either no Price rows exist yet for
    the stay, or the arrival date is beyond BookingSettings.max_advance_booking_months. This is
    deliberately NOT a Booking (same reasoning as bookings/models.py::PropertyBlock's own docstring
    for why a distinct non-Booking concept exists): it holds no dates, blocks no other guest's
    availability, and creates no Guest row - just contact details captured on ReserveView's
    "Contact Me" step (properties/views.py) when property_is_on_sale is False but the dates
    themselves are still free.

    Resolved by the check_notify_on_sale_requests management command (also reachable from the
    staff app's "Not on sale yet" list, staff/views.py::StaffNotifyOnSaleListView) - run manually/
    periodically for now, since klt-web has no deployed scheduler (see project memory on the
    automation roadmap). NOTIFIED means the "it's on sale now" email went out; UNAVAILABLE means
    someone else booked these exact dates before that ever happened, so the request can never be
    fulfilled as asked - both are terminal, distinct end states rather than one generic "done"."""
    STATUS_PENDING = 'pending'
    STATUS_NOTIFIED = 'notified'
    STATUS_UNAVAILABLE = 'unavailable'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_NOTIFIED, 'Notified'),
        (STATUS_UNAVAILABLE, 'No longer available'),
    )

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name='notify_on_sale_requests')
    start_date = models.DateField()
    end_date = models.DateField()
    adults = models.PositiveIntegerField(default=2)
    children = models.PositiveIntegerField(default=0)
    infants = models.PositiveIntegerField(default=0)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.property} {self.start_date}–{self.end_date} ({self.email})"
