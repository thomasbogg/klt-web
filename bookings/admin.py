from django.contrib import admin
from django.urls import reverse
from django.shortcuts import redirect

from .models import (
    Booking, BookingCondition, BookingGuest, BookingRequestedExtra, BookingSettings, Charge, Extra,
    Payment, PlatformPayout, RequestType, WelcomePackItem,
)
from .utils import expire_stale_holds


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


class PaymentInline(admin.StackedInline):
    """Lets staff manually flip status/enquiry_status once a Wise transfer is seen to land, since
    there's no automated Wise reconciliation (see bookings/utils.py::determine_payment_provider)."""
    model = Payment
    max_num = 1


class BookingGuestInline(admin.TabularInline):
    model = BookingGuest


class PlatformPayoutInline(admin.StackedInline):
    """Only relevant for platform-sourced bookings (see env_settings.PLATFORMS) - gross/commission/
    payout, distinct from Charge/Payment which are the online direct-booking flow. See
    bookings/models.py::PlatformPayout."""
    model = PlatformPayout
    max_num = 1


class ExtraInline(admin.StackedInline):
    model = Extra
    max_num = 1


class BookingRequestedExtraInline(admin.TabularInline):
    model = BookingRequestedExtra
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    inlines = [ChargeInline, PaymentInline, BookingGuestInline, PlatformPayoutInline, ExtraInline,
               BookingRequestedExtraInline]
    list_display = ('reference', 'property', 'guest', 'arrival_date', 'departure_date', 'enquiry_status', 'enquiry_source')
    list_filter = ('enquiry_status', 'enquiry_source')
    search_fields = ('reference', 'guest__first_name', 'guest__last_name', 'guest__email')

    def get_queryset(self, request):
        # Cheap bulk update, run on every changelist load - see bookings/utils.py::expire_stale_holds()
        # for why this can't clobber a genuinely in-progress payment.
        expire_stale_holds()
        return super().get_queryset(request)


@admin.register(BookingCondition)
class BookingConditionAdmin(admin.ModelAdmin):
    list_display = ('order', 'text')
    list_display_links = ('text',)
    list_editable = ('order',)
    ordering = ('order',)


@admin.register(RequestType)
class RequestTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_price', 'active')
    list_editable = ('default_price', 'active')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(WelcomePackItem)
class WelcomePackItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'name', 'active')
    list_display_links = ('name',)
    list_editable = ('order', 'active')
    ordering = ('order',)
