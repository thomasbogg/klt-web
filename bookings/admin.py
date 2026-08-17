from django.contrib import admin
from django.urls import reverse
from django.shortcuts import redirect

from .models import BookingSettings


@admin.register(BookingSettings)
class BookingSettingsAdmin(admin.ModelAdmin):
    """Singleton admin: skips the changelist and goes straight to the one row's edit form."""

    def has_add_permission(self, request):
        return not BookingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings = BookingSettings.load()
        return redirect(reverse('admin:bookings_bookingsettings_change', args=[settings.pk]))
