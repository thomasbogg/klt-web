from datetime import timedelta

from django.contrib import admin
from django.urls import reverse
from django.shortcuts import redirect
from django.utils import timezone

from env_settings import VALID_BOOKING_STATUSES
from .models import (
    AirportTransfer, AirportTransferPriceBand, Arrival, BalancePayment, Booking, BookingCondition,
    BookingDateAdjustment, BookingGuest, BookingRequestedExtra, BookingSettings, Charge, Departure,
    Extra, ExtrasSettings, GuestListAdjustment, Payment, PlatformPayout, RequestType, WelcomePackItem,
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


class BalancePaymentInline(admin.StackedInline):
    """Only present on two-stage bookings - see BalancePayment's docstring. Same manual-confirm
    role as PaymentInline: klt-hooks' webhook handling for this is built but deliberately left
    switched off (a cross-talk issue with the legacy tourist-tax webhook on the same Revolut
    account), so staff flip status here by hand for both Wise and Revolut until that's turned on."""
    model = BalancePayment
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


class AirportTransferInline(admin.TabularInline):
    model = AirportTransfer
    extra = 0


class ArrivalInline(admin.StackedInline):
    model = Arrival
    max_num = 1


class DepartureInline(admin.StackedInline):
    """clean/manual_date are staff/ops-only - the guest-facing form never sets them beyond their
    default, so this is where staff actually review/set them."""
    model = Departure
    max_num = 1


class BookingDateAdjustmentInline(admin.TabularInline):
    """Staff only ever fill in new_arrival_date/new_departure_date/additional_charge/notes - the
    previous_* dates are auto-snapshotted by BookingDateAdjustment.save() and shown read-only here
    so staff can review history without being able to hand-edit what "before" was."""
    model = BookingDateAdjustment
    extra = 0
    readonly_fields = ('previous_arrival_date', 'previous_departure_date', 'created_at')
    fields = ('previous_arrival_date', 'previous_departure_date', 'new_arrival_date',
              'new_departure_date', 'additional_charge', 'notes', 'created_at')


class GuestListAdjustmentInline(admin.TabularInline):
    """Read-only audit trail only - unlike BookingDateAdjustment, this model has no save() override
    to auto-snapshot previous_party_size, since it's only ever meant to be created by the
    guest-facing Manage Booking hub alongside the actual new BookingGuest rows (see
    BookingManageGuestAddView) - letting staff hand-create a row here would produce an adjustment
    unlinked to any real added guest."""
    model = GuestListAdjustment
    extra = 0
    max_num = 0
    can_delete = False
    readonly_fields = ('previous_party_size', 'new_party_size', 'additional_charge', 'notes', 'created_at')
    fields = readonly_fields


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    inlines = [ChargeInline, PaymentInline, BalancePaymentInline, BookingGuestInline, PlatformPayoutInline,
               ExtraInline, BookingRequestedExtraInline, AirportTransferInline, ArrivalInline, DepartureInline,
               BookingDateAdjustmentInline, GuestListAdjustmentInline]
    list_display = ('reference', 'property', 'guest', 'arrival_date', 'departure_date', 'enquiry_status', 'enquiry_source')
    list_filter = ('enquiry_status', 'enquiry_source', 'manual_override')
    search_fields = ('reference', 'guest__first_name', 'guest__last_name', 'guest__email')

    def get_queryset(self, request):
        # Cheap bulk update, run on every changelist load - see bookings/utils.py::expire_stale_holds()
        # for why this can't clobber a genuinely in-progress payment.
        expire_stale_holds()
        return super().get_queryset(request)


class BalanceReminderDueFilter(admin.SimpleListFilter):
    """Whether this booking has crossed BookingSettings.balance_reminder_days_before_arrival and
    hasn't been reminded yet - the admin-surfaced replacement for an automated reminder email
    (not built yet, see bookings/models.py::BalancePayment). Staff message the guest manually,
    then use the "Mark reminder sent" action below to clear it from this filter."""
    title = 'reminder due'
    parameter_name = 'reminder_due'

    def lookups(self, request, model_admin):
        return (('yes', 'Due now'),)

    def queryset(self, request, queryset):
        if self.value() != 'yes':
            return queryset
        lead_days = BookingSettings.load().balance_reminder_days_before_arrival
        cutoff = timezone.now().date() + timedelta(days=lead_days)
        return queryset.filter(
            reminder_sent=False, status='pending', booking__arrival_date__lte=cutoff,
            # A booking whose deposit failed/was cancelled still has a dormant BalancePayment row -
            # only a genuinely confirmed booking should ever prompt a balance reminder.
            booking__enquiry_status__in=VALID_BOOKING_STATUSES,
        )


@admin.register(BalancePayment)
class BalancePaymentAdmin(admin.ModelAdmin):
    list_display = ('booking', 'provider', 'status', 'reminder_sent', 'created_at')
    list_filter = (BalanceReminderDueFilter, 'provider', 'status')
    search_fields = ('booking__reference', 'booking__guest__first_name', 'booking__guest__last_name')
    actions = ['mark_reminder_sent']

    @admin.action(description="Mark reminder sent")
    def mark_reminder_sent(self, request, queryset):
        queryset.update(reminder_sent=True, reminder_sent_at=timezone.now())


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
    list_display = ('name', 'category', 'order', 'active')
    list_display_links = ('name',)
    list_editable = ('category', 'order', 'active')
    list_filter = ('category',)
    ordering = ('category', 'order')


@admin.register(ExtrasSettings)
class ExtrasSettingsAdmin(admin.ModelAdmin):
    """Singleton admin: skips the changelist and goes straight to the one row's edit form."""

    def has_add_permission(self, request):
        return not ExtrasSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings = ExtrasSettings.load()
        return redirect(reverse('admin:bookings_extrassettings_change', args=[settings.pk]))


@admin.register(AirportTransferPriceBand)
class AirportTransferPriceBandAdmin(admin.ModelAdmin):
    list_display = ('max_guests', 'price')
    list_editable = ('price',)
    ordering = ('max_guests',)
