from django.contrib import admin
from django.urls import reverse
from django.shortcuts import redirect

from .models import Booking, BookingCondition, BookingSettings, Charge


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


class ChargeInline(admin.StackedInline):
    model = Charge
    max_num = 1


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    inlines = [ChargeInline]
    list_display = ('reference', 'property', 'guest', 'arrival_date', 'departure_date', 'enquiry_status', 'enquiry_source')
    list_filter = ('enquiry_status', 'enquiry_source')
    search_fields = ('reference', 'guest__first_name', 'guest__last_name', 'guest__email')


@admin.register(BookingCondition)
class BookingConditionAdmin(admin.ModelAdmin):
    list_display = ('order', 'text')
    list_display_links = ('text',)
    list_editable = ('order',)
    ordering = ('order',)
