from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django_countries import countries as country_choices

import env_settings
from availability.utils import get_property_calendar
from bookings.models import (
    CURRENCY_CHOICES, MONTH_CHOICES, PAYMENT_STATUS_CHOICES, Arrival, Booking, BookingCondition,
    BookingSettings, CheckinSettings, Departure, ExtrasSettings, FAQ, PaymentSettings, PlatformPayout,
    RequestType, TravelMethod, WelcomePackItem,
)
from bookings.payouts import compute_owner_payout
from finance.models import AdHocService, DepositReturn, Memo, PayoutRecord
from finance.services import (
    backfill_memos_for_company, deposits_due_in_range, open_memo_for_property, owner_balance_in_range,
    payouts_due_in_range, sweep_unattached_ad_hoc_services,
)
from bookings.utils import (
    FLIGHT_NUMBER_HINT, compute_deposit_waiver, create_booking, create_owner_booking, extras_summary,
    parsed_arrival_departure_time, parsed_travel_method, sync_ical_link, valid_flight_number,
)
from communications.models import EmailTemplate, ScheduledEmail
from communications.registry import PLACEHOLDER_KEYS
from communications.services.sending import send_scheduled_email
from guests.models import Guest
from libraries.utils import logerror
from properties.models import (
    Accountant, Amenity, Location, LocationImage, LocationRules, LocationSpec, ManagementCompany,
    Owner, Platform, Price, Property, PropertyImage, PropertyOwnership, PropertyPlatformID,
    PropertySpec, SEFDetail, WashingMaterial, iCalLink,
)
from properties.utils import (
    apply_price_bulk_plan, build_price_bulk_plan, get_stay_total_price, gross_up_for_commission,
)
from staff.models import Checkin, CleaningTask, Deduction, OwnerPayment, StaffProfile, StaffRole, TaskHistoryEntry
from staff.monthly_reports import (
    EXTRAS_METRICS, REVENUE_GROUPS, bookings_trend_rows, commissions_trend_rows, extras_trend_rows,
    location_groups, management_trend_rows, monthly_bookings_rows, monthly_commissions_rows,
    monthly_extras_rows, monthly_management_rows, monthly_revenue_rows, monthly_stays_rows,
    revenue_trend_rows, stays_trend_rows,
)
from staff.permissions import can_send_emails, staff_email_action_required, staff_page_required, superuser_required
from staff.reports import REPORT_COLUMNS, booking_report_rows, report_totals
from staff.utils import (
    AMENITY_BOOLEAN_FIELDS, CLOSED_STATUSES, ENQUIRY_STATUS_GROUPS, ENQUIRY_STATUSES, GUEST_LETTERS,
    LOCATION_SPEC_BOOLEAN_FIELDS, OWNER_BOOLEAN_FIELDS, REVIVABLE_STATUSES, STAFF_PAGE_PERMISSION_FIELDS,
    STAGE_TABS, STATUS_BUCKETS, apply_manual_checkin_time, apply_manual_task_date,
    batch_cleaning_task_valid_ranges, booking_stage, checkin_valid_range, cleaning_task_valid_range,
    compute_arrival_eta, next_step_hint, properties_grouped_by_location, property_last_clean_before,
    reservation_rows,
)
from staff.utils import last_day_of_month as _last_day_of_month
from staff.utils import parsed_date as _parsed_date


@method_decorator(staff_page_required('can_view_home'), name='dispatch')
class StaffHomeView(View):
    """Mini-calendars + active-reservations list, per property or across all of them - mirrors
    PIMS' own Home screen. Reuses availability/utils.py::get_property_calendar() as-is (built for
    the guest-facing property page's own calendar - same day-status logic, called once per
    property shown here rather than modified) and staff/utils.py::booking_stage() (built for the
    booking detail page) for the reservation table's Status column. Deliberately no PIMS-style
    "BLOCK - Unbookable" rows (no such model exists) or overlap-warning icons - see the plan this
    was built from for what's deferred.

    The filter bar (property/status/checkboxes) is a single auto-submitting GET form
    (staff/templates/staff/home.html) - every real submission of it therefore always carries a
    full, unambiguous query string (a <select> and every checkbox's on/off state is always present
    or absent exactly as the browser currently shows them), even one that resets every filter back
    to its default. The nav bar's own "Home" link, by contrast, is a bare `staff:home` URL with no
    query string at all - clicking it used to always reset to the true defaults, discarding
    whatever a staffer had just filtered to, no matter how recently (2026-09-03, per Thomas: this
    was a real annoyance clicking back in from a booking they'd found by filtering to one
    property). Persisted in request.session (per-staffer, survives across tabs and any amount of
    time away, same lifetime as their login session - no client-side storage needed) rather than
    via a redirect from the nav link itself, since the link is a plain <a> with no server
    involvement until it's followed: an empty query string on this view now means "restore the
    last filter this staffer used" rather than "no filter", and a real (possibly filter-resetting)
    query string always overwrites what's remembered, exactly matching "only change when those
    filters are changed again"."""
    template_name = 'staff/home.html'
    SESSION_KEY = 'staff_home_query_string'

    def get(self, request, *args, **kwargs):
        query_string = request.META.get('QUERY_STRING', '')
        if query_string:
            request.session[self.SESSION_KEY] = query_string
        else:
            remembered = request.session.get(self.SESSION_KEY)
            if remembered:
                return redirect(f'{request.path}?{remembered}')

        properties = Property.objects.select_related('location').all()

        selected_property = None
        property_id = request.GET.get('property', '').strip()
        if property_id.isdigit():
            selected_property = properties.filter(pk=property_id).first()

        property_groups = properties_grouped_by_location(properties)

        shown_properties = [selected_property] if selected_property else list(properties)
        months = 12 if selected_property else 3
        calendars = [
            {'property': property, 'months': get_property_calendar(property, months=months)}
            for property in shown_properties
        ]

        status_filter = request.GET.get('status', '').strip() or 'Valid'
        base = Booking.objects.filter(property=selected_property) if selected_property else Booking.objects.all()

        direct_only = request.GET.get('direct_only') == 'on'
        ical_only = request.GET.get('ical_only') == 'on'
        owner_only = request.GET.get('owner_only') == 'on'
        exclude_owner = request.GET.get('exclude_owner') == 'on'
        if direct_only:
            # Same "direct vs platform" definition already used for payout math - see
            # bookings/payouts.py::_is_platform_booking().
            base = base.exclude(enquiry_source__in=env_settings.PLATFORMS)
        if ical_only:
            # Was ical_uid-based until 2026-09-03 (a real bug, per Thomas): ical_uid is only ever
            # populated by bookings/utils.py::sync_ical_link(), the *live* sync mechanism, so it
            # only caught bookings synced after that feature existed - 1,990 of 2,078 real
            # platform-sourced bookings (96%) had no ical_uid at all (correctly enquiry_source-
            # tagged at migration time, never backfilled with one), making this checkbox
            # effectively blind to almost every historic platform booking. Now the exact inverse of
            # direct_only above - "iCal Only" is a legacy label for what's really "Platform Only"
            # (the template's checkbox text was updated to match; the GET param name/session-
            # persisted filter state deliberately weren't, to avoid breaking either).
            base = base.filter(enquiry_source__in=env_settings.PLATFORMS)
        if owner_only:
            base = base.filter(is_owner=True)
        if exclude_owner:
            base = base.filter(is_owner=False)

        rows = reservation_rows(base, status_filter)

        context = {
            'property_groups': property_groups,
            'selected_property': selected_property,
            'calendars': calendars,
            'rows': rows,
            'status_buckets': STATUS_BUCKETS,
            'status_filter': status_filter,
            'direct_only': direct_only,
            'ical_only': ical_only,
            'owner_only': owner_only,
            'exclude_owner': exclude_owner,
        }
        return render(request, self.template_name, context)


@method_decorator(staff_page_required('can_view_bookings'), name='dispatch')
class StaffBookingLookupView(View):
    """A single reference lookup, not a search - for quickly opening a known booking without
    going through the Home reservation list (see StaffHomeView)."""
    template_name = 'staff/booking_lookup.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {})

    def post(self, request, *args, **kwargs):
        reference = request.POST.get('reference', '').strip()
        if Booking.objects.filter(reference__iexact=reference).exists():
            return redirect('staff:booking_detail', reference=reference)
        messages.error(request, f'No booking found for reference "{reference}".')
        return render(request, self.template_name, {})


@method_decorator(staff_page_required('can_view_bookings'), name='dispatch')
class StaffOwnerBookingCreateView(View):
    """Staff-side equivalent of owners/views.py::OwnerBookingCreateView, for an owner who's "too
    busy" to use their own Owner Suite - same create_owner_booking() call, unchanged, just with
    an unrestricted property picker instead of one scoped to a single owner's own properties (the
    owner here is derived from property.owner, not a logged-in owner session). No Charge/Payment
    at all, same as the Owner Suite version - an owner stay is never charged. No automated
    confirmation email - this codebase has no outbound email anywhere yet (see e.g.
    StaffFinanceMemoSendView's own docstring) - so success just shows a copy/paste summary for
    staff to send the owner manually."""
    template_name = 'staff/booking_create_owner.html'

    def get(self, request, *args, **kwargs):
        context = self._context()
        context.update({
            'arrival_date': '', 'departure_date': '', 'adults': '2', 'children': '0', 'babies': '0',
            'clean_checked': True, 'meet_greet_checked': True,
        })
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        post = request.POST
        context = self._context()
        context.update({
            'selected_property_id': post.get('property_id', ''),
            'arrival_date': post.get('arrival_date', ''),
            'departure_date': post.get('departure_date', ''),
            'adults': post.get('adults', '2'),
            'children': post.get('children', '0'),
            'babies': post.get('babies', '0'),
            'clean_checked': post.get('clean') == 'on',
            'meet_greet_checked': post.get('meet_greet') == 'on',
        })

        property_id = post.get('property_id', '')
        property = Property.objects.select_related('owner').filter(pk=property_id).first() if property_id.isdigit() else None
        if property is None:
            messages.error(request, "Please choose a property.")
            return render(request, self.template_name, context)
        if property.owner_id is None:
            messages.error(request, "This property has no owner on file, so a staff-created owner booking isn't possible.")
            return render(request, self.template_name, context)

        arrival_date = _parsed_date(post.get('arrival_date'))
        departure_date = _parsed_date(post.get('departure_date'))
        if not arrival_date or not departure_date:
            messages.error(request, "Please provide both an arrival and departure date.")
            return render(request, self.template_name, context)

        try:
            booking = create_owner_booking(
                property, property.owner, arrival_date, departure_date,
                adults=_parsed_int(post.get('adults')) or 1,
                children=_parsed_int(post.get('children')) or 0,
                babies=_parsed_int(post.get('babies')) or 0,
                clean=post.get('clean') == 'on', meet_greet=post.get('meet_greet') == 'on',
            )
        except ValidationError as error:
            message = " ".join(error.messages) if hasattr(error, 'messages') else str(error)
            messages.error(request, message)
            return render(request, self.template_name, context)

        messages.success(
            request,
            f"Reservation created for {property.owner}. No confirmation email is sent automatically yet - "
            f"copy this to send it yourself: {property} from {arrival_date:%d %b %Y} to {departure_date:%d %b %Y}, "
            f"{booking.adults} adult(s), {booking.children} child(ren), {booking.babies} baby/babies."
        )
        return redirect('staff:booking_detail', reference=booking.reference)

    def _context(self):
        return {'property_groups': properties_grouped_by_location(Property.objects.select_related('location').all())}


@method_decorator(staff_page_required('can_view_bookings'), name='dispatch')
class StaffGuestOfferCreateView(View):
    """Staff picks property/dates/guests, sees the normally-calculated price with an optional
    one-click % discount layered on top (Thomas: percent + a reason, not a flat amount), then gets
    a link to send the guest manually (no email infra exists - same stopgap as
    StaffOwnerBookingCreateView) that skips straight past name/email/phone/country entry, landing
    right where a normal guest would be after that step - see bookings/views.py::
    BookingOfferOpenView for how that's made safe (bare reference possession isn't enough; scoped
    to enquiry_source='Staff offer' bookings only, so this doesn't turn every booking's reference
    into a bearer-cancellable one). The payment hold timer starts the moment this view creates the
    Booking, not when the guest opens the link (Thomas's explicit call, 2026-09-04) - reuses
    create_booking()'s existing hold logic completely unchanged.

    Same page/view handles both the live price preview (a plain re-render, 'action' != 'create')
    and the actual creation ('action' == 'create') - mirrors ReserveView's GET-quote-then-POST
    pattern from the public reserve page, just collapsed into one POST-only staff form since
    there's no separate guest-facing quote step to keep in sync here."""
    template_name = 'staff/booking_create_offer.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._context(request.GET))

    def post(self, request, *args, **kwargs):
        context = self._context(request.POST)
        if request.POST.get('action') != 'create':
            return render(request, self.template_name, context)

        if context['property'] is None or context['pricing'] is None:
            messages.error(request, "Choose a property and valid dates before creating an offer.")
            return render(request, self.template_name, context)

        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        if not last_name or not email:
            messages.error(request, "The guest's last name and email are required.")
            return render(request, self.template_name, context)

        guest_data = {
            'first_name': request.POST.get('first_name', '').strip(),
            'last_name': last_name,
            'email': email,
            'phone': request.POST.get('phone', '').strip(),
            'country': request.POST.get('country', '').strip() or None,
        }
        try:
            booking = create_booking(
                context['property'], guest_data, context['start_date'], context['end_date'],
                context['guests'], enquiry_source='Staff offer',
                manual_discount_percent=context['discount_percent'],
                manual_discount_reason=request.POST.get('discount_reason', '').strip(),
            )
        except ValidationError as error:
            message = " ".join(error.messages) if hasattr(error, 'messages') else str(error)
            messages.error(request, message)
            return render(request, self.template_name, context)

        offer_url = request.build_absolute_uri(
            reverse('bookings:offer_open', kwargs={'reference': booking.reference})
        )
        return render(request, 'staff/booking_create_offer_confirm.html', {
            'booking': booking, 'offer_url': offer_url, 'charge': booking.charges,
        })

    def _context(self, params):
        property_id = params.get('property_id', '')
        property = Property.objects.select_related('owner').filter(pk=property_id).first() if property_id.isdigit() else None
        start_date = _parsed_date(params.get('start_date'))
        end_date = _parsed_date(params.get('end_date'))
        adults = _parsed_int(params.get('adults')) or 0
        children = _parsed_int(params.get('children')) or 0
        babies = _parsed_int(params.get('babies')) or 0
        guests = {'adults': adults, 'children': children, 'infants': babies}
        discount_percent = _parsed_decimal(params.get('discount_percent'))

        pricing = None
        costs = None
        if property is not None and start_date and end_date and end_date > start_date:
            booking_settings = BookingSettings.load()
            raw_pricing = get_stay_total_price(
                property, start_date, end_date, guests,
                monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
            )
            if raw_pricing is not None:
                manual_discount_amount = Decimal('0.00')
                if discount_percent:
                    manual_discount_amount = (
                        raw_pricing['basic_total'] * discount_percent / Decimal('100')
                    ).quantize(Decimal('0.01'))
                discount_total = raw_pricing['discount_total'] + manual_discount_amount
                rental_total = raw_pricing['basic_total'] - discount_total + raw_pricing['extra_guest_total']
                costs = booking_settings.compute_costs(rental_total, arrival_date=start_date)
                pricing = {
                    **raw_pricing, 'discount_total': discount_total,
                    'manual_discount_amount': manual_discount_amount, 'rental_total': rental_total,
                }

        return {
            'property_groups': properties_grouped_by_location(Property.objects.select_related('location').all()),
            'countries': country_choices,
            'selected_property_id': property_id,
            'property': property,
            'start_date': start_date, 'end_date': end_date,
            'start_date_raw': params.get('start_date', ''), 'end_date_raw': params.get('end_date', ''),
            'adults': params.get('adults', '2'), 'children': params.get('children', '0'),
            'babies': params.get('babies', '0'),
            'discount_percent': discount_percent, 'discount_percent_raw': params.get('discount_percent', ''),
            'discount_reason': params.get('discount_reason', ''),
            'first_name': params.get('first_name', ''), 'last_name': params.get('last_name', ''),
            'email': params.get('email', ''), 'phone': params.get('phone', ''),
            'country': params.get('country', ''),
            'guests': guests,
            'pricing': pricing, 'costs': costs,
        }


@method_decorator(staff_page_required('can_view_guests'), name='dispatch')
class StaffGuestListView(View):
    """PIMS-style "All Customers" list - an A-Z surname index plus a free-text search box (name,
    email, or phone - phone included so an unfamiliar incoming call can be matched to a guest),
    both over guests.models.Guest. klt-web's Guest has no address field (PIMS' own screenshot
    shows one) so that column is simply dropped rather than faked. Each row links to
    StaffGuestDetailView, klt-web's equivalent of PIMS' "View/Modify Customer" page."""
    template_name = 'staff/guest_list.html'

    def get(self, request, *args, **kwargs):
        query = request.GET.get('q', '').strip()
        letter = request.GET.get('letter', '').strip().upper()

        guests = Guest.objects.all()
        if query:
            letter = ''
            guests = guests.filter(
                Q(first_name__icontains=query) | Q(last_name__icontains=query)
                | Q(email__icontains=query) | Q(phone__icontains=query)
            )
        else:
            # No letter in the URL yet (first visit) defaults to the first letter, matching PIMS'
            # own behaviour rather than dumping every guest on first load; 'ALL' is a real,
            # explicit choice ("anything") that bypasses the surname filter entirely.
            letter = letter or GUEST_LETTERS[0]
            if letter != 'ALL':
                guests = guests.filter(last_name__istartswith=letter)
        guests = guests.order_by('last_name', 'first_name')

        context = {
            'guests': guests,
            'letters': GUEST_LETTERS,
            'selected_letter': letter,
            'query': query,
        }
        return render(request, self.template_name, context)


@method_decorator(staff_page_required('can_view_guests'), name='dispatch')
class StaffGuestDetailView(View):
    """PIMS-style "View/Modify Customer" page - a guest-info form on the left (PIMS' own address/
    flight/extras/discount fields have no klt-web equivalent - those are per-booking concepts
    already covered on the booking detail page - so the fields shown here are exactly what
    guests.models.Guest actually stores) and, on the right, that guest's reservations using the
    same Valid/Invalid/Ended/All filter as StaffHomeView (via the shared reservation_rows()
    helper) but without a property selector - Thomas confirmed one guest's own booking list
    doesn't need that extra granularity. Defaults to 'All' rather than StaffHomeView's 'Valid' -
    unlike the home page's live reservation list, a single guest here often has only a
    cancelled/ended booking, so defaulting to 'Valid' just shows an empty table."""
    template_name = 'staff/guest_detail.html'

    def _get_guest(self, pk):
        guest = Guest.objects.filter(pk=pk).first()
        if guest is None:
            raise Http404("No guest found.")
        return guest

    def get(self, request, pk, *args, **kwargs):
        guest = self._get_guest(pk)
        status_filter = request.GET.get('status', '').strip() or 'All'
        context = {
            'guest': guest,
            'rows': reservation_rows(Booking.objects.filter(guest=guest), status_filter),
            'status_buckets': STATUS_BUCKETS,
            'status_filter': status_filter,
        }
        return render(request, self.template_name, context)

    def post(self, request, pk, *args, **kwargs):
        guest = self._get_guest(pk)
        post = request.POST
        guest.first_name = post.get('first_name', guest.first_name or '').strip()
        guest.last_name = post.get('last_name', guest.last_name).strip() or guest.last_name
        guest.email = post.get('email', guest.email or '').strip()
        guest.phone = post.get('phone', guest.phone or '').strip()
        preferred_language = post.get('preferred_language', '').strip()
        if preferred_language:
            guest.preferred_language = preferred_language
        guest.save()
        messages.success(request, "Guest info updated.")
        return redirect('staff:guest_detail', pk=guest.pk)


def _parsed_team(raw):
    """Falls back to 1 for anything missing/invalid rather than rejecting the save outright - the
    team picker is a visual-grouping aid, not data worth blocking a clean-planner save over."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return value if value in dict(CleaningTask.TEAM_CHOICES) else 1


def _flash_validation_error(request, error):
    """ValidationError.messages repeats a message once per field it's attached to (e.g. an
    overlap error raised against both start_date and end_date), so dedupe before joining."""
    messages.error(request, '; '.join(dict.fromkeys(error.messages)))


def _field_changes(field_labels, before, after):
    """"Label 'old' to 'new'" fragments for every field whose value actually differs between two
    same-shaped, positionally-matched value tuples - same phrasing as the Status changed/Booking
    dates updated history entries, used to give the (otherwise blank) Arrival/Departure updated
    entry the same level of detail."""
    return [f"{label} '{b}' to '{a}'" for label, b, a in zip(field_labels, before, after) if b != a]


def _conflict_query(queryset):
    """Query-string fragment carrying which rows to highlight after a redirect, for handlers
    whose validation failure should flag existing conflicting rows (e.g. a Price overlap)."""
    ids = ','.join(str(pk) for pk in queryset.values_list('pk', flat=True))
    return f"conflict={ids}" if ids else None


def _parsed_decimal(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parsed_int(raw):
    raw = (raw or '').strip()
    return int(raw) if raw.isdigit() else None


def _parsed_time(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%H:%M').time()
    except ValueError:
        return None


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffPropertyListView(View):
    """PIMS puts property CRUD behind Settings rather than a dedicated list; klt-web instead
    mirrors the Home/Guests list pattern already used elsewhere - a list filterable by Location
    (Thomas's own suggestion) with an "+ Add new property" link above it."""
    template_name = 'staff/property_list.html'

    def get(self, request, *args, **kwargs):
        locations = Location.objects.order_by('title')
        selected_location = None
        location_id = request.GET.get('location', '').strip()
        if location_id.isdigit():
            selected_location = locations.filter(pk=location_id).first()

        properties = Property.objects.select_related('location', 'owner', 'booking_company', 'cleaning_company').order_by('title')
        if selected_location:
            properties = properties.filter(location=selected_location)

        context = {
            'properties': properties,
            'locations': locations,
            'selected_location': selected_location,
        }
        return render(request, self.template_name, context)


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffPriceBulkToolsView(View):
    """Bulk-adjusts a whole year's rate card at once, either in place (raise/cut every price row
    in a given year by a %) or by cloning one year's rows forward into a new year (shifted dates,
    same % applied to rate) - see properties/utils.py::build_price_bulk_plan/apply_price_bulk_plan
    for the actual computation, which this view is the only caller of (2026-09-04: Thomas doesn't
    want a Django admin equivalent - staff app is the only surface for this). Two-step confirm
    (preview, then a resubmit carrying step=confirm) so a wrong year/percentage doesn't silently
    rewrite hundreds of rows before anyone's seen what it would do."""
    template_name = 'staff/property_bulk_prices.html'

    def _bookable_properties(self):
        # Excludes properties with no booking_company - not sold through our own booking flow at
        # all, so they routinely have no current-year pricing and would just be noise in the
        # picker and in 'no_data' reporting (see build_price_bulk_plan's own docstring).
        return Property.objects.exclude(booking_company__isnull=True).order_by('title')

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {'properties': self._bookable_properties()})

    def post(self, request, *args, **kwargs):
        post = request.POST
        mode = post.get('mode')
        percent = _parsed_decimal(post.get('percent'))
        year = _parsed_int(post.get('year'))
        source_year = _parsed_int(post.get('source_year'))
        target_year = _parsed_int(post.get('target_year'))
        scale_extra_rates = post.get('scale_extra_rates') == 'on'
        selected_ids = [pk for pk in post.getlist('properties') if pk.isdigit()]
        selected_properties = Property.objects.filter(pk__in=selected_ids) if selected_ids else None

        errors = []
        if mode not in ('adjust', 'clone'):
            errors.append("Choose whether to adjust a year or clone one forward.")
        if percent is None:
            errors.append("Enter a percentage change.")
        if mode == 'adjust' and not year:
            errors.append("Enter the year to adjust.")
        if mode == 'clone':
            if not source_year:
                errors.append("Enter a source year.")
            if not target_year:
                errors.append("Enter a target year.")
            if source_year and target_year and source_year == target_year:
                errors.append("The target year must differ from the source year.")

        context = {
            'properties': self._bookable_properties(),
            'mode': mode, 'percent': post.get('percent', ''),
            'year': post.get('year', ''), 'source_year': post.get('source_year', ''),
            'target_year': post.get('target_year', ''),
            'scale_extra_rates': scale_extra_rates,
            'selected_ids': {int(pk) for pk in selected_ids},
        }
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, self.template_name, context)

        plan = build_price_bulk_plan(
            mode, percent, properties=selected_properties, year=year,
            source_year=source_year, target_year=target_year, scale_extra_rates=scale_extra_rates,
        )

        if post.get('step') == 'confirm':
            count = apply_price_bulk_plan(plan)
            verb = 'Updated' if mode == 'adjust' else 'Created'
            notes = []
            if plan['skipped']:
                notes.append(f"{len(plan['skipped'])} row(s) skipped - already priced in the target year")
            if plan['no_data']:
                notes.append(f"{len(plan['no_data'])} selected propert{'y' if len(plan['no_data']) == 1 else 'ies'} had no price rows to touch")
            note_text = f" ({'; '.join(notes)})" if notes else ''
            messages.success(request, f"{verb} {count} price rows.{note_text}")
            return redirect('staff:property_bulk_prices')

        context['plan'] = plan
        preview_source = plan['updates'] or plan['creates']
        context['preview_rows'] = preview_source[:100]
        context['preview_truncated'] = len(preview_source) > 100
        return render(request, self.template_name, context)


@method_decorator(staff_page_required('can_view_locations'), name='dispatch')
class StaffLocationListView(View):
    """Location's own list page, same shape as StaffPropertyListView minus the filter (there's no
    obvious thing to filter Locations by) - each row also shows how many properties currently
    point at it, so staff can tell at a glance which locations are actually in use."""
    template_name = 'staff/location_list.html'

    def get(self, request, *args, **kwargs):
        context = {
            'locations': Location.objects.annotate(property_count=Count('property')).order_by('title'),
        }
        return render(request, self.template_name, context)


def _property_form_context():
    return {
        'owners': Owner.objects.order_by('name'),
        'locations': Location.objects.order_by('title'),
        'accountants': Accountant.objects.order_by('company'),
        'management_companies': ManagementCompany.objects.order_by('name'),
        'owner_boolean_fields': OWNER_BOOLEAN_FIELDS,
        'platforms': Platform.objects.all(),
    }


def _save_platform_ids(request, property):
    """Upserts one PropertyPlatformID per Platform from POST fields named
    'platform_listing_<platform.pk>' - a blank value deletes any existing row for that platform.
    Shared by StaffPropertyCreateView and StaffPropertyDetailView._update_property_info so both
    forms behave identically."""
    for platform in Platform.objects.all():
        value = request.POST.get(f'platform_listing_{platform.pk}', '').strip()
        if value:
            PropertyPlatformID.objects.update_or_create(
                property=property, platform=platform, defaults={'listing_id': value},
            )
        else:
            PropertyPlatformID.objects.filter(property=property, platform=platform).delete()


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffPropertyCreateView(View):
    """A minimal create form for exactly the fields StaffPropertyDetailView's "Property info"
    panel edits - everything else (specification, amenities, rate card, SEF details, iCal links,
    photos) only makes sense once the property row exists, so those stay detail-page-only."""
    template_name = 'staff/property_create.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, _property_form_context())

    def post(self, request, *args, **kwargs):
        post = request.POST
        property = Property(
            title=post.get('title', '').strip(),
            short_title=post.get('short_title', '').strip(),
            door_number=post.get('door_number', '').strip(),
            owner_id=post.get('owner') or None,
            location_id=post.get('location') or None,
            accountant_id=post.get('accountant') or None,
            al_number=_parsed_int(post.get('al_number')),
            booking_company_id=post.get('booking_company') or None,
            cleaning_company_id=post.get('cleaning_company') or None,
            standard_cleaning_fee=_parsed_decimal(post.get('standard_cleaning_fee')) or 0,
        )
        try:
            property.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return render(request, self.template_name, _property_form_context())
        property.save()
        _save_platform_ids(request, property)
        if property.owner_id:
            PropertyOwnership.record_initial_ownership(property, property.owner)
        messages.success(request, "Property created.")
        return redirect('staff:property_detail', pk=property.pk)


@method_decorator(staff_page_required('can_view_locations'), name='dispatch')
class StaffLocationCreateView(View):
    """Same minimal-fields philosophy as StaffPropertyCreateView - exactly the fields the existing
    quick-add panel already asks for (see StaffQuickAddView._build_location), since a location
    used to only ever be creatable from there. Specification, rules and photos only make sense
    once the row exists, so those stay detail-page-only."""
    template_name = 'staff/location_create.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {})

    def post(self, request, *args, **kwargs):
        post = request.POST
        location = Location(
            title=post.get('title', '').strip(),
            street=post.get('street', '').strip(),
            zip_code=post.get('zip_code', '').strip(),
            city=post.get('city', '').strip(),
            coordinates=post.get('coordinates', '').strip(),
            map_link=post.get('map_link', '').strip(),
        )
        try:
            location.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return render(request, self.template_name, {})
        location.save()
        messages.success(request, "Location created.")
        return redirect('staff:location_detail', pk=location.pk)


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffQuickAddView(View):
    """Backs the "+ New" quick-add panels next to the Location/Owner/Accountant/Management company
    dropdowns on the Create Property page - fetch()-only (no full-page navigation), so creating one of these
    can never disturb whatever's already been typed into the rest of the Create Property form.
    Returns the new row as JSON so the calling page's <select> can just gain a new, pre-selected
    option in place. Only touches full_clean()-valid rows; unique-field clashes (e.g. a duplicate
    Owner email) come back as a normal JSON error for the panel to display inline."""

    def post(self, request, model, *args, **kwargs):
        builder = {
            'location': self._build_location,
            'owner': self._build_owner,
            'accountant': self._build_accountant,
            # Keyed by the FK field name it populates (booking_company), not the model class name,
            # matching this dict's existing convention (owner/accountant/location all use their FK
            # field name too) - the quick-add JS finds its target <select> by that same name (see
            # property_create.js), and ManagementCompany has two candidate selects
            # (booking_company/cleaning_company) with no single field name matching the class.
            'booking_company': self._build_management_company,
        }.get(model)
        if builder is None:
            return JsonResponse({'error': 'Unknown type.'}, status=404)
        instance = builder(request.POST)
        try:
            instance.full_clean()
        except ValidationError as error:
            return JsonResponse({'error': '; '.join(dict.fromkeys(error.messages))}, status=400)
        instance.save()
        return JsonResponse({'id': instance.pk, 'label': str(instance)})

    def _build_location(self, post):
        return Location(
            title=post.get('title', '').strip(),
            street=post.get('street', '').strip(),
            zip_code=post.get('zip_code', '').strip(),
            city=post.get('city', '').strip(),
            coordinates=post.get('coordinates', '').strip(),
            map_link=post.get('map_link', '').strip(),
        )

    def _build_owner(self, post):
        fields = {name: post.get(name) == 'on' for name, _ in OWNER_BOOLEAN_FIELDS}
        return Owner(
            name=post.get('name', '').strip(),
            email=post.get('email', '').strip(),
            **fields,
        )


    def _build_accountant(self, post):
        return Accountant(
            company=post.get('company', '').strip(),
            name=post.get('name', '').strip(),
            email=post.get('email', '').strip(),
            phone=post.get('phone', '').strip(),
        )

    def _build_management_company(self, post):
        # Just the essentials here (name + optionally a head contact) - the other four contact
        # roles (maintenance/liaison/cleaning/finance) are genuinely optional and left blank until
        # filled in later via Settings > People, rather than defaulting to a copy of the head
        # contact (every role can differ, or simply not apply, depending on the company's scope).
        return ManagementCompany(
            name=post.get('name', '').strip(),
            head_name=post.get('head_name', '').strip(),
            head_email=post.get('head_email', '').strip(),
            head_phone=post.get('head_phone', '').strip(),
        )


@method_decorator(staff_page_required('can_view_settings'), name='dispatch')
class StaffSettingsView(View):
    """Site-wide configuration the Staff admin doesn't have a page for yet, pulled out of
    Django-admin-only territory into one CSS-only tabbed page (same radio-input sidebar
    technique as StaffPropertyDetailView). Bookings/Extras wrap the existing BookingSettings/
    ExtrasSettings singletons (plus Extras' three admin-only catalog lists, each following the
    Rate card's inline-edit-plus-blank-bottom-row table pattern). Staff is basic Django User
    management (list/add accounts, toggle is_staff/is_superuser/is_active, assign the one
    StaffRole each account holds) - password resets still punt to Django admin. Roles is the
    real role/permission model this app was missing until 2026-08-26 (see staff.models.StaffRole/
    StaffProfile, staff.permissions.staff_page_required): superusers define named roles and
    toggle, per role, which top-level staff pages it can see (page-level and visibility-only by
    design, not per-panel or view/edit-split - see STAFF_PAGE_PERMISSION_FIELDS). Staff and Roles
    are BOTH superuser-only regardless of can_view_settings - see PANELS/_available_panels() and
    the {% if request.user.is_superuser %} wrap around both panels' content in settings.html;
    `can_view_settings` alone only grants Bookings/Extras/People/Payments. People gives
    Owner/Accountant/Management company full CRUD (previously select-only on the Property forms,
    see StaffQuickAddView) via the same inline-edit-plus-blank-bottom-row table pattern as Extras'
    catalog lists - Management company's table shows the head contact inline, with the other four
    contact roles (maintenance/liaison/cleaning/finance) editable via a per-row "More fields"
    toggle (staff-management-company-details-row, see settings.html/settings.js) rather than a
    table column each, since 5 roles x 3 fields doesn't fit a table row; every role is genuinely
    optional, left blank until filled in. Each of these tables also shows a live count of
    properties (or, for Roles, users) currently pointing at that row and warns (client-side,
    settings.js) before a delete that would orphan them, since Property's owner/accountant FKs,
    ManagementCompany's booking/cleaning FKs, and StaffProfile's role FK are all SET_NULL rather
    than protected. Payments is a new PaymentSettings singleton storing default owner-payout/
    commission percentages that nothing else in the app reads yet - a deliberate starting point,
    not a finished payout system."""
    template_name = 'staff/settings.html'
    PANELS = ('bookings', 'extras', 'staff', 'people', 'payments', 'emails', 'roles')
    SUPERUSER_ONLY_PANELS = ('staff', 'roles')
    SUPERUSER_ONLY_ACTIONS = (
        'add_staff_user', 'update_staff_user', 'add_role', 'update_role', 'delete_role',
    )
    ACTION_PANELS = {
        'update_booking_settings': 'bookings',
        'update_checkin_settings': 'bookings',
        'add_booking_condition': 'bookings',
        'update_booking_condition': 'bookings',
        'delete_booking_condition': 'bookings',
        'add_faq': 'bookings',
        'update_faq': 'bookings',
        'delete_faq': 'bookings',
        'add_platform': 'bookings',
        'update_platform': 'bookings',
        'delete_platform': 'bookings',
        'update_extras_settings': 'extras',
        'add_welcome_pack_item': 'extras',
        'update_welcome_pack_item': 'extras',
        'delete_welcome_pack_item': 'extras',
        'add_request_type': 'extras',
        'update_request_type': 'extras',
        'delete_request_type': 'extras',
        'add_staff_user': 'staff',
        'update_staff_user': 'staff',
        'add_role': 'roles',
        'update_role': 'roles',
        'delete_role': 'roles',
        'add_owner': 'people',
        'update_owner': 'people',
        'delete_owner': 'people',
        'add_accountant': 'people',
        'update_accountant': 'people',
        'delete_accountant': 'people',
        'add_management_company': 'people',
        'update_management_company': 'people',
        'delete_management_company': 'people',
        'add_washing_material': 'people',
        'delete_washing_material': 'people',
        'update_payment_settings': 'payments',
        'update_email_template': 'emails',
    }

    def _available_panels(self, request):
        # Staff and Roles are the meta-configuration surface itself (who can see what) - kept
        # superuser-only regardless of can_view_settings, which only ever grants Bookings/Extras/
        # People/Payments. Filtering here (not just hiding the tab controls in the template) is
        # what stops ?panel=staff URL-tampering from landing a non-superuser on that tab.
        if request.user.is_superuser:
            return self.PANELS
        return tuple(panel for panel in self.PANELS if panel not in self.SUPERUSER_ONLY_PANELS)

    def get(self, request, *args, **kwargs):
        panel = request.GET.get('panel', '')
        available_panels = self._available_panels(request)
        active_panel = panel if panel in available_panels else 'bookings'
        return render(request, self.template_name, self._context(active_panel))

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        if action in self.SUPERUSER_ONLY_ACTIONS and not request.user.is_superuser:
            raise PermissionDenied
        handler = {
            'update_booking_settings': self._update_booking_settings,
            'update_checkin_settings': self._update_checkin_settings,
            'add_booking_condition': self._add_booking_condition,
            'update_booking_condition': self._update_booking_condition,
            'delete_booking_condition': self._delete_booking_condition,
            'add_faq': self._add_faq,
            'update_faq': self._update_faq,
            'delete_faq': self._delete_faq,
            'add_platform': self._add_platform,
            'update_platform': self._update_platform,
            'delete_platform': self._delete_platform,
            'update_extras_settings': self._update_extras_settings,
            'add_welcome_pack_item': self._add_welcome_pack_item,
            'update_welcome_pack_item': self._update_welcome_pack_item,
            'delete_welcome_pack_item': self._delete_welcome_pack_item,
            'add_request_type': self._add_request_type,
            'update_request_type': self._update_request_type,
            'delete_request_type': self._delete_request_type,
            'add_staff_user': self._add_staff_user,
            'update_staff_user': self._update_staff_user,
            'add_role': self._add_role,
            'update_role': self._update_role,
            'delete_role': self._delete_role,
            'add_owner': self._add_owner,
            'update_owner': self._update_owner,
            'delete_owner': self._delete_owner,
            'add_accountant': self._add_accountant,
            'update_accountant': self._update_accountant,
            'delete_accountant': self._delete_accountant,
            'add_management_company': self._add_management_company,
            'update_management_company': self._update_management_company,
            'delete_management_company': self._delete_management_company,
            'add_washing_material': self._add_washing_material,
            'delete_washing_material': self._delete_washing_material,
            'update_payment_settings': self._update_payment_settings,
            'update_email_template': self._update_email_template,
        }.get(action)
        if handler is not None:
            handler(request)
        panel = self.ACTION_PANELS.get(action, 'bookings')
        return redirect(f"{reverse('staff:settings')}?panel={panel}")

    def _context(self, active_panel):
        return {
            'active_panel': active_panel,
            'booking_settings': BookingSettings.load(),
            'email_templates': self._email_templates_with_placeholder_hints(),
            'checkin_settings': CheckinSettings.load(),
            'booking_conditions': BookingCondition.objects.all(),
            'faqs': FAQ.objects.select_related('location').all(),
            'faq_locations': Location.objects.order_by('title'),
            'extras_settings': ExtrasSettings.load(),
            'payment_settings': PaymentSettings.load(),
            'month_choices': MONTH_CHOICES,
            'welcome_pack_items': WelcomePackItem.objects.all(),
            'welcome_pack_categories': WelcomePackItem.Category.choices,
            'request_types': RequestType.objects.all(),
            'platforms': Platform.objects.all(),
            # select_related across the reverse OneToOne (staff_profile) plus its role avoids an
            # N+1 when the Staff tab's role <select> renders each user's current role.
            'staff_users': User.objects.select_related('staff_profile__role').order_by('username'),
            'roles': StaffRole.objects.annotate(user_count=Count('profiles')).order_by('name'),
            'permission_fields': STAFF_PAGE_PERMISSION_FIELDS,
            # property_count (via the reverse FK's default accessor - Property.owner/accountant
            # have no related_name) drives the confirm-before-delete warning in settings.js, so
            # staff can see how many properties would be orphaned (SET_NULL, not blocked) before
            # deleting one of these.
            'owners': Owner.objects.annotate(property_count=Count('property')).order_by('name'),
            'accountants': Accountant.objects.annotate(property_count=Count('property')).order_by('company'),
            'management_companies': self._management_companies_with_property_count(),
            'check_in_method_choices': ManagementCompany.CheckInMethod.choices,
            'owner_boolean_fields': OWNER_BOOLEAN_FIELDS,
        }

    def _management_companies_with_property_count(self):
        # A company's booking_properties/cleaning_properties can genuinely overlap (the common
        # case: one company both books and cleans the same property) - counting each relation
        # separately and summing them (Count('booked_properties') + Count('cleaned_properties'))
        # would double-count that property. Computed in Python per company (cheap at this app's
        # scale) rather than one annotated queryset, since a single Count() can't express "distinct
        # properties across either of two separate reverse FKs" without a subquery.
        companies = list(ManagementCompany.objects.order_by('name').prefetch_related('washing_materials'))
        for company in companies:
            company.property_count = Property.objects.filter(
                Q(booking_company=company) | Q(cleaning_company=company)
            ).distinct().count()
        return companies

    def _email_templates_with_placeholder_hints(self):
        # placeholder_hint is documentation only, read straight from communications/registry.py's
        # PLACEHOLDER_KEYS (a static list kept in sync with what each slug's context() callable
        # actually produces - see that dict's own comment for why it can't be introspected live)
        # so staff editing a template's subject/body know exactly which {{ }} placeholders exist.
        templates = list(EmailTemplate.objects.order_by('name'))
        for template in templates:
            template.placeholder_hint = ', '.join(f'{{{{ {key} }}}}' for key in PLACEHOLDER_KEYS.get(template.slug, ()))
        return templates

    # --- Bookings ---

    def _update_booking_settings(self, request):
        settings = BookingSettings.load()
        post = request.POST
        for field in (
            'admin_fee_percent', 'deposit_percent_at_booking', 'security_deposit_amount',
            'gbp_conversion_rate',
        ):
            value = _parsed_decimal(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        for field in (
            'balance_due_days_before_arrival', 'balance_reminder_days_before_arrival',
            'extras_edit_cutoff_days_before_arrival', 'monthly_discount_min_nights',
            'revolut_hold_minutes', 'revolut_hold_extension_minutes',
            'payment_clearing_business_days', 'adult_min_age', 'child_min_age',
        ):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        try:
            settings.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        settings.save()
        messages.success(request, "Booking settings updated.")

    def _update_checkin_settings(self, request):
        settings = CheckinSettings.load()
        post = request.POST
        for field in ('faro_buffer_minutes', 'lisbon_buffer_minutes', 'transit_buffer_minutes'):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        for field in ('key_box_prep_time', 'welcome_visit_time'):
            value = _parsed_time(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        try:
            settings.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        settings.save()
        messages.success(request, "Check-in settings updated.")

    def _add_booking_condition(self, request):
        post = request.POST
        text = post.get('text', '').strip()
        if not text:
            messages.error(request, "A booking condition needs some text.")
            return
        condition = BookingCondition(text=text, order=_parsed_int(post.get('order')) or 0)
        condition.save()
        messages.success(request, "Booking condition added.")

    def _update_booking_condition(self, request):
        condition = BookingCondition.objects.filter(pk=request.POST.get('condition_id')).first()
        if condition is None:
            messages.error(request, "That booking condition no longer exists.")
            return
        post = request.POST
        text = post.get('text', '').strip()
        if not text:
            messages.error(request, "A booking condition needs some text.")
            return
        condition.text = text
        condition.order = _parsed_int(post.get('order')) or 0
        condition.save()
        messages.success(request, "Booking condition updated.")

    def _delete_booking_condition(self, request):
        BookingCondition.objects.filter(pk=request.POST.get('condition_id')).delete()
        messages.success(request, "Booking condition deleted.")

    def _add_faq(self, request):
        post = request.POST
        question = post.get('question', '').strip()
        answer = post.get('answer', '').strip()
        if not question or not answer:
            messages.error(request, "A FAQ needs both a question and an answer.")
            return
        location_id = post.get('location', '').strip()
        faq = FAQ(
            question=question, answer=answer, location_id=location_id or None,
            order=_parsed_int(post.get('order')) or 0,
        )
        faq.save()
        messages.success(request, "FAQ added.")

    def _update_faq(self, request):
        faq = FAQ.objects.filter(pk=request.POST.get('faq_id')).first()
        if faq is None:
            messages.error(request, "That FAQ no longer exists.")
            return
        post = request.POST
        question = post.get('question', '').strip()
        answer = post.get('answer', '').strip()
        if not question or not answer:
            messages.error(request, "A FAQ needs both a question and an answer.")
            return
        location_id = post.get('location', '').strip()
        faq.question = question
        faq.answer = answer
        faq.location_id = location_id or None
        faq.order = _parsed_int(post.get('order')) or 0
        faq.save()
        messages.success(request, "FAQ updated.")

    def _delete_faq(self, request):
        FAQ.objects.filter(pk=request.POST.get('faq_id')).delete()
        messages.success(request, "FAQ deleted.")

    def _add_platform(self, request):
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "A platform needs a name.")
            return
        platform = Platform(
            name=name, take_security_deposits=request.POST.get('take_security_deposits') == 'on',
            commission_percent=_parsed_decimal(request.POST.get('commission_percent')),
        )
        try:
            platform.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        platform.save()
        messages.success(request, "Platform added.")

    def _update_platform(self, request):
        platform = Platform.objects.filter(pk=request.POST.get('platform_id')).first()
        if platform is None:
            messages.error(request, "That platform no longer exists.")
            return
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "A platform needs a name.")
            return
        platform.name = name
        platform.take_security_deposits = request.POST.get('take_security_deposits') == 'on'
        platform.commission_percent = _parsed_decimal(request.POST.get('commission_percent'))
        try:
            platform.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        platform.save()
        messages.success(request, "Platform updated.")

    def _delete_platform(self, request):
        platform = Platform.objects.filter(pk=request.POST.get('platform_id')).first()
        if platform is None:
            messages.error(request, "That platform no longer exists.")
            return
        try:
            platform.delete()
        except ProtectedError:
            messages.error(
                request,
                "Can't delete this platform - it's still set on at least one property's iCal "
                "link or listing ID. Change or delete those first.",
            )
            return
        messages.success(request, "Platform deleted.")

    # --- Extras ---

    def _update_extras_settings(self, request):
        settings = ExtrasSettings.load()
        post = request.POST
        for field in (
            'airport_transfer_price_1_4_guests', 'airport_transfer_price_5_8_guests',
            'airport_transfer_night_surcharge', 'cot_price_short_stay', 'cot_price_long_stay',
            'high_chair_price_short_stay', 'high_chair_price_long_stay',
            'cot_and_high_chair_combo_discount_percent', 'welcome_pack_price', 'late_checkout_price',
            'mid_stay_clean_price_one_bedroom', 'mid_stay_clean_price_multi_bedroom',
        ):
            value = _parsed_decimal(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        minimum_nights = _parsed_int(post.get('mid_stay_clean_minimum_nights'))
        if minimum_nights is not None:
            settings.mid_stay_clean_minimum_nights = minimum_nights
        night_start = _parsed_time(post.get('airport_transfer_night_window_start'))
        if night_start is not None:
            settings.airport_transfer_night_window_start = night_start
        night_end = _parsed_time(post.get('airport_transfer_night_window_end'))
        if night_end is not None:
            settings.airport_transfer_night_window_end = night_end
        try:
            settings.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        settings.save()
        messages.success(request, "Extras settings updated.")

    def _add_welcome_pack_item(self, request):
        post = request.POST
        item = WelcomePackItem(
            name=post.get('name', '').strip(),
            category=post.get('category') or WelcomePackItem.Category.FOOD_COMMON,
            active=post.get('active') == 'on',
        )
        try:
            item.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        item.save()
        messages.success(request, "Welcome pack item added.")

    def _update_welcome_pack_item(self, request):
        item = WelcomePackItem.objects.filter(pk=request.POST.get('item_id')).first()
        if item is None:
            messages.error(request, "That welcome pack item no longer exists.")
            return
        post = request.POST
        item.name = post.get('name', '').strip()
        item.category = post.get('category') or item.category
        item.active = post.get('active') == 'on'
        try:
            item.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        item.save()
        messages.success(request, "Welcome pack item updated.")

    def _delete_welcome_pack_item(self, request):
        WelcomePackItem.objects.filter(pk=request.POST.get('item_id')).delete()
        messages.success(request, "Welcome pack item deleted.")

    def _add_request_type(self, request):
        post = request.POST
        item = RequestType(
            name=post.get('name', '').strip(),
            description=post.get('description', '').strip(),
            default_price=_parsed_decimal(post.get('default_price')) or 0,
            active=post.get('active') == 'on',
        )
        try:
            item.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        item.save()
        messages.success(request, "Request type added.")

    def _update_request_type(self, request):
        item = RequestType.objects.filter(pk=request.POST.get('item_id')).first()
        if item is None:
            messages.error(request, "That request type no longer exists.")
            return
        post = request.POST
        item.name = post.get('name', '').strip()
        item.description = post.get('description', '').strip()
        item.default_price = _parsed_decimal(post.get('default_price')) or 0
        item.active = post.get('active') == 'on'
        try:
            item.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        item.save()
        messages.success(request, "Request type updated.")

    def _delete_request_type(self, request):
        RequestType.objects.filter(pk=request.POST.get('item_id')).delete()
        messages.success(request, "Request type deleted.")

    # --- Staff ---

    def _add_staff_user(self, request):
        post = request.POST
        username = post.get('username', '').strip()
        password = post.get('password', '')
        if not username or not password:
            messages.error(request, "A new staff account needs both a username and a password.")
            return
        if User.objects.filter(username=username).exists():
            messages.error(request, f'A user named "{username}" already exists.')
            return
        user = User.objects.create_user(
            username=username,
            email=post.get('email', '').strip(),
            password=password,
        )
        user.is_staff = True
        user.is_superuser = post.get('is_superuser') == 'on'
        user.save()
        messages.success(request, f'Staff account "{username}" created.')

    def _update_staff_user(self, request):
        user = User.objects.filter(pk=request.POST.get('user_id')).first()
        if user is None:
            messages.error(request, "That user no longer exists.")
            return
        post = request.POST
        user.is_staff = post.get('is_staff') == 'on'
        user.is_superuser = post.get('is_superuser') == 'on'
        user.is_active = post.get('is_active') == 'on'
        user.save()
        StaffProfile.objects.update_or_create(
            user=user, defaults={'role_id': post.get('role') or None}
        )
        messages.success(request, f'"{user.username}" updated.')

    # --- Roles ---

    def _add_role(self, request):
        post = request.POST
        name = post.get('name', '').strip()
        if not name:
            messages.error(request, "A role needs a name.")
            return
        role = StaffRole(name=name)
        for field_name, _label in STAFF_PAGE_PERMISSION_FIELDS:
            setattr(role, field_name, post.get(field_name) == 'on')
        role.is_cleaning_staff = post.get('is_cleaning_staff') == 'on'
        role.can_send_emails = post.get('can_send_emails') == 'on'
        try:
            role.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        role.save()
        messages.success(request, f'Role "{role.name}" created.')

    def _update_role(self, request):
        role = StaffRole.objects.filter(pk=request.POST.get('role_id')).first()
        if role is None:
            messages.error(request, "That role no longer exists.")
            return
        post = request.POST
        role.name = post.get('name', '').strip()
        for field_name, _label in STAFF_PAGE_PERMISSION_FIELDS:
            setattr(role, field_name, post.get(field_name) == 'on')
        role.is_cleaning_staff = post.get('is_cleaning_staff') == 'on'
        role.can_send_emails = post.get('can_send_emails') == 'on'
        try:
            role.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        role.save()
        messages.success(request, f'Role "{role.name}" updated.')

    def _delete_role(self, request):
        role = StaffRole.objects.filter(pk=request.POST.get('role_id')).first()
        if role is not None:
            messages.success(request, f'Role "{role.name}" deleted.')
            role.delete()

    # --- People (Owners / Accountants / Management companies) ---

    def _add_owner(self, request):
        post = request.POST
        owner = Owner(
            name=post.get('name', '').strip(),
            email=post.get('email', '').strip(),
            phone=post.get('phone', '').strip() or None,
            nif_number=post.get('nif_number', '').strip() or None,
            **{field: post.get(field) == 'on' for field, _label in OWNER_BOOLEAN_FIELDS},
        )
        try:
            owner.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        owner.save()
        messages.success(request, "Owner added.")

    def _update_owner(self, request):
        owner = Owner.objects.filter(pk=request.POST.get('owner_id')).first()
        if owner is None:
            messages.error(request, "That owner no longer exists.")
            return
        post = request.POST
        owner.name = post.get('name', '').strip()
        owner.email = post.get('email', '').strip()
        owner.phone = post.get('phone', '').strip() or None
        owner.nif_number = post.get('nif_number', '').strip() or None
        for field, _label in OWNER_BOOLEAN_FIELDS:
            setattr(owner, field, post.get(field) == 'on')
        try:
            owner.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        owner.save()
        messages.success(request, "Owner updated.")

    def _delete_owner(self, request):
        owner = Owner.objects.filter(pk=request.POST.get('owner_id')).first()
        if owner is None:
            messages.error(request, "That owner no longer exists.")
            return
        try:
            owner.delete()
        except ProtectedError:
            messages.error(
                request,
                f'"{owner}" has ownership history on record and cannot be deleted - '
                'owners with any ownership history must be kept, even once they no longer own anything.'
            )
            return
        messages.success(request, "Owner deleted.")

    def _add_accountant(self, request):
        post = request.POST
        accountant = Accountant(
            company=post.get('company', '').strip(),
            name=post.get('name', '').strip(),
            email=post.get('email', '').strip(),
            phone=post.get('phone', '').strip(),
        )
        try:
            accountant.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        accountant.save()
        messages.success(request, "Accountant added.")

    def _update_accountant(self, request):
        accountant = Accountant.objects.filter(pk=request.POST.get('accountant_id')).first()
        if accountant is None:
            messages.error(request, "That accountant no longer exists.")
            return
        post = request.POST
        accountant.company = post.get('company', '').strip()
        accountant.name = post.get('name', '').strip()
        accountant.email = post.get('email', '').strip()
        accountant.phone = post.get('phone', '').strip()
        try:
            accountant.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        accountant.save()
        messages.success(request, "Accountant updated.")

    def _delete_accountant(self, request):
        Accountant.objects.filter(pk=request.POST.get('accountant_id')).delete()
        messages.success(request, "Accountant deleted.")

    # Every one of the 5 contact roles (each name/email/phone) is genuinely optional - a company
    # acting on a narrow scope (e.g. cleaning only) may only ever fill in one. The Settings table
    # only shows the head contact inline, with the other four tucked behind each row's "More
    # fields" toggle (see staff-management-company-details-row in settings.html / settings.js).
    MANAGEMENT_COMPANY_CONTACT_ROLES = ('head', 'maintenance', 'liaison', 'cleaning', 'finance')

    def _parsed_management_company_operational_fields(self, post):
        """towels_per_guest/includes_beach_towels/linen_provided/standard_meet_and_greet_fee/
        check_in_method/self_check_in_after/freshen_after_days - all genuinely optional per-company
        operational defaults (see ManagementCompany's own docstring), so anything missing or
        unrecognised parses to None rather than a default value - there's no sensible fallback for
        "this company didn't say" that isn't itself a real, distinct answer (e.g. 0 towels, or
        check-in always in person). Washing materials are a separate list, handled by
        _add_washing_material/_delete_washing_material instead - not part of this dict."""
        beach_towels_raw = post.get('includes_beach_towels', '')
        linen_raw = post.get('linen_provided', '')
        check_in_method = post.get('check_in_method', '').strip()
        return {
            'towels_per_guest': _parsed_int(post.get('towels_per_guest')),
            'includes_beach_towels': {'true': True, 'false': False}.get(beach_towels_raw),
            'linen_provided': {'true': True, 'false': False}.get(linen_raw),
            'standard_meet_and_greet_fee': _parsed_decimal(post.get('standard_meet_and_greet_fee')),
            'check_in_method': check_in_method if check_in_method in ManagementCompany.CheckInMethod.values else None,
            'self_check_in_after': _parsed_time(post.get('self_check_in_after')),
            'freshen_after_days': _parsed_int(post.get('freshen_after_days')),
        }

    def _parsed_standard_checkin_checkout(self, post):
        """standard_checkin_time/standard_checkout_time always have a real value (unlike the other
        operational fields above, which use None for "not specified") - a blank submission means
        "leave it as it is" on an update, or "use the model's own 14:00/10:00 default" on an add,
        not "set it to nothing" (the field itself isn't nullable). Only includes a key when the
        post actually parsed to a real time, so callers can rely on an absent key meaning
        "unchanged"."""
        result = {}
        checkin = _parsed_time(post.get('standard_checkin_time'))
        checkout = _parsed_time(post.get('standard_checkout_time'))
        if checkin is not None:
            result['standard_checkin_time'] = checkin
        if checkout is not None:
            result['standard_checkout_time'] = checkout
        return result

    def _parsed_calendar_visibility_fields(self, post):
        """cleans_on_calendar/checkins_on_calendar/finances_managed_internally/
        bookable_on_website - plain checkboxes, unlike every field above: the whole
        management-company form always submits together (see management-company-form-{{
        company.pk }} in settings.html), so an absent key unambiguously means "left unchecked" for
        this submission, not "not specified" - no None/leave-unchanged case to thread through the
        way the two fields above need."""
        return {
            'cleans_on_calendar': post.get('cleans_on_calendar') == 'on',
            'checkins_on_calendar': post.get('checkins_on_calendar') == 'on',
            'finances_managed_internally': post.get('finances_managed_internally') == 'on',
            'bookable_on_website': post.get('bookable_on_website') == 'on',
        }

    def _add_management_company(self, request):
        post = request.POST
        fields = {'name': post.get('name', '').strip()}
        for role in self.MANAGEMENT_COMPANY_CONTACT_ROLES:
            fields[f'{role}_name'] = post.get(f'{role}_name', '').strip()
            fields[f'{role}_email'] = post.get(f'{role}_email', '').strip()
            fields[f'{role}_phone'] = post.get(f'{role}_phone', '').strip()
        fields.update(self._parsed_management_company_operational_fields(post))
        fields.update(self._parsed_standard_checkin_checkout(post))
        fields.update(self._parsed_calendar_visibility_fields(post))
        company = ManagementCompany(**fields)
        try:
            company.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        company.save()
        messages.success(request, "Management company added.")

    def _update_management_company(self, request):
        company = ManagementCompany.objects.filter(pk=request.POST.get('management_company_id')).first()
        if company is None:
            messages.error(request, "That management company no longer exists.")
            return
        post = request.POST
        was_finances_managed_internally = company.finances_managed_internally
        company.name = post.get('name', '').strip()
        for role in self.MANAGEMENT_COMPANY_CONTACT_ROLES:
            setattr(company, f'{role}_name', post.get(f'{role}_name', '').strip())
            setattr(company, f'{role}_email', post.get(f'{role}_email', '').strip())
            setattr(company, f'{role}_phone', post.get(f'{role}_phone', '').strip())
        for field, value in self._parsed_management_company_operational_fields(post).items():
            setattr(company, field, value)
        for field, value in self._parsed_standard_checkin_checkout(post).items():
            setattr(company, field, value)
        for field, value in self._parsed_calendar_visibility_fields(post).items():
            setattr(company, field, value)
        try:
            company.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        company.save()

        if company.finances_managed_internally and not was_finances_managed_internally:
            # Newly opted in - backfill Memo rows for turnover cleans already scheduled from today
            # onward (see finance/services.py::backfill_memos_for_company), so Finance doesn't stay
            # empty for cleans that were set up before this was switched on.
            synced = backfill_memos_for_company(company)
            if synced:
                messages.success(request, f"Management company updated. Synced {synced} upcoming turnover clean(s) to Finance.")
                return

        messages.success(request, "Management company updated.")

    def _delete_management_company(self, request):
        company = ManagementCompany.objects.filter(pk=request.POST.get('management_company_id')).first()
        if company is None:
            messages.error(request, "That management company no longer exists.")
            return
        company.delete()
        messages.success(request, "Management company deleted.")

    def _add_washing_material(self, request):
        post = request.POST
        company = ManagementCompany.objects.filter(pk=post.get('management_company_id')).first()
        if company is None:
            messages.error(request, "That management company no longer exists.")
            return
        title = post.get('title', '').strip()
        if not title:
            messages.error(request, "A washing material needs a title.")
            return
        quantity = _parsed_int(post.get('quantity')) or 1
        WashingMaterial.objects.create(company=company, title=title, quantity=quantity)
        messages.success(request, "Washing material added.")

    def _delete_washing_material(self, request):
        material = WashingMaterial.objects.filter(pk=request.POST.get('washing_material_id')).first()
        if material is None:
            messages.error(request, "That washing material no longer exists.")
            return
        material.delete()
        messages.success(request, "Washing material removed.")

    # --- Payments ---

    def _update_payment_settings(self, request):
        settings = PaymentSettings.load()
        post = request.POST
        for field in (
            'high_season_commission_percent', 'low_season_commission_percent',
            'klt_commission_share_percent', 'vat_rate_percent',
            'cleaning_surcharge_one_bedroom', 'cleaning_surcharge_multi_bedroom',
            'cleaning_high_occupancy_surcharge', 'meet_greet_fee', 'extra_bed_fee',
        ):
            value = _parsed_decimal(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        for field in ('high_season_start_month', 'high_season_end_month', 'regular_payout_days_after_arrival'):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(settings, field, value)
        for field in ('charge_vat_on_low_season_direct_commission', 'charge_vat_on_low_season_platform_commission'):
            setattr(settings, field, post.get(field) == 'on')
        try:
            settings.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        settings.save()
        messages.success(request, "Payment settings updated.")

    # --- Emails ---

    def _update_email_template(self, request):
        # No add/delete here, deliberately - the set of valid slugs is fixed in code
        # (communications/registry.py::EMAIL_TYPES), staff can only edit an existing row's copy/
        # timing/active state, not invent a new slug with no matching trigger/eligibility logic.
        post = request.POST
        template = EmailTemplate.objects.filter(pk=post.get('template_id')).first()
        if template is None:
            messages.error(request, "That email template no longer exists.")
            return
        subject = post.get('subject', '').strip()
        body = post.get('body', '').strip()
        if not subject or not body:
            messages.error(request, "An email template needs both a subject and a body.")
            return
        offset_days_raw = post.get('offset_days', '').strip()
        try:
            offset_days = int(offset_days_raw) if offset_days_raw else None
        except ValueError:
            offset_days = None
        if offset_days is None:
            messages.error(request, "Days offset must be a whole number (negative for before the date, positive for after).")
            return
        template.subject = subject
        template.body = body
        template.offset_days = offset_days
        template.active = post.get('active') == 'on'
        template.updated_by = request.user
        try:
            template.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        template.save()
        messages.success(request, "Email template updated.")


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffPropertyDetailView(View):
    """PIMS-style "Change Property" page - one panel per related model (info, specification,
    amenities, SEF details), the rate card (each row directly editable in place - via the input
    `form=` attribute, since a <form> can't legally wrap several <td>s in one row - stacked above
    a separate "Add price line" card in the same tab for creating new ones), an add/delete-only
    iCal import links list (matching the append-then-delete pattern used for Deductions/Owner
    Payments elsewhere in this app), a photo gallery, and an Ownership tab (read-only handover
    history plus a "Record handover" form - see PropertyOwnership.record_handover() in
    properties/models.py). Every OneToOne side-model (PropertySpec/Amenity/SEFDetail) is
    get_or_create'd on load - all three have defaults for every field, so a freshly created
    Property with none of them yet still renders a fully fillable form instead of an empty-state
    message."""
    template_name = 'staff/property_detail.html'

    def _get_property(self, pk):
        property = Property.objects.filter(pk=pk).first()
        if property is None:
            raise Http404("No property found.")
        return property

    # Maps each POST action to the sidebar tab it belongs to, so saving/deleting something in a
    # given panel redirects back to that same panel (via ?panel=) instead of dropping the staffer
    # back on "Main info" every time - see property_detail.html/.css for the tabs themselves.
    # Property info and Property specification share the 'main' tab (shown side by side there on
    # wide viewports), so both actions map to the same slug.
    ACTION_PANELS = {
        'update_property_info': 'main',
        'update_specification': 'main',
        'update_amenities': 'amenities',
        'update_sef': 'sef',
        'update_price': 'rates',
        'add_price': 'rates',
        'delete_price': 'rates',
        'add_ical_link': 'ical',
        'update_ical_link': 'ical',
        'delete_ical_link': 'ical',
        'add_image': 'photos',
        'delete_image': 'photos',
        'record_handover': 'ownership',
    }
    PANELS = ('main', 'amenities', 'sef', 'rates', 'ical', 'photos', 'ownership')

    def get(self, request, pk, *args, **kwargs):
        property = self._get_property(pk)
        panel = request.GET.get('panel', '')
        active_panel = panel if panel in self.PANELS else 'main'
        show_historic = request.GET.get('show_historic') == '1'
        context = self._context(property, active_panel, show_historic)
        context['export_url'] = request.build_absolute_uri(
            reverse('properties:calendar_export', kwargs={'token': property.ical_export_token})
        )
        conflict = request.GET.get('conflict', '')
        context['conflict_price_ids'] = {int(pk) for pk in conflict.split(',') if pk.isdigit()}
        return render(request, self.template_name, context)

    def post(self, request, pk, *args, **kwargs):
        property = self._get_property(pk)
        action = request.POST.get('action')
        handler = {
            'update_property_info': self._update_property_info,
            'update_specification': self._update_specification,
            'update_amenities': self._update_amenities,
            'update_sef': self._update_sef,
            'update_price': self._update_price,
            'add_price': self._add_price,
            'delete_price': self._delete_price,
            'add_ical_link': self._add_ical_link,
            'update_ical_link': self._update_ical_link,
            'delete_ical_link': self._delete_ical_link,
            'add_image': self._add_image,
            'delete_image': self._delete_image,
            'record_handover': self._record_handover,
        }.get(action)
        extra_query = handler(request, property) if handler is not None else None
        panel = self.ACTION_PANELS.get(action, 'main')
        url = f"{reverse('staff:property_detail', kwargs={'pk': property.pk})}?panel={panel}"
        if request.POST.get('show_historic') == '1':
            url += '&show_historic=1'
        if extra_query:
            url += f"&{extra_query}"
        return redirect(url)

    def _context(self, property, active_panel, show_historic=False):
        specs, _ = PropertySpec.objects.get_or_create(property=property)
        amenities, _ = Amenity.objects.get_or_create(property=property)
        sef_details, _ = SEFDetail.objects.get_or_create(property=property)
        existing_listing_ids = {p.platform_id: p.listing_id for p in property.platform_ids.all()}
        all_prices = property.prices.order_by('start_date')
        today = timezone.localdate()
        historic_price_count = all_prices.filter(end_date__lt=today).count()
        prices = all_prices if show_historic else all_prices.filter(end_date__gte=today)
        context = {
            'property': property,
            'active_panel': active_panel,
            'specs': specs,
            'amenities': amenities,
            'amenity_fields': AMENITY_BOOLEAN_FIELDS,
            'sef_details': sef_details,
            'ical_links': property.ical_links.all(),
            'platform_id_rows': [
                (platform, existing_listing_ids.get(platform.pk, ''))
                for platform in Platform.objects.all()
            ],
            'images': property.images.all(),
            'prices': prices,
            'show_historic': show_historic,
            'historic_price_count': historic_price_count,
            'ownership_history': property.ownership_history.all(),
        }
        context.update(_property_form_context())
        return context

    def _update_property_info(self, request, property):
        post = request.POST
        property.title = post.get('title', property.title).strip() or property.title
        property.short_title = post.get('short_title', property.short_title).strip() or property.short_title
        property.door_number = post.get('door_number', '').strip()
        # No owner field here deliberately - Property.owner only ever changes via
        # PropertyOwnership.record_handover() (see the Ownership tab / _record_handover below),
        # so ownership history can never silently drift out of sync with it.
        property.location_id = post.get('location') or None
        property.accountant_id = post.get('accountant') or None
        property.al_number = _parsed_int(post.get('al_number'))
        property.booking_company_id = post.get('booking_company') or None
        property.cleaning_company_id = post.get('cleaning_company') or None
        fee = _parsed_decimal(post.get('standard_cleaning_fee'))
        if fee is not None:
            property.standard_cleaning_fee = fee
        try:
            property.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        property.save()
        _save_platform_ids(request, property)
        messages.success(request, "Property info updated.")

    def _update_specification(self, request, property):
        specs, _ = PropertySpec.objects.get_or_create(property=property)
        post = request.POST
        specs.is_sea_view = post.get('is_sea_view') == 'on'
        specs.is_pool_view = post.get('is_pool_view') == 'on'
        specs.is_upper_floor = post.get('is_upper_floor') == 'on'
        specs.is_beachfront = post.get('is_beachfront') == 'on'
        specs.children_allowed = post.get('children_allowed') == 'on'
        specs.pets_allowed = post.get('pets_allowed') == 'on'
        for field in ('bedrooms', 'bathrooms', 'half_bathrooms', 'square_metres', 'minimum_nights',
                      'max_adults', 'max_guests'):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(specs, field, value)
        specs.description = post.get('description', '').strip()
        specs.save()
        messages.success(request, "Property specification updated.")

    def _update_amenities(self, request, property):
        amenities, _ = Amenity.objects.get_or_create(property=property)
        post = request.POST
        for field, _label in AMENITY_BOOLEAN_FIELDS:
            setattr(amenities, field, post.get(field) == 'on')
        for field in (
            'double_beds', 'single_beds',
            'hand_towels_per_guest', 'bath_towels_per_guest', 'beach_towels_per_guest',
        ):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(amenities, field, value)
        bed_sizes = post.get('bed_sizes', '').strip()
        if bed_sizes:
            amenities.bed_sizes = bed_sizes
        amenities.save()
        messages.success(request, "Amenities updated.")

    def _update_sef(self, request, property):
        sef_details, _ = SEFDetail.objects.get_or_create(property=property)
        post = request.POST
        sef_details.unidade_hoteleira = post.get('unidade_hoteleira', '').strip()
        sef_details.estabelecimento = post.get('estabelecimento', '').strip()
        sef_details.chave_de_autenticacao = post.get('chave_de_autenticacao', '').strip()
        sef_details.save()
        messages.success(request, "SEF details updated.")

    def _add_price(self, request, property):
        post = request.POST
        start_date = _parsed_date(post.get('start_date'))
        end_date = _parsed_date(post.get('end_date'))
        if not start_date or not end_date:
            messages.error(request, "A price line needs both a start and end date.")
            return
        price = Price(
            property=property,
            start_date=start_date,
            end_date=end_date,
            rate=_parsed_decimal(post.get('rate')) or 0,
            weekly_discount_percent=_parsed_decimal(post.get('weekly_discount_percent')) or 0,
            last_minute_discount_percent=_parsed_decimal(post.get('last_minute_discount_percent')) or 0,
            last_minute_discount_days=_parsed_int(post.get('last_minute_discount_days')) or 7,
            monthly_discount_percent=_parsed_decimal(post.get('monthly_discount_percent')) or 0,
            extra_adult_rate=_parsed_decimal(post.get('extra_adult_rate')) or 0,
            extra_child_rate=_parsed_decimal(post.get('extra_child_rate')) or 0,
        )
        try:
            price.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return _conflict_query(Price.overlapping(property.pk, start_date, end_date))
        price.save()
        messages.success(request, "Price line added.")

    def _update_price(self, request, property):
        price = Price.objects.filter(pk=request.POST.get('price_id'), property=property).first()
        if price is None:
            messages.error(request, "That price line no longer exists.")
            return
        post = request.POST
        start_date = _parsed_date(post.get('start_date'))
        end_date = _parsed_date(post.get('end_date'))
        if not start_date or not end_date:
            messages.error(request, "A price line needs both a start and end date.")
            return
        price.start_date = start_date
        price.end_date = end_date
        price.rate = _parsed_decimal(post.get('rate')) or 0
        price.weekly_discount_percent = _parsed_decimal(post.get('weekly_discount_percent')) or 0
        price.monthly_discount_percent = _parsed_decimal(post.get('monthly_discount_percent')) or 0
        price.last_minute_discount_percent = _parsed_decimal(post.get('last_minute_discount_percent')) or 0
        price.last_minute_discount_days = _parsed_int(post.get('last_minute_discount_days')) or 7
        price.extra_adult_rate = _parsed_decimal(post.get('extra_adult_rate')) or 0
        price.extra_child_rate = _parsed_decimal(post.get('extra_child_rate')) or 0
        try:
            # Price.clean() excludes price.pk from its own overlap check, so editing dates that
            # still just cover this same row's existing slot won't falsely flag against itself.
            price.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return _conflict_query(Price.overlapping(property.pk, start_date, end_date, exclude_pk=price.pk))
        price.save()
        messages.success(request, "Price line updated.")

    def _delete_price(self, request, property):
        Price.objects.filter(pk=request.POST.get('price_id'), property=property).delete()
        messages.success(request, "Price line deleted.")

    def _add_ical_link(self, request, property):
        post = request.POST
        platform_id = post.get('platform', '').strip()
        platform = Platform.objects.filter(pk=platform_id).first() if platform_id else None
        url = post.get('ical_url', '').strip()
        if not url:
            messages.error(request, "An iCal link needs a URL.")
            return
        iCalLink.objects.create(
            property=property, platform=platform, ical_url=url,
            is_owner_link=post.get('is_owner_link') == 'on',
            exclude_summary_contains=post.get('exclude_summary_contains', '').strip(),
        )
        messages.success(request, "iCal link added.")

    def _update_ical_link(self, request, property):
        link = iCalLink.objects.filter(pk=request.POST.get('link_id'), property=property).first()
        if link is None:
            return
        url = request.POST.get('ical_url', '').strip()
        if not url:
            messages.error(request, "An iCal link needs a URL.")
            return
        link.ical_url = url
        link.is_owner_link = request.POST.get('is_owner_link') == 'on'
        link.exclude_summary_contains = request.POST.get('exclude_summary_contains', '').strip()
        link.save(update_fields=['ical_url', 'is_owner_link', 'exclude_summary_contains'])
        messages.success(request, "iCal link updated.")

    def _delete_ical_link(self, request, property):
        iCalLink.objects.filter(pk=request.POST.get('link_id'), property=property).delete()
        messages.success(request, "iCal link deleted.")

    def _add_image(self, request, property):
        image_file = request.FILES.get('image')
        if not image_file:
            messages.error(request, "Choose a file to upload.")
            return
        image = PropertyImage(
            property=property, image=image_file, caption=request.POST.get('caption', '').strip(),
        )
        try:
            image.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        image.save()
        messages.success(request, "Photo added.")

    def _delete_image(self, request, property):
        PropertyImage.objects.filter(pk=request.POST.get('image_id'), property=property).delete()
        messages.success(request, "Photo deleted.")

    def _record_handover(self, request, property):
        post = request.POST
        new_owner_id = post.get('new_owner', '').strip()
        new_owner = Owner.objects.filter(pk=new_owner_id).first() if new_owner_id.isdigit() else None
        effective_date = _parsed_date(post.get('effective_date'))
        if new_owner is None or effective_date is None:
            messages.error(request, "Choose a new owner and an effective date.")
            return
        try:
            PropertyOwnership.record_handover(property, new_owner, effective_date)
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        messages.success(request, f"Ownership transferred to {new_owner} effective {effective_date}.")


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffPropertyPlatformRatesView(View):
    """Rate Card "Platform rates" popup - opened via the same named-window pattern as the iCal
    sync popup below (see property_detail.js/property_detail.html's data-popup-link), except a
    plain GET link rather than a POST form since this has no side effects at all. Grosses up this
    property's current/future Direct rates by every Platform with a commission_percent set (see
    properties/utils.py::gross_up_for_commission and Platform's own docstring for the caveats) -
    purely informational, telling staff what to type into a platform's own calendar when updating
    it by hand. Historic rows are left out for the same reason the Rate card panel hides them by
    default - a platform's live calendar only cares about current/future pricing."""
    template_name = 'staff/property_platform_rates.html'

    def get(self, request, pk, *args, **kwargs):
        property = Property.objects.filter(pk=pk).first()
        if property is None:
            raise Http404("No property found.")
        platforms = Platform.objects.filter(commission_percent__isnull=False).order_by('name')
        prices = property.prices.filter(end_date__gte=timezone.localdate()).order_by('start_date')
        rows = [
            {
                'price': price,
                'platform_rates': [
                    gross_up_for_commission(price.rate, platform.commission_percent)
                    for platform in platforms
                ],
            }
            for price in prices
        ]
        context = {'property': property, 'platforms': platforms, 'rows': rows}
        return render(request, self.template_name, context)


@method_decorator(staff_page_required('can_view_properties'), name='dispatch')
class StaffIcalSyncView(View):
    """Staff "Sync now" popup for one iCalLink - fetches the feed live and runs it through the
    same sync_ical_link() logic bookings/management/commands/sync_ical_feeds.py uses on its own
    schedule, then renders a small standalone results page listing every booking found in the feed
    and what happened to it - mirroring PIMS' own manual iCal-sync popup, which the "Sync now"
    button in property_detail.html's iCal panel opens into a real popup window (see
    property_detail.js). A feed with zero events is reported as healthy/connected rather than an
    empty table, and any overlap conflict is called out explicitly - both per Thomas's ask. POST
    only (this has real write side-effects - creating/updating/cancelling Bookings - so it isn't a
    plain link the way the read-only iCal export is)."""
    template_name = 'staff/ical_sync_result.html'

    def post(self, request, pk, link_id, *args, **kwargs):
        property = Property.objects.filter(pk=pk).first()
        if property is None:
            raise Http404("No property found.")
        link = iCalLink.objects.filter(pk=link_id, property=property).first()
        if link is None:
            raise Http404("No iCal link found.")

        context = {'property': property, 'link': link, 'synced_at': timezone.now()}

        if not link.ical_url:
            context['fetch_error'] = "This link has no URL configured."
        elif link.platform_id is None:
            context['fetch_error'] = "This link has no platform set."
        else:
            label = f"{property} ({link.platform.name})"
            try:
                response = requests.get(link.ical_url, timeout=30)
                response.raise_for_status()
            except requests.RequestException as error:
                logerror(f"Could not fetch iCal feed for {label}: {error}")
                context['fetch_error'] = f"Could not connect to this feed: {error}"
            else:
                try:
                    context['summary'] = sync_ical_link(link, response.text)
                except Exception as error:
                    logerror(f"Could not parse/sync iCal feed for {label}: {error}")
                    context['fetch_error'] = f"Could not read this feed: {error}"

        return render(request, self.template_name, context)


# Fallback values only used the first time a Location's Rules row is created (LocationRules'
# four TimeFields have no model-level default, unlike PropertySpec/Amenity/SEFDetail's fields -
# see StaffLocationDetailView._context) - sensible starting hours for a holiday rental, not a
# real policy statement; staff are expected to override them for each location's actual rules.
LOCATION_RULES_DEFAULTS = {
    'quiet_hours_start': time(22, 0), 'quiet_hours_end': time(8, 0),
    'pool_hours_start': time(9, 0), 'pool_hours_end': time(20, 0),
}


@method_decorator(staff_page_required('can_view_locations'), name='dispatch')
class StaffLocationDetailView(View):
    """Location's equivalent of StaffPropertyDetailView - same CSS-only radio-tab sidebar
    technique, three panels instead of seven since Location has no rate card, SEF details, iCal
    links or ownership concept of its own: Main info (Location fields + LocationSpec's boolean
    flags side by side, same two-card layout as Property's own Main info tab), Rules
    (LocationRules - quiet/pool hours plus free-text pool/condominium rules), and Photos
    (LocationImage gallery, identical add/delete pattern to PropertyImage's)."""
    template_name = 'staff/location_detail.html'

    def _get_location(self, pk):
        location = Location.objects.filter(pk=pk).first()
        if location is None:
            raise Http404("No location found.")
        return location

    ACTION_PANELS = {
        'update_location_info': 'main',
        'update_specification': 'main',
        'update_rules': 'rules',
        'add_image': 'photos',
        'delete_image': 'photos',
    }
    PANELS = ('main', 'rules', 'photos')

    def get(self, request, pk, *args, **kwargs):
        location = self._get_location(pk)
        panel = request.GET.get('panel', '')
        active_panel = panel if panel in self.PANELS else 'main'
        return render(request, self.template_name, self._context(location, active_panel))

    def post(self, request, pk, *args, **kwargs):
        location = self._get_location(pk)
        action = request.POST.get('action')
        handler = {
            'update_location_info': self._update_location_info,
            'update_specification': self._update_specification,
            'update_rules': self._update_rules,
            'add_image': self._add_image,
            'delete_image': self._delete_image,
        }.get(action)
        if handler is not None:
            handler(request, location)
        panel = self.ACTION_PANELS.get(action, 'main')
        return redirect(f"{reverse('staff:location_detail', kwargs={'pk': location.pk})}?panel={panel}")

    def _context(self, location, active_panel):
        specs, _ = LocationSpec.objects.get_or_create(location=location)
        rules, _ = LocationRules.objects.get_or_create(location=location, defaults=LOCATION_RULES_DEFAULTS)
        return {
            'location': location,
            'active_panel': active_panel,
            'specs': specs,
            'spec_fields': LOCATION_SPEC_BOOLEAN_FIELDS,
            'rules': rules,
            'images': location.images.all(),
        }

    def _update_location_info(self, request, location):
        post = request.POST
        location.title = post.get('title', location.title).strip() or location.title
        location.block = post.get('block', '').strip() or 'N/A'
        location.street = post.get('street', location.street).strip() or location.street
        location.zip_code = post.get('zip_code', location.zip_code).strip() or location.zip_code
        location.city = post.get('city', location.city).strip() or location.city
        location.coordinates = post.get('coordinates', location.coordinates).strip() or location.coordinates
        location.map_link = post.get('map_link', location.map_link).strip() or location.map_link
        location.directions = post.get('directions', '').strip()
        location.description = post.get('description', '').strip()
        location.nearest_bins = post.get('nearest_bins', '').strip()
        location.nearest_corner_shop = post.get('nearest_corner_shop', '').strip()
        location.nearest_supermarket = post.get('nearest_supermarket', '').strip()
        location.parking_details = post.get('parking_details', '').strip()
        location.color = post.get('color', '').strip()
        try:
            location.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        location.save()
        messages.success(request, "Location info updated.")

    def _update_specification(self, request, location):
        specs, _ = LocationSpec.objects.get_or_create(location=location)
        post = request.POST
        for field, _label in LOCATION_SPEC_BOOLEAN_FIELDS:
            setattr(specs, field, post.get(field) == 'on')
        specs.save()
        messages.success(request, "Location specification updated.")

    def _update_rules(self, request, location):
        rules, _ = LocationRules.objects.get_or_create(location=location, defaults=LOCATION_RULES_DEFAULTS)
        post = request.POST
        for field in ('quiet_hours_start', 'quiet_hours_end', 'pool_hours_start', 'pool_hours_end'):
            value = _parsed_time(post.get(field))
            if value is not None:
                setattr(rules, field, value)
        rules.pool_rules = post.get('pool_rules', '').strip()
        rules.condominium_rules = post.get('condominium_rules', '').strip()
        try:
            rules.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        rules.save()
        messages.success(request, "Location rules updated.")

    def _add_image(self, request, location):
        image_file = request.FILES.get('image')
        if not image_file:
            messages.error(request, "Choose a file to upload.")
            return
        image = LocationImage(
            location=location, image=image_file, caption=request.POST.get('caption', '').strip(),
        )
        try:
            image.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        image.save()
        messages.success(request, "Photo added.")

    def _delete_image(self, request, location):
        LocationImage.objects.filter(pk=request.POST.get('image_id'), location=location).delete()
        messages.success(request, "Photo deleted.")


@method_decorator(staff_page_required('can_view_bookings'), name='dispatch')
class StaffBookingDetailView(View):
    """PIMS-style single-booking admin page - see the staff booking detail page plan for the
    full panel-by-panel field mapping and what's deliberately deferred (per-line charge locking,
    a real reminders system, Guest field expansion, etc). Each panel POSTs its own `action` to
    this same view/URL, matching PIMS' own separate "Update" buttons per panel rather than one
    giant admin-style save."""
    template_name = 'staff/booking_detail.html'

    def _get_booking(self, reference):
        booking = Booking.objects.filter(reference__iexact=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        return booking

    def get(self, request, reference, *args, **kwargs):
        booking = self._get_booking(reference)
        return render(request, self.template_name, self._context(request, booking))

    def post(self, request, reference, *args, **kwargs):
        booking = self._get_booking(reference)
        handler = {
            'update_booking': self._update_booking,
            'add_deduction': self._add_deduction,
            'add_owner_payment': self._add_owner_payment,
            'delete_owner_payment': self._delete_owner_payment,
            'recalculate_payment_split': self._recalculate_payment_split,
            'add_task_note': self._add_task_note,
            'cancel_booking': self._cancel_booking,
            'uncancel_booking': self._uncancel_booking,
            'dismiss_freshen': self._dismiss_freshen_task,
            'undo_dismiss_freshen': self._undo_dismiss_freshen_task,
        }.get(request.POST.get('action'))
        if handler is not None:
            handler(request, booking)
        return redirect('staff:booking_detail', reference=booking.reference)

    def _planner_context(self, task):
        if task is None:
            return None
        min_date, max_date = cleaning_task_valid_range(task)
        return {
            'task': task, 'min_date': min_date, 'max_date': max_date,
            'assigned_ids': set(task.assigned_to.values_list('pk', flat=True)),
        }

    def _context(self, request, booking):
        charge = getattr(booking, 'charges', None)
        platform_payout = getattr(booking, 'platform_payout', None)
        balance_payment = getattr(booking, 'balance_payment', None)
        subtotal = due_total = None
        split_mismatch = False
        if charge is not None and charge.total_rental is not None and charge.admin is not None:
            subtotal = charge.total_rental + charge.admin
            due_total = (charge.due_at_booking or Decimal('0')) + (charge.due_at_balance or Decimal('0'))
            split_mismatch = abs(subtotal - due_total) > Decimal('0.01')
        return {
            'booking': booking,
            'guest': booking.guest,
            'charge': charge,
            'platform_payout': platform_payout,
            'is_platform_booking': booking.enquiry_source in env_settings.PLATFORMS,
            'payment': getattr(booking, 'payment', None),
            'balance_payment': balance_payment,
            'subtotal': subtotal,
            'due_total': due_total,
            'split_mismatch': split_mismatch,
            'arrival': getattr(booking, 'arrival', None),
            'departure': getattr(booking, 'departure', None),
            'arrival_travel_methods': TravelMethod.choices,
            'departure_travel_methods': TravelMethod.departure_choices(),
            'properties': Property.objects.all(),
            'stage_tabs': STAGE_TABS,
            'current_stage': booking_stage(booking),
            'can_cancel': booking.enquiry_status not in CLOSED_STATUSES,
            'can_uncancel': booking.enquiry_status in REVIVABLE_STATUSES,
            'extras': extras_summary(booking),
            'next_step': next_step_hint(booking, charge, balance_payment),
            'scheduled_emails': booking.scheduled_emails.select_related('template', 'sent_by').order_by('scheduled_for'),
            'can_send_emails': can_send_emails(request.user),
            'deductions': booking.deductions.all(),
            'memo': Memo.objects.filter(cleaning_task__booking=booking).prefetch_related('ad_hoc_services').first(),
            'owner_payout': compute_owner_payout(booking),
            'task_history': booking.task_history.all(),
            'currency_choices': CURRENCY_CHOICES,
            'payment_status_choices': PAYMENT_STATUS_CHOICES,
            'enquiry_status_groups': ENQUIRY_STATUS_GROUPS,
            'enquiry_statuses': ENQUIRY_STATUSES,
            'turnover_planner': self._planner_context(booking.cleaning_tasks.filter(task_type='turnover').first()),
            'mid_stay_planner': self._planner_context(booking.cleaning_tasks.filter(task_type='mid_stay').first()),
            'freshen_planner': self._planner_context(booking.cleaning_tasks.filter(task_type='freshen').first()),
            'cleaning_assignable_users': User.objects.filter(
                staff_profile__role__is_cleaning_staff=True,
            ).order_by('username'),
        }

    def _update_booking(self, request, booking):
        """One combined save for every editable field across Booking info, Guest info, Arrival,
        Departure, Rental charges, Enquiry data and Payments from guest - previously five separate
        panel forms each with their own Update button, which risked a staffer editing several
        panels, clicking Update on only one, and silently losing the rest (Thomas's exact
        complaint). The panels still look the same - each one's inputs now carry
        form="booking-detail-form" and post into the single form the Booking info panel renders
        (see booking_detail.html), landing here as one combined POST body. All parsing/validation
        happens before anything is written, then every touched model is saved together in one
        transaction so a mid-way validation failure never leaves a partial save. Deductions/Owner
        payments/Update history keep their own dedicated "+ New ..." buttons - those add a new row
        rather than edit existing fields, so there's nothing there a staffer could "forget" to
        save. Source/date of enquiry are read-only (2026-08-25) - never read from POST at all any
        more. Outcome/status is now a constrained dropdown (ENQUIRY_STATUS_GROUPS) rather than free
        text, so a value only gets accepted here if it's one of those known statuses or unchanged
        from what the booking already had - closing off the exact typo-drift risk that already
        produced one real booking stuck on 'Booking cancelled' instead of a status the rest of the
        app recognises. Arrival.self_check_in/meet_greet and Departure.clean - staff/ops-only flags
        the guest-facing flow never touches past row creation - are edited here from Booking info's
        checkboxes, not from the Arrival/Departure panels themselves (2026-08-25, per Thomas: even
        an owner booking can need a Meet & Greet or a scheduled clean, so those panels stay useful
        regardless of is_owner - only the checkboxes' one edit location consolidates).
        Departure.clean now defaults to True at booking creation (2026-08-27 - see Departure's own
        docstring, an end-of-stay clean is the normal case, not an opt-in extra), so this checkbox
        mainly matters for the rare booking that should NOT get one; Extra.mid_stay_clean, by
        contrast, is genuinely never touched by this form at all - it's guest-selected only (see
        BookingFormMixin._save_extras/_parse_mid_stay_clean)."""
        post = request.POST

        # Snapshotted before anything below saves, for _update_cleaning_tasks() to compare each
        # planner date field against - see that method's docstring for why comparing against the
        # live task instead produces a false "That date is outside this task's valid window."
        # error whenever this same submission also changes the booking's own arrival/departure
        # dates (confirmed live 2026-08-27).
        task_dates_before = dict(booking.cleaning_tasks.values_list('task_type', 'date'))

        property_id = post.get('property')
        if property_id:
            booking.property_id = property_id
        booking.is_owner = post.get('is_owner') == 'on'

        dates_before = (booking.arrival_date, booking.departure_date)
        arrival_date = _parsed_date(post.get('arrival_date'))
        departure_date = _parsed_date(post.get('departure_date'))
        if arrival_date:
            booking.arrival_date = arrival_date
        if departure_date:
            booking.departure_date = departure_date
        dates_changed = (booking.arrival_date, booking.departure_date) != dates_before

        for field in ('adults', 'children', 'babies'):
            raw = post.get(field, '').strip()
            if raw.isdigit():
                setattr(booking, field, int(raw))

        # Source/date of enquiry are read-only on this page now (Thomas: they shouldn't be
        # editable at all here) - not read from POST any more, so a stray field with this name
        # can't change them even if the form somehow still sent one.
        old_status = booking.enquiry_status
        new_status = post.get('enquiry_status', '').strip()
        if new_status and (new_status in ENQUIRY_STATUSES or new_status == old_status):
            booking.enquiry_status = new_status

        try:
            booking.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return

        arrival_method = parsed_travel_method(post.get('arrival_method'))
        departure_method = parsed_travel_method(post.get('departure_method'))
        arrival_flight_number = post.get('arrival_flight_number', '').strip()
        departure_flight_number = post.get('departure_flight_number', '').strip()
        if not valid_flight_number(arrival_method, arrival_flight_number):
            messages.error(request, f"Arrival: {FLIGHT_NUMBER_HINT}")
            return
        if not valid_flight_number(departure_method, departure_flight_number):
            messages.error(request, f"Departure: {FLIGHT_NUMBER_HINT}")
            return

        charge = getattr(booking, 'charges', None)
        charge_changed = False
        if charge is not None:
            for field in (
                'basic_rental', 'discount_total', 'extra_guest_total', 'admin', 'security',
                'due_at_booking', 'due_at_balance',
            ):
                raw = post.get(field, '').strip()
                if not raw:
                    # Discount/extra-guest are often legitimately absent, so clearing the input
                    # should actually clear the stored value - unlike the other fields here, which
                    # stay untouched on blank as a safety net against an accidental empty submit.
                    if field in ('discount_total', 'extra_guest_total') and getattr(charge, field) is not None:
                        charge_changed = True
                        setattr(charge, field, None)
                    continue
                value = _parsed_decimal(raw)
                if value is None:
                    messages.error(request, f"'{raw}' isn't a valid amount for {field.replace('_', ' ')}.")
                    return
                if value != getattr(charge, field):
                    charge_changed = True
                setattr(charge, field, value)
            currency = post.get('currency')
            if currency in dict(CURRENCY_CHOICES):
                if currency != charge.currency:
                    charge_changed = True
                charge.currency = currency

        # Platform Payout replaces Rental Charges on this page for a platform booking (see
        # booking_detail.html) - gross/commission/payout is structurally different money to a
        # Charge's rental/admin split (see PlatformPayout's own docstring), and no import or sync
        # path creates this row, so it's get-or-create'd here rather than requiring one to already
        # exist like `charge` above. Only actually saved if a field was touched (platform_payout_
        # changed), so a platform booking nobody's entered payout figures for yet doesn't grow an
        # empty row on every unrelated booking edit.
        is_platform_booking = booking.enquiry_source in env_settings.PLATFORMS
        platform_payout = getattr(booking, 'platform_payout', None)
        platform_payout_changed = False
        if is_platform_booking:
            if platform_payout is None:
                platform_payout = PlatformPayout(booking=booking)
            for field in ('gross_amount', 'platform_commission', 'payout_amount'):
                raw = post.get(field, '').strip()
                if not raw:
                    if getattr(platform_payout, field) is not None:
                        platform_payout_changed = True
                        setattr(platform_payout, field, None)
                    continue
                value = _parsed_decimal(raw)
                if value is None:
                    messages.error(request, f"'{raw}' isn't a valid amount for {field.replace('_', ' ')}.")
                    return
                if value != getattr(platform_payout, field):
                    platform_payout_changed = True
                setattr(platform_payout, field, value)
            payout_currency = post.get('payout_currency')
            if payout_currency in dict(CURRENCY_CHOICES):
                if payout_currency != platform_payout.payout_currency:
                    platform_payout_changed = True
                platform_payout.payout_currency = payout_currency
            payout_date = _parsed_date(post.get('payout_date'))
            if payout_date != platform_payout.payout_date:
                platform_payout_changed = True
            platform_payout.payout_date = payout_date

        guest = booking.guest
        guest.first_name = post.get('first_name', guest.first_name or '').strip()
        guest.last_name = post.get('last_name', guest.last_name).strip() or guest.last_name
        guest.email = post.get('email', guest.email or '').strip()
        guest.phone = post.get('phone', guest.phone or '').strip()
        preferred_language = post.get('preferred_language', '').strip()
        if preferred_language:
            guest.preferred_language = preferred_language

        payment = getattr(booking, 'payment', None)
        payment_status = post.get('payment_status', '').strip() if payment is not None else ''
        balance_payment = getattr(booking, 'balance_payment', None)
        balance_status = post.get('balance_payment_status', '').strip() if balance_payment is not None else ''

        with transaction.atomic():
            booking.save()
            guest.save()
            if dates_changed:
                TaskHistoryEntry.objects.create(
                    booking=booking, description="Booking dates updated",
                    detail=f"Arrival {dates_before[0]} → {booking.arrival_date}, "
                           f"Departure {dates_before[1]} → {booking.departure_date}",
                    created_by=request.user,
                )

            arrival, _ = Arrival.objects.get_or_create(
                booking=booking, defaults={'self_check_in': False, 'meet_greet': True},
            )
            # flight_number/details are nullable (a fresh row has None), but a save always writes a
            # string (possibly '') - normalise here so an unset field doesn't look "changed" just
            # because it went from None to ''.
            arrival_before = (
                arrival.method, arrival.flight_number or '', arrival.travelling_from, arrival.hiring_car,
                arrival.time, arrival.details or '', arrival.self_check_in, arrival.meet_greet,
            )
            arrival.method = arrival_method
            arrival.flight_number = arrival_flight_number
            arrival.travelling_from = post.get('arrival_travelling_from', '').strip()
            arrival.hiring_car = post.get('arrival_hiring_car') == 'on'
            arrival.time = parsed_arrival_departure_time(post.get('arrival_time'))
            arrival.details = post.get('arrival_details', '').strip()[:140]
            arrival.self_check_in = post.get('self_check_in') == 'on'
            # Only touched when the Owner-booking row was actually enabled for this submission -
            # arrival_departure.js disables (not just hides) every field in that row while it's
            # collapsed, so a disabled checkbox is omitted from the POST body entirely per the
            # HTML form spec. Without this guard, saving any other field on a normal (non-owner)
            # booking would silently read that absence as "unchecked" and reset meet_greet to
            # False every time - same class of bug as departure.clean below.
            if booking.is_owner:
                arrival.meet_greet = post.get('meet_greet') == 'on'
            arrival.save()
            arrival_after = (
                arrival.method, arrival.flight_number, arrival.travelling_from, arrival.hiring_car,
                arrival.time, arrival.details, arrival.self_check_in, arrival.meet_greet,
            )
            arrival_changed = arrival_before != arrival_after

            # defaults={'clean': True} matters for a booking that predates create_booking()
            # eagerly creating this row - clean itself defaults on for every booking now (see
            # Departure's own docstring), but stays staff-editable here via the Owner-booking-
            # conditional checkbox (2026-08-27, per Thomas: back where it always was, alongside
            # Meet & Greet - the rare booking that shouldn't get one, e.g. an owner cleaning it
            # themselves, needed some way to say so, and this was already the simplest one).
            departure, _ = Departure.objects.get_or_create(booking=booking, defaults={'clean': True})
            departure_before = (
                departure.method, departure.flight_number or '', departure.travelling_from, departure.time,
                departure.details or '', departure.clean,
            )
            departure.method = departure_method
            departure.flight_number = departure_flight_number
            departure.travelling_from = post.get('departure_travelling_from', '').strip()
            departure.time = parsed_arrival_departure_time(post.get('departure_time'))
            departure.details = post.get('departure_details', '').strip()[:140]
            # Same "only touched when the row was actually enabled" guard as meet_greet above -
            # without it, saving any other field on a normal booking would silently wipe the new
            # default-True clean flag to False every time (confirmed live: changing just the
            # booking dates made its CleaningTask vanish from the cleaning calendar).
            if booking.is_owner:
                departure.clean = post.get('clean') == 'on'
            departure.save()
            departure_after = (
                departure.method, departure.flight_number, departure.travelling_from, departure.time,
                departure.details, departure.clean,
            )
            departure_changed = departure_before != departure_after

            # Runs after departure is saved above (its post_save signal - staff/signals.py - has
            # by now created/updated the booking's turnover CleaningTask row), so there's
            # something for the turnover planner fields to apply to. Mid-stay clean itself is no
            # longer settable here at all (2026-08-27, per Thomas: it's a guest-selected Extra,
            # see BookingFormMixin._save_extras/_parse_mid_stay_clean - a duplicate on/off control
            # on this page could silently stomp the guest's real choice with whatever this page
            # happened to last render, which is exactly what was happening). The mid-stay
            # CleaningTask row itself, and its planner fields below, still exist and sync
            # normally whenever the guest (or staff, via Manage Extras) actually requests one -
            # this only removes the second, redundant place that could set the flag itself. A
            # validation failure here only skips that one task (see _update_cleaning_tasks) - it
            # never rolls back the core booking fields already validated and about to be saved
            # above.
            self._update_cleaning_tasks(request, booking, task_dates_before)

            if arrival_changed or departure_changed:
                changes = _field_changes(
                    ('Method', 'Flight number', 'Travelling from', 'Hiring a car', 'Time', 'Details',
                     'Self check-in', 'Meet & greet'),
                    arrival_before, arrival_after,
                ) + _field_changes(
                    ('Method', 'Flight number', 'Travelling from', 'Time', 'Details', 'Clean on departure'),
                    departure_before, departure_after,
                )
                TaskHistoryEntry.objects.create(
                    booking=booking, description="Arrival/Departure updated",
                    detail='; '.join(changes), created_by=request.user,
                )

            if charge is not None:
                charge.save()
                if charge_changed:
                    TaskHistoryEntry.objects.create(
                        booking=booking, description="Rental charges updated", created_by=request.user,
                    )
            if platform_payout_changed:
                platform_payout.save()
                TaskHistoryEntry.objects.create(
                    booking=booking, description="Platform payout updated", created_by=request.user,
                )
            if payment is not None and payment_status:
                payment.status = payment_status
                payment.save(update_fields=['status'])
            if balance_payment is not None and balance_status:
                balance_payment.status = balance_status
                balance_payment.save(update_fields=['status'])
            if new_status and new_status != old_status:
                TaskHistoryEntry.objects.create(
                    booking=booking, description="Status changed",
                    detail=f"From '{old_status}' to '{new_status}'", created_by=request.user,
                )

        messages.success(request, "Booking updated.")

    def _update_cleaning_tasks(self, request, booking, task_dates_before):
        """Applies the embedded clean-planner fields (staff/_clean_planner_fields.html) for
        whichever of this booking's turnover/mid-stay CleaningTask rows currently exist - a date
        change goes through apply_manual_task_date() (same validation/override logic as a
        calendar drag or the popup's save), so it can never silently write an out-of-window date;
        assignees are always set regardless, since a checkbox change carries no such risk.

        task_dates_before (task_type -> date, snapshotted in _update_booking() before any saves)
        is what the posted date field is compared against, not the task's current .date - by the
        time this runs, saving the booking/departure above has already re-synced a non-manually-
        scheduled task's date to match a freshly-changed departure_date/mid_stay_clean_date (see
        staff/utils.py::_sync_task_date). Comparing against that already-updated value would make
        the *old*, no-longer-current value still sitting in the posted form (nobody touched this
        field, the booking's own dates changed instead) look like a deliberate edit away from the
        correct new one - confirmed live 2026-08-27: changing just the departure date raised "That
        date is outside this task's valid window." trying to move the task (already correctly
        auto-advanced) back to the stale date the page had loaded with."""
        for task_type, prefix in (('turnover', 'turnover_'), ('mid_stay', 'mid_stay_'), ('freshen', 'freshen_')):
            task = booking.cleaning_tasks.filter(task_type=task_type).first()
            if task is None or task.status == 'dismissed':
                continue
            new_date = _parsed_date(request.POST.get(f'{prefix}date'))
            if new_date and new_date != task_dates_before.get(task_type):
                error = apply_manual_task_date(task, new_date)
                if error:
                    messages.error(request, error)
                    continue
            task.assigned_to.set(request.POST.getlist(f'{prefix}assigned_to'))
            task.team = _parsed_team(request.POST.get(f'{prefix}team'))
            task.save(update_fields=['team'])

    def _add_deduction(self, request, booking):
        post = request.POST
        description = post.get('description', '').strip()
        amount = _parsed_decimal(post.get('amount'))
        if not description or amount is None:
            messages.error(request, "A deduction needs both a description and a valid amount.")
            return
        Deduction.objects.create(
            booking=booking, description=description, amount=amount,
            date=_parsed_date(post.get('date')) or timezone.now().date(),
        )
        messages.success(request, "Deduction added.")

    def _add_owner_payment(self, request, booking):
        post = request.POST
        amount = _parsed_decimal(post.get('amount'))
        if amount is None:
            messages.error(request, "An ad-hoc payment needs a valid amount.")
            return
        OwnerPayment.objects.create(
            booking=booking, amount=amount, note=post.get('note', '').strip(),
            date=_parsed_date(post.get('date')) or timezone.now().date(),
        )
        messages.success(request, "Ad-hoc payment recorded.")

    def _delete_owner_payment(self, request, booking):
        booking.owner_payments.filter(pk=request.POST.get('payment_id')).delete()
        messages.success(request, "Ad-hoc payment removed.")

    def _recalculate_payment_split(self, request, booking):
        """Reconciles Due at booking/balance with the current Rental Charges - the two are separate
        stored fields that don't auto-update when staff edit Basic Rental/Discount/Extra Guest/Admin
        (see the "Reconcile Rental Charges with the Payments from Guest split" plan). Once the
        deposit is actually paid, due_at_booking is a locked historical fact - same rule
        bookings/utils.py::recalculate_balance_for_party() already applies on the guest-facing side
        - so only due_at_balance moves; otherwise both are freshly derived via
        BookingSettings.split_subtotal() - NOT compute_costs(), which would re-derive its own
        fresh admin_fee_percent on top of a subtotal that already has Charge.admin baked in and
        double-count it. Never creates/deletes a BalancePayment row - same limitation
        recalculate_balance_for_party() already has."""
        charge = getattr(booking, 'charges', None)
        if charge is None or charge.total_rental is None or charge.admin is None:
            messages.error(request, "No Charge record to recalculate.")
            return
        subtotal = charge.total_rental + charge.admin
        payment = getattr(booking, 'payment', None)
        if payment is not None and payment.status == 'paid':
            charge.due_at_balance = max(subtotal - (charge.due_at_booking or Decimal('0')), Decimal('0'))
            charge.save(update_fields=['due_at_balance'])
        else:
            due_at_booking, due_at_balance, balance_due_date = BookingSettings.load().split_subtotal(
                subtotal, arrival_date=booking.arrival_date
            )
            charge.due_at_booking = due_at_booking
            charge.due_at_balance = due_at_balance
            charge.balance_due_date = balance_due_date
            charge.save(update_fields=['due_at_booking', 'due_at_balance', 'balance_due_date'])
        messages.success(request, "Payment split recalculated.")

    def _add_task_note(self, request, booking):
        note = request.POST.get('description', '').strip()
        if not note:
            messages.error(request, "Enter a note before adding it.")
            return
        TaskHistoryEntry.objects.create(
            booking=booking, description="Note added", detail=note, created_by=request.user,
        )
        messages.success(request, "Note added.")

    def _cancel_booking(self, request, booking):
        # Kept distinct from bookings/utils.py::cancel_booking_hold()'s 'Cancelled by guest' (the
        # guest-facing self-service cancellation) so the record still shows who actually cancelled
        # it. For a platform-sourced booking this doesn't tell Airbnb/Booking.com/Vrbo anything -
        # the real cancellation still has to happen on the platform itself, or the next iCal sync
        # (see bookings/utils.py::sync_ical_link()) will just recreate the row once it sees the
        # dates still occupying the feed.
        if booking.enquiry_status in CLOSED_STATUSES:
            messages.info(request, "This booking is already closed.")
            return
        old_status = booking.enquiry_status
        booking.enquiry_status = 'Cancelled by staff'
        booking.save(update_fields=['enquiry_status'])
        TaskHistoryEntry.objects.create(
            booking=booking, description="Status changed",
            detail=f"From '{old_status}' to 'Cancelled by staff'", created_by=request.user,
        )
        messages.success(request, "Booking cancelled.")

    def _uncancel_booking(self, request, booking):
        # Only revives a guest/staff cancellation - see REVIVABLE_STATUSES' docstring in
        # staff/utils.py for why 'Cancelled by platform' and the payment-failure statuses are
        # deliberately excluded. Still has to re-check for a new overlap: something else may have
        # been booked into these dates since this one was cancelled.
        if booking.enquiry_status not in REVIVABLE_STATUSES:
            messages.error(request, "This booking can't be revived from here.")
            return
        if Booking.objects.overlapping(
            booking.property, booking.arrival_date, booking.departure_date
        ).exclude(pk=booking.pk).exists():
            messages.error(request, "Can't revive - these dates are now booked by something else.")
            return
        old_status = booking.enquiry_status
        booking.enquiry_status = 'Booking confirmed'
        booking.save(update_fields=['enquiry_status'])
        TaskHistoryEntry.objects.create(
            booking=booking, description="Status changed",
            detail=f"From '{old_status}' to 'Booking confirmed'", created_by=request.user,
        )
        messages.success(request, "Booking revived.")

    def _dismiss_freshen_task(self, request, booking):
        task = booking.cleaning_tasks.filter(task_type='freshen').first()
        if task is None or task.status != 'pending':
            messages.error(request, "There's no Freshen clean to dismiss.")
            return
        task.status = 'dismissed'
        task.dismissed_by = request.user
        task.dismissed_at = timezone.now()
        task.dismissed_reason = 'manual'
        task.save(update_fields=['status', 'dismissed_by', 'dismissed_at', 'dismissed_reason'])
        messages.success(request, "Freshen clean dismissed.")

    def _undo_dismiss_freshen_task(self, request, booking):
        task = booking.cleaning_tasks.filter(task_type='freshen').first()
        if task is None or task.status != 'dismissed':
            messages.error(request, "There's no dismissed Freshen clean to restore.")
            return
        task.status = 'pending'
        task.dismissed_by = None
        task.dismissed_at = None
        task.dismissed_reason = ''
        task.save(update_fields=['status', 'dismissed_by', 'dismissed_at', 'dismissed_reason'])
        messages.success(request, "Freshen clean restored.")


@method_decorator(staff_page_required('can_view_cleaning_rota'), name='dispatch')
class StaffCleaningRotaView(View):
    """The digital cleaning diary/rota (Phase 3 of the staff-admin roadmap) - replaces a legacy
    daily email to one hardcoded cleaning contact. CleaningTask rows are the actual data, kept in
    sync with Departure.clean/Extra.mid_stay_clean via signals (see staff/signals.py). A read +
    mark-done status page over that synced data, not a place to edit the underlying flags (still
    the booking detail page's Booking Info panel) or who's assigned (2026-08-26: assignment moved
    to the cleaning calendar's popup - staff/views.py::StaffCleaningTaskSaveView - so this stays a
    "my day"/"staff visualiser" page; who's currently assigned is shown read-only). Superusers see
    every task in the window; a non-superuser with the role sees only their own assigned tasks."""
    template_name = 'staff/cleaning_rota.html'

    def get(self, request, *args, **kwargs):
        target_date = _parsed_date(request.GET.get('date')) or timezone.now().date()
        return render(request, self.template_name, self._context(request, target_date))

    def post(self, request, *args, **kwargs):
        target_date = _parsed_date(request.POST.get('date')) or timezone.now().date()
        redirect_url = f"{reverse('staff:cleaning_rota')}?date={target_date.isoformat()}"

        action = request.POST.get('action')
        task = CleaningTask.objects.filter(pk=request.POST.get('task_id')).first()
        if task is None:
            messages.error(request, "That cleaning task no longer exists.")
            return redirect(redirect_url)

        if action in ('mark_done', 'mark_pending'):
            if not (request.user.is_superuser or task.assigned_to.filter(pk=request.user.pk).exists()):
                raise PermissionDenied
            if action == 'mark_done':
                task.status = 'done'
                task.completed_by = request.user
                task.completed_at = timezone.now()
            else:
                task.status = 'pending'
                task.completed_by = None
                task.completed_at = None
            task.save(update_fields=['status', 'completed_by', 'completed_at'])
            messages.success(request, "Cleaning task updated.")

        return redirect(redirect_url)

    def _context(self, request, target_date):
        # Yesterday, today, and the next 3 days - a 5-day rolling window centred just behind
        # `target_date` (2026-08-26, per Thomas: wants to see what's just happened alongside
        # what's coming). Previous/Next page the window by 3 days rather than the full 5 - same
        # overlapping-window principle as the cleaning calendar's FullCalendar week view
        # (dateIncrement 3 over a 7-day duration), so consecutive pages still share a couple of
        # days instead of jumping to a disjoint block.
        window_dates = [target_date + timedelta(days=offset) for offset in range(-1, 4)]

        # select_related/prefetch_related here cover task.booking's own extras_summary()/
        # total_guests() needs (mid_stay/freshen) - the turnover case's *next* booking is a
        # separate row entirely and gets its own batched fetch below.
        extras_relations = (
            'booking__airport_transfers', 'booking__requested_extras__request_type',
            'booking__date_adjustments', 'booking__party',
        )
        if request.user.is_superuser:
            tasks = CleaningTask.objects.filter(date__range=(window_dates[0], window_dates[-1])).select_related(
                'booking__property', 'booking__guest', 'booking__extras',
            ).prefetch_related('assigned_to', *extras_relations)
        else:
            tasks = CleaningTask.objects.filter(
                date__range=(window_dates[0], window_dates[-1]), assigned_to=request.user,
            ).select_related(
                'booking__property', 'booking__guest', 'booking__extras',
            ).prefetch_related('assigned_to', *extras_relations)
        tasks = list(tasks)

        next_bookings_by_id = self._next_bookings_for_turnover_tasks(tasks)

        rows_by_date = {}
        for task in tasks:
            guest_booking, extras = self._guest_booking_and_extras(task, next_bookings_by_id)
            rows_by_date.setdefault(task.date, []).append({
                'task': task,
                'guest_booking': guest_booking,
                'extras': extras,
                'assigned_names': ', '.join(u.username for u in task.assigned_to.all()),
            })
        days = [{'date': day, 'rows': rows_by_date.get(day, [])} for day in window_dates]

        return {
            'target_date': target_date,
            'window_start': window_dates[0],
            'window_end': window_dates[-1],
            'prev_date': target_date - timedelta(days=3),
            'next_date': target_date + timedelta(days=3),
            'days': days,
            'is_superuser': request.user.is_superuser,
        }

    def _next_bookings_for_turnover_tasks(self, tasks):
        """{task.pk: next Booking or None} for every turnover task in `tasks`, fully prefetched
        for extras_summary() (extras/airport_transfers/requested_extras/date_adjustments) - one
        query for the (arrival_date, pk) pairs across every relevant property, one more to fetch
        the actual next-booking rows, instead of one next_confirmed_booking_after call (plus
        extras_summary()'s own four queries on top of *that*) per turnover task. Was 109 queries/
        8.1s for this page's 5-day window before batching (2026-09-03) - the same shape of bug as
        StaffCleaningEventsView's had, just worse here since extras_summary() compounds it."""
        import bisect

        turnover_tasks = [t for t in tasks if t.task_type == 'turnover']
        property_ids = {t.booking.property_id for t in turnover_tasks}
        pairs_by_property = Booking.objects.next_confirmed_booking_ids_by_property(property_ids)

        next_booking_id_for_task = {}
        for task in turnover_tasks:
            pairs = pairs_by_property.get(task.booking.property_id, [])
            dates = [d for d, _ in pairs]
            idx = bisect.bisect_left(dates, task.booking.departure_date)
            next_booking_id_for_task[task.pk] = pairs[idx][1] if idx < len(pairs) else None

        next_booking_ids = {bid for bid in next_booking_id_for_task.values() if bid is not None}
        bookings_by_id = Booking.objects.filter(pk__in=next_booking_ids).select_related(
            'guest', 'extras',
        ).prefetch_related(
            'airport_transfers', 'requested_extras__request_type', 'date_adjustments', 'party',
        ).in_bulk()

        return {
            task_pk: bookings_by_id.get(booking_id)
            for task_pk, booking_id in next_booking_id_for_task.items()
        }

    def _guest_booking_and_extras(self, task, next_bookings_by_id):
        """(booking to show guest/guest-count for, its extras) - NOT always task.booking's own:
        for a turnover, the departing guest's name/count/extras are irrelevant to prepping the
        property - what matters is who's actually arriving and what they ordered (a cot to set
        up, a welcome pack to leave out). The card showed the departing guest's name/count
        alongside the *arriving* guest's extras until 2026-09-03 (per Thomas) - confusing, since
        "no extras requested by the next guest" then sat directly under a name that wasn't the
        next guest. Both now come from the same booking, whichever one is actually relevant. Only
        a mid-stay clean (same guest still in-house, no next-arrival concept) shows the booking's
        own everything. guest_booking is None (distinct from an empty extras_summary) when a
        turnover has no next booking yet, so the template can say so rather than implying an
        empty guest line or "nothing ordered"."""
        if task.task_type != 'turnover':
            return task.booking, extras_summary(task.booking)
        next_booking = next_bookings_by_id.get(task.pk)
        if next_booking is None:
            return None, None
        return next_booking, extras_summary(next_booking)


@method_decorator(superuser_required, name='dispatch')
class StaffCleaningCalendarView(View):
    """A drag-to-reschedule calendar over the same CleaningTask rows as StaffCleaningRotaView,
    for the cleaning manager to shuffle a clean's date within its safe window (never before
    checkout, never so late the next confirmed arrival lands on an unclean property - see
    staff/utils.py::cleaning_task_valid_range). Deliberately superuser-only (not gated by a
    StaffRole field like the day-list rota) and deliberately a separate page rather than a
    replacement for it - the day-list stays the simple view a cleaner checks for their own day."""
    template_name = 'staff/cleaning_calendar.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {})


@method_decorator(superuser_required, name='dispatch')
class StaffCleaningEventsView(View):
    """JSON event feed for StaffCleaningCalendarView's FullCalendar instance - one object per
    CleaningTask whose date falls in the requested [start, end) range, with the valid drag window
    embedded in extendedProps so the frontend can shade invalid drop days without a round trip."""

    def get(self, request, *args, **kwargs):
        # FullCalendar sends full ISO datetimes (with a timezone offset) for its events-feed
        # start/end params, not plain dates - only the date portion matters for this all-day feed.
        start_date = _parsed_date((request.GET.get('start') or '').split('T')[0])
        end_date = _parsed_date((request.GET.get('end') or '').split('T')[0])
        tasks = CleaningTask.objects.select_related(
            'booking__property__location',
        ).prefetch_related('assigned_to').exclude(status='dismissed')
        if start_date:
            tasks = tasks.filter(date__gte=start_date)
        if end_date:
            tasks = tasks.filter(date__lt=end_date)
        tasks = list(tasks)

        # Was one cleaning_task_valid_range(task) call per task here - one next_confirmed_
        # arrival_after query per turnover task, each a full round trip to the remote Railway
        # Postgres (~75ms measured). batch_cleaning_task_valid_ranges collapses that to one query
        # total for every turnover task in the response (2026-09-03 - see its own docstring).
        valid_ranges = batch_cleaning_task_valid_ranges(tasks)

        events = []
        for task in tasks:
            min_date, max_date = valid_ranges[task.pk]
            location = task.booking.property.location
            assigned_usernames = [u.username for u in task.assigned_to.all()]
            # short_title, not the full property title - the location's own colour (below)
            # already identifies which property group a clean belongs to, so the extra label
            # length just crowds out the task-type/NEW/team/turnaround information that's
            # actually decision-relevant on a busy day. 'Turnover' itself is dropped from the
            # label (2026-09-03, per Thomas) - it's the overwhelming majority of tasks (130 of
            # 135 over a full month), so naming it on every tile is redundant; Mid-stay/Freshen
            # stay labelled since either one is the actually-informative, minority case. A
            # turnover's own suffix instead carries whichever of two things is more useful right
            # now (2026-09-03, per Thomas): the departure-to-next-arrival gap while nobody's
            # picked it up yet (how much runway staff have), or - once it's assigned - who has
            # it, since the gap's job (surfacing what still needs a decision) is done at that
            # point.
            title = task.booking.property.short_title
            if task.task_type != 'turnover':
                title = f"{title} — {task.get_task_type_display()}"
            elif assigned_usernames:
                title = f"{title} — {', '.join(assigned_usernames)}"
            elif max_date is not None:
                gap_days = (max_date - min_date).days
                if gap_days == 0:
                    gap_label = 'Same-day'
                else:
                    gap_label = f"{gap_days} day" + ('s' if gap_days != 1 else '')
                title = f"{title} — {gap_label}"
            if not assigned_usernames:
                title = f"NEW {title}"
            events.append({
                'id': task.pk,
                'title': title,
                'start': task.date.isoformat(),
                'allDay': True,
                'extendedProps': {
                    'task_type': task.task_type,
                    'status': task.status,
                    'property_id': task.booking.property_id,
                    'location_id': location.pk if location else None,
                    'location_color': (location.color or None) if location else None,
                    'assigned_to': assigned_usernames,
                    'team': task.team,
                    'booking_reference': task.booking.reference,
                    'min_date': min_date.isoformat(),
                    'max_date': max_date.isoformat() if max_date else None,
                },
            })
        return JsonResponse(events, safe=False)


@method_decorator(superuser_required, name='dispatch')
class StaffCleaningTaskMoveView(View):
    """Drag-drop endpoint for StaffCleaningCalendarView - re-validates the new date against
    cleaning_task_valid_range() server-side (the client-side shading in cleaning_calendar.js is
    just a UX aid, never trusted here), then marks the task manually_scheduled so the next
    unrelated booking save doesn't silently revert it (see staff/utils.py::_sync_task_date)."""

    def post(self, request, *args, **kwargs):
        task = CleaningTask.objects.select_related('booking').filter(pk=kwargs['pk']).first()
        if task is None:
            return JsonResponse({'error': 'That cleaning task no longer exists.'}, status=404)

        new_date = _parsed_date(request.POST.get('date'))
        if new_date is None:
            return JsonResponse({'error': 'Invalid date.'}, status=400)

        error = apply_manual_task_date(task, new_date)
        if error:
            return JsonResponse({'error': error}, status=400)
        return JsonResponse({'ok': True})


@method_decorator(superuser_required, name='dispatch')
class StaffCleaningTaskDetailView(View):
    """Fetched by cleaning_calendar.js when a calendar event is clicked - returns everything the
    clean-planner popup needs to render, both boundary summaries rendered server-side (this
    codebase has no JS templating anywhere), plus the task's current date/assignees and its valid
    drag window (same as the calendar's own event feed, so the popup's date field can be validated
    the same way client-side before ever hitting the save endpoint). What the two summary panels
    show depends on task_type, since each clean is racing to be ready for a different moment:
    - turnover: this booking's own Departure, and the next confirmed booking's Arrival - the
      actual guest this clean is racing to be ready for.
    - mid_stay: this booking's own Arrival ('Arrived') and Departure ('Departing') - context for a
      clean happening mid-way through a single, still-ongoing stay.
    - freshen: the property's Last Clean (not tied to any one booking - see
      staff/utils.py::property_last_clean_before) and this booking's own Arrival ('Next Arrival') -
      the guest this speculative clean exists to prepare for."""

    def get(self, request, *args, **kwargs):
        task = CleaningTask.objects.select_related(
            'booking__guest', 'booking__departure', 'booking__arrival', 'booking__property',
        ).filter(pk=kwargs['pk']).first()
        if task is None:
            return JsonResponse({'error': 'That cleaning task no longer exists.'}, status=404)

        min_date, max_date = cleaning_task_valid_range(task)
        booking = task.booking

        if task.task_type == 'freshen':
            last_clean = property_last_clean_before(booking.property, booking)
            departure_html = render_to_string('staff/_stay_boundary_summary.html', {
                'heading': 'Last Clean', 'date': last_clean, 'is_arrival': False,
                'empty_message': 'No earlier clean on record for this property.',
            })
        else:
            departure = getattr(booking, 'departure', None)
            departure_method_label = dict(TravelMethod.departure_choices()).get(departure.method) if departure else None
            departure_html = render_to_string('staff/_stay_boundary_summary.html', {
                'heading': 'Departing' if task.task_type == 'mid_stay' else 'Departure',
                'date': booking.departure_date, 'record': departure,
                'method_label': departure_method_label, 'booking': booking, 'is_arrival': False,
            })

        arrival_html = ''
        if task.task_type == 'turnover':
            next_booking = Booking.objects.next_confirmed_booking_after(booking.property, booking.departure_date)
            next_arrival = getattr(next_booking, 'arrival', None) if next_booking else None
            next_arrival_method_label = dict(TravelMethod.choices).get(next_arrival.method) if next_arrival else None
            arrival_html = render_to_string('staff/_stay_boundary_summary.html', {
                'heading': 'Next Arrival', 'date': next_booking.arrival_date if next_booking else None,
                'record': next_arrival, 'method_label': next_arrival_method_label,
                'booking': next_booking, 'is_arrival': True,
                'guests': next_booking.total_guests() if next_booking else None,
                'extras': extras_summary(next_booking) if next_booking else None,
                'empty_message': 'No upcoming arrival scheduled for this property.',
            })
        elif task.task_type in ('mid_stay', 'freshen'):
            arrival = getattr(booking, 'arrival', None)
            arrival_method_label = dict(TravelMethod.choices).get(arrival.method) if arrival else None
            arrival_html = render_to_string('staff/_stay_boundary_summary.html', {
                'heading': 'Arrived' if task.task_type == 'mid_stay' else 'Next Arrival',
                'date': booking.arrival_date, 'record': arrival, 'method_label': arrival_method_label,
                'booking': booking, 'is_arrival': True,
                'guests': booking.total_guests(), 'extras': extras_summary(booking),
            })

        assignable_users = User.objects.filter(staff_profile__role__is_cleaning_staff=True).order_by('username')
        planner_html = render_to_string('staff/_clean_planner_fields.html', {
            'task': task, 'field_prefix': '', 'standalone': True,
            'assignable_users': assignable_users,
            'assigned_ids': set(task.assigned_to.values_list('pk', flat=True)),
            'min_date': min_date, 'max_date': max_date,
        })
        return JsonResponse({'departure_html': departure_html, 'arrival_html': arrival_html, 'planner_html': planner_html})


@method_decorator(superuser_required, name='dispatch')
class StaffCleaningTaskSaveView(View):
    """Save handler for the clean-planner popup (and, via the same helper, the booking detail
    page's embedded planner) - sets the task's assignees unconditionally, and only runs the date
    through apply_manual_task_date() when it actually changed, so ticking a cleaner's checkbox
    alone never falsely marks the task as manually rescheduled."""

    def post(self, request, *args, **kwargs):
        task = CleaningTask.objects.select_related('booking').filter(pk=kwargs['pk']).first()
        if task is None:
            return JsonResponse({'error': 'That cleaning task no longer exists.'}, status=404)

        new_date = _parsed_date(request.POST.get('date'))
        if new_date is None:
            return JsonResponse({'error': 'Invalid date.'}, status=400)

        if new_date != task.date:
            error = apply_manual_task_date(task, new_date)
            if error:
                return JsonResponse({'error': error}, status=400)

        task.assigned_to.set(request.POST.getlist('assigned_to'))
        task.team = _parsed_team(request.POST.get('team'))
        task.save(update_fields=['team'])
        return JsonResponse({
            'ok': True,
            'date': task.date.isoformat(),
            'team': task.team,
            'assigned_to': list(task.assigned_to.values_list('pk', flat=True)),
        })


@method_decorator(superuser_required, name='dispatch')
class StaffCleaningTaskDismissView(View):
    """Delete handler for the clean-planner popup's Freshen task - only ever meaningful for
    task_type='freshen' (turnover/mid-stay aren't user-deletable, they follow Departure.clean/
    Extra.mid_stay_clean instead). A dismissal never removes the row - see CleaningTask's own
    docstring for why - it's a status flip staff can undo from the booking detail page's Cleaning
    panel (StaffBookingDetailView._undo_dismiss_freshen_task), not from here."""

    def post(self, request, *args, **kwargs):
        task = CleaningTask.objects.select_related('booking').filter(pk=kwargs['pk']).first()
        if task is None:
            return JsonResponse({'error': 'That cleaning task no longer exists.'}, status=404)
        if task.task_type != 'freshen':
            return JsonResponse({'error': 'Only a Freshen clean can be dismissed here.'}, status=400)
        if task.status != 'pending':
            return JsonResponse({'error': 'That task is no longer pending.'}, status=400)

        task.status = 'dismissed'
        task.dismissed_by = request.user
        task.dismissed_at = timezone.now()
        task.dismissed_reason = 'manual'
        task.save(update_fields=['status', 'dismissed_by', 'dismissed_at', 'dismissed_reason'])
        return JsonResponse({'ok': True})


@method_decorator(staff_page_required('can_view_checkins_calendar'), name='dispatch')
class StaffCheckinCalendarView(View):
    """The check-ins calendar's own page shell - gated by the can_view_checkins_calendar StaffRole
    flag rather than superuser_required (unlike the cleaning calendar's drag view): check-ins is
    meant for day-to-day use by whoever runs arrivals, not a superuser-only scheduling tool."""
    template_name = 'staff/checkins_calendar.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {})


@method_decorator(staff_page_required('can_view_checkins_calendar'), name='dispatch')
class StaffCheckinEventsView(View):
    """JSON event feed for StaffCheckinCalendarView's FullCalendar timeGrid instance - one object
    per Checkin whose date falls in the requested [start, end) range. Any two checkins on the same
    date whose real times fall within EVENT_BLOCK_MINUTES of each other are pushed apart into a
    simple back-to-back sequence, one EVENT_BLOCK_MINUTES-wide slot each with a real visual gap
    between them (2026-08-28, per Thomas: legible sequential placement matters more
    here than perfect fidelity to the calculated ETA) - this is broader than just an exact-same-
    minute collision, since two genuinely close-but-different ETAs (e.g. 14:00 and 14:01) would
    otherwise still overlap and trigger FullCalendar's side-by-side column-split layout, not the
    stacked-list look this calendar wants. Only the *rendered* start moves, the stored Checkin.time
    and the title's own time label both keep showing the real time - and since the render nudge can
    now legitimately diverge from that real time, the calendar tile itself no longer displays a
    clock time at all (checkins_calendar.js's displayEventTime: false) to avoid it reading as
    authoritative.

    A self-check-in booking's own 'arrival' Checkin is excluded from this feed entirely (2026-09-03,
    per Thomas) - self-check-in means nobody actually attends a meet & greet at the guest's real
    ETA, so a normal arrival tile there is a redundant, never-actioned duplicate of the key_box/
    welcome_visit tiles that already cover the same booking. The row itself still gets created/kept
    in sync as before (see sync_checkins_for_booking()) - it's the one place extras_collected/
    deposit_collected live, and finance/services.py::deposits_due_in_range() depends on a
    task_type='arrival' row existing to query against - only its calendar visibility changes.
    StaffCheckinDetailView folds its Guest/Arrival-plans content into the key_box popup and its
    Extras/Security-deposit content into the welcome_visit popup instead, so nothing it would have
    shown is actually lost, just relocated to the two tiles staff do interact with."""

    # The push-out step between consecutive blocks. Deliberately NOT the same as
    # checkins_calendar.js's defaultTimedEventDuration ('00:29') - a block rendered for the full
    # 30 minutes would be flush against the next one with no visual gap, which still reads as one
    # touching/overlapping mass even though FullCalendar's own collision math would call it fine
    # (2026-08-28, per Thomas, echoing the same thing in Google Calendar). One minute of empty
    # space between blocks is enough to read as clearly separate.
    EVENT_BLOCK_MINUTES = 30

    def get(self, request, *args, **kwargs):
        start_date = _parsed_date((request.GET.get('start') or '').split('T')[0])
        end_date = _parsed_date((request.GET.get('end') or '').split('T')[0])
        checkins = Checkin.objects.select_related(
            'booking__property__location', 'booking__guest', 'booking__arrival',
        ).exclude(task_type='arrival', booking__arrival__self_check_in=True)
        if start_date:
            checkins = checkins.filter(date__gte=start_date)
        if end_date:
            checkins = checkins.filter(date__lt=end_date)

        next_free_by_date = {}
        events = []
        for checkin in checkins.order_by('date', 'time', 'pk'):
            booking = checkin.booking
            location = booking.property.location
            arrival = getattr(booking, 'arrival', None)
            guest_name = f"{booking.guest.first_name or ''} {booking.guest.last_name}".strip()
            # A guessed/fallback ETA (compute_arrival_eta's has_given_eta=False - no real Arrival.
            # time to work with) reads as just another clock time otherwise, indistinguishable from
            # a legitimate guest-given one (2026-09-03, per Thomas). Only applies to 'arrival' -
            # key_box/welcome_visit times come from CheckinSettings' own fixed policy slots, not
            # the guest's ETA, so this distinction is meaningless for them. A manual drag
            # (manually_scheduled) is a deliberate staff decision, not a guess, so it's exempted
            # even if the booking's own Arrival still has no real time on file.
            if (checkin.task_type == 'arrival' and not checkin.manually_scheduled
                    and not compute_arrival_eta(booking)[1]):
                time_label = 'No ETA'
            else:
                time_label = checkin.time.strftime('%H:%M') if checkin.time else 'No time given'
            title = f"{booking.property.short_title}, {guest_name}, {time_label}"

            all_day = checkin.time is None
            if all_day:
                start = checkin.date.isoformat()
            else:
                render_time = checkin.time
                next_free = next_free_by_date.get(checkin.date)
                if next_free is not None and render_time < next_free:
                    render_time = next_free
                next_free_by_date[checkin.date] = (
                    datetime.combine(checkin.date, render_time) + timedelta(minutes=self.EVENT_BLOCK_MINUTES)
                ).time()
                start = f"{checkin.date.isoformat()}T{render_time.strftime('%H:%M:%S')}"

            events.append({
                'id': checkin.pk,
                'title': title,
                'start': start,
                'allDay': all_day,
                'extendedProps': {
                    'task_type': checkin.task_type,
                    'status': checkin.status,
                    'meet_greet': arrival.meet_greet if arrival else True,
                    'property_id': booking.property_id,
                    'location_id': location.pk if location else None,
                    'location_color': (location.color or None) if location else None,
                    'booking_reference': booking.reference,
                },
            })
        return JsonResponse(events, safe=False)


@method_decorator(staff_page_required('can_view_checkins_calendar'), name='dispatch')
class StaffCheckinMoveView(View):
    """Drag-drop endpoint for the check-ins calendar - a Checkin's date is never draggable (an
    arrival-date change happens on the booking's own page instead), so this only ever accepts a
    new time on the checkin's existing date. checkin_valid_range()'s window is always that date's
    full 00:00-23:59 span, so the only real rejection case is an all-day event being dropped onto
    a timed slot with no time to actually move - the client blocks that already
    (checkins_calendar.js), this is the server-side backstop."""

    def post(self, request, *args, **kwargs):
        checkin = Checkin.objects.select_related('booking').filter(pk=kwargs['pk']).first()
        if checkin is None:
            return JsonResponse({'error': 'That check-in no longer exists.'}, status=404)

        new_time = _parsed_time(request.POST.get('time'))
        if new_time is None:
            return JsonResponse({'error': 'Invalid time.'}, status=400)

        error = apply_manual_checkin_time(checkin, new_time)
        if error:
            return JsonResponse({'error': error}, status=400)
        return JsonResponse({'ok': True})


@method_decorator(staff_page_required('can_view_checkins_calendar'), name='dispatch')
class StaffCheckinDetailView(View):
    """Fetched by checkins_calendar.js when a calendar event is clicked - returns the popup's
    content as server-rendered HTML (this codebase has no JS templating anywhere), shaped
    differently per task_type. 'arrival' (a non-self-check-in booking's only Checkin row) gets the
    full guest/travel/extras/deposit picture in one popup, same as always.

    For a self-check-in booking, that same picture is split across the two tiles staff actually
    interact with (2026-09-03, per Thomas - the 'arrival' row itself is hidden from the calendar
    entirely, see StaffCheckinEventsView): 'key_box' (the same-day key-box-prep task) gets the
    Guest/Arrival-plans sections, since that's the prep staff need before the guest even arrives;
    'welcome_visit' (the next-day policy visit) gets Extras/Security-deposit, since that's when
    staff actually meet the guest and can collect cash. Both still need the extras/deposit
    checkboxes to write to the *underlying 'arrival' row* (extras_collected/deposit_collected only
    exist there, and finance/services.py::deposits_due_in_range() specifically queries
    task_type='arrival' - see StaffCheckinEventsView's docstring), so 'deposit_checkin' in context
    is always that sibling row, not `checkin` itself, whenever it differs."""

    def get(self, request, *args, **kwargs):
        checkin = Checkin.objects.select_related(
            'booking__guest', 'booking__property', 'booking__arrival',
        ).filter(pk=kwargs['pk']).first()
        if checkin is None:
            return JsonResponse({'error': 'That check-in no longer exists.'}, status=404)

        booking = checkin.booking
        context = {'checkin': checkin, 'booking': booking}
        if checkin.task_type in ('arrival', 'key_box'):
            context['arrival'] = getattr(booking, 'arrival', None)
        if checkin.task_type in ('arrival', 'welcome_visit'):
            # Charge.security is the actual source of truth for what's owed (see its own
            # docstring, bookings/models.py) - read directly, not recomputed here. A booking with
            # no Charge at all (an owner's own self-booking, created via create_owner_booking(),
            # which never gets one - "an owner stay is never charged") has nothing owed either.
            charge = getattr(booking, 'charges', None)
            deposit_checkin = checkin if checkin.task_type == 'arrival' else Checkin.objects.filter(
                booking=booking, task_type='arrival',
            ).first()
            context.update({
                'extras': extras_summary(booking),
                'deposit_amount': charge.security if charge and charge.security else None,
                'deposit_checkin': deposit_checkin,
            })
        popup_html = render_to_string('staff/_checkin_popup.html', context)
        return JsonResponse({'popup_html': popup_html})


@method_decorator(staff_page_required('can_view_checkins_calendar'), name='dispatch')
class StaffCheckinToggleDoneView(View):
    """Flips a Checkin between 'pending' and 'done' - the popup's "Mark as done"/"Mark as not
    done" button, a real two-way toggle (unlike CleaningTask, which has no undo-done today)."""

    def post(self, request, *args, **kwargs):
        checkin = Checkin.objects.filter(pk=kwargs['pk']).first()
        if checkin is None:
            return JsonResponse({'error': 'That check-in no longer exists.'}, status=404)

        if checkin.status == 'done':
            checkin.status = 'pending'
            checkin.completed_by = None
            checkin.completed_at = None
        else:
            checkin.status = 'done'
            checkin.completed_by = request.user
            checkin.completed_at = timezone.now()
        checkin.save(update_fields=['status', 'completed_by', 'completed_at'])
        return JsonResponse({'ok': True, 'status': checkin.status})


@method_decorator(staff_page_required('can_view_checkins_calendar'), name='dispatch')
class StaffCheckinSaveView(View):
    """Saves the popup's extras-collected/deposit-collected checkboxes - only meaningful for
    task_type='arrival', matching Checkin's own docstring on those fields."""

    def post(self, request, *args, **kwargs):
        checkin = Checkin.objects.filter(pk=kwargs['pk']).first()
        if checkin is None:
            return JsonResponse({'error': 'That check-in no longer exists.'}, status=404)
        if checkin.task_type != 'arrival':
            return JsonResponse({'error': 'Only an arrival check-in tracks extras/deposit.'}, status=400)

        checkin.extras_collected = request.POST.get('extras_collected') == 'true'
        checkin.deposit_collected = request.POST.get('deposit_collected') == 'true'
        checkin.save(update_fields=['extras_collected', 'deposit_collected'])
        return JsonResponse({'ok': True})


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceMemosView(View):
    """The Memos tab - one row per finance.models.Memo, grouped by its turnover CleaningTask's
    date, a 5-day window starting the day before ?date= (default today) and paged by 3 - same
    -1..+3-window/3-day-paging convention as StaffCleaningRotaView (2026-09, per Thomas: wants
    yesterday visible alongside what's still coming, and the whole window to fit the viewport
    without scrolling). Each row shows its computed total (Memo.total()) and three actions: View
    memo (its own detail page), Add service (deep-links to the Ad-hoc Services page with this
    property pre-selected), Send (StaffFinanceMemoSendView). No POST here - every action routes
    through its own small view, matching StaffCleaningTaskDismissView's one-view-per-action
    convention."""
    template_name = 'staff/finance_memos.html'

    def get(self, request, *args, **kwargs):
        target_date = _parsed_date(request.GET.get('date')) or timezone.now().date()
        window_dates = [target_date + timedelta(days=offset) for offset in range(-1, 4)]

        memos = Memo.objects.filter(
            cleaning_task__date__range=(window_dates[0], window_dates[-1]),
        ).select_related('property', 'cleaning_task').prefetch_related('ad_hoc_services')

        rows_by_date = {}
        for memo in memos:
            rows_by_date.setdefault(memo.cleaning_task.date, []).append(memo)
        for rows in rows_by_date.values():
            rows.sort(key=lambda memo: memo.property.title)
        days = [{'date': day, 'rows': rows_by_date.get(day, [])} for day in window_dates]

        return render(request, self.template_name, {
            'days': days,
            'target_date': target_date,
            'window_start': window_dates[0],
            'window_end': window_dates[-1],
            'prev_date': target_date - timedelta(days=3),
            'next_date': target_date + timedelta(days=3),
            'active_tab': 'memos',
        })


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceMemoDetailView(View):
    """The memo document itself - property, clean date, Clean/Meet & Greet/Ad-hoc lines, total,
    sent status - as its own printable-feeling page with a back-link to the Memos tab."""
    template_name = 'staff/finance_memo_detail.html'

    def get(self, request, pk, *args, **kwargs):
        memo = Memo.objects.select_related('property', 'cleaning_task', 'sent_by').filter(pk=pk).first()
        if memo is None:
            raise Http404("No memo found.")
        return render(request, self.template_name, {
            'memo': memo,
            'ad_hoc_services': memo.ad_hoc_services.order_by('date'),
        })


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceMemoSendView(View):
    """Marks a Memo sent - freezes its clean_fee/meet_greet_fee permanently (see Memo's own
    docstring) and immediately sweeps any unattached AdHocService for the property onto whatever
    memo is now open next (finance/services.py::sweep_unattached_ad_hoc_services), satisfying "any
    new ad-hoc service rolls onto the next memo from this moment". Emailing the memo to the
    owner is explicitly out of scope/stubbed - this project has no outbound email yet - so Send
    here just records who/when."""

    def post(self, request, pk, *args, **kwargs):
        memo = Memo.objects.select_related('property').filter(pk=pk).first()
        if memo is None:
            messages.error(request, "That memo no longer exists.")
            return redirect('staff:finance_memos')
        if memo.sent_at is not None:
            messages.error(request, "That memo has already been sent.")
            return redirect('staff:finance_memos')

        memo.sent_at = timezone.now()
        memo.sent_by = request.user
        memo.save(update_fields=['sent_at', 'sent_by'])
        sweep_unattached_ad_hoc_services(memo.property)
        messages.success(request, "Memo marked as sent.")

        redirect_date = request.POST.get('date', '').strip()
        redirect_url = reverse('staff:finance_memos')
        if redirect_date:
            redirect_url = f"{redirect_url}?date={redirect_date}"
        return redirect(redirect_url)


@method_decorator(staff_email_action_required, name='dispatch')
class StaffBookingEmailSendView(View):
    """"Send now" on one ScheduledEmail row in the Booking View's Next step(s) panel - jumps it
    ahead of its scheduled date. Actually dispatches a real email via communications/services/
    sending.py::send_scheduled_email(), unlike StaffFinanceMemoSendView above (which only ever
    records who/when - this project genuinely had no outbound email until the communications app).
    Eligibility (is this email still actually needed) is rechecked live INSIDE
    send_scheduled_email() itself, not here - see ScheduledEmail's own docstring for why a manual
    jump-ahead must be exactly as safe as the cron: an earlier draft of this feature only checked
    row status here, which would have let staff fire a since-become-stale email (e.g. a balance
    reminder after the balance was already paid)."""

    def post(self, request, reference, pk, *args, **kwargs):
        scheduled_email = ScheduledEmail.objects.select_related('booking', 'template').filter(
            pk=pk, booking__reference__iexact=reference,
        ).first()
        if scheduled_email is None:
            messages.error(request, "That email no longer exists.")
        elif scheduled_email.status != ScheduledEmail.Status.PENDING:
            messages.error(request, "That email has already been sent.")
        else:
            send_scheduled_email(scheduled_email, actor=request.user)
            scheduled_email.refresh_from_db()
            if scheduled_email.status == ScheduledEmail.Status.SKIPPED:
                messages.warning(request, f"Skipped - no longer needed: {scheduled_email.error_message}")
            elif scheduled_email.status == ScheduledEmail.Status.FAILED:
                messages.error(request, f"Could not send: {scheduled_email.error_message}")
            else:
                messages.success(request, "Email sent.")
        return redirect('staff:booking_detail', reference=reference)


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceAdHocServiceListView(View):
    """The Ad-hoc Services CRUD page - a create form (property/description/cost) on the left, and
    a searchable last-30 list with inline edit/delete on the right, following
    StaffSettingsView's action-dispatch POST convention. Deliberately its own page rather than
    only reachable from within a Memo, since a service can be logged any time - between bookings,
    before a clean is even scheduled (finance.models.AdHocService.save() leaves it unattached
    until a matching Memo exists, see that model's own docstring)."""
    template_name = 'staff/finance_ad_hoc_services.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._context(request))

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        handler = {
            'create': self._create,
            'update': self._update,
            'delete': self._delete,
        }.get(action)
        if handler is not None:
            handler(request)
        redirect_url = reverse('staff:finance_ad_hoc_services')
        selected_property = request.POST.get('redirect_property', '').strip()
        if selected_property:
            redirect_url = f"{redirect_url}?property={selected_property}"
        return redirect(redirect_url)

    def _context(self, request):
        selected_property = None
        property_id = request.GET.get('property', '').strip()
        if property_id.isdigit():
            selected_property = Property.objects.filter(pk=property_id).first()

        services = AdHocService.objects.select_related('property', 'memo').order_by('-created_at')
        if selected_property:
            services = services.filter(property=selected_property)

        return {
            'properties': Property.objects.order_by('title'),
            'selected_property': selected_property,
            'services': services[:30],
            'active_tab': 'services',
            'today': timezone.now().date(),
        }

    def _create(self, request):
        post = request.POST
        service = AdHocService(
            property_id=post.get('property') or None,
            description=post.get('description', '').strip(),
            cost=_parsed_decimal(post.get('cost')),
            date=_parsed_date(post.get('date')) or timezone.now().date(),
        )
        try:
            service.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        service.save()
        messages.success(request, "Ad-hoc service added.")

    def _update(self, request):
        post = request.POST
        service = AdHocService.objects.filter(pk=post.get('service_id')).first()
        if service is None:
            messages.error(request, "That ad-hoc service no longer exists.")
            return
        new_property_id = post.get('property') or None
        property_changed = str(service.property_id) != str(new_property_id)
        service.property_id = new_property_id
        service.description = post.get('description', '').strip()
        service.cost = _parsed_decimal(post.get('cost'))
        service.date = _parsed_date(post.get('date')) or service.date
        if property_changed:
            # A service now belongs to a different property than the memo it's currently attached
            # to (if any) - re-run the same "attach to the current open memo, or leave unattached"
            # logic a brand-new service goes through, rather than leaving it pointed at a memo for
            # the wrong property.
            service.memo = open_memo_for_property(service.property) if new_property_id else None
        try:
            service.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        service.save()
        messages.success(request, "Ad-hoc service updated.")

    def _delete(self, request):
        service = AdHocService.objects.filter(pk=request.POST.get('service_id')).first()
        if service is None:
            messages.error(request, "That ad-hoc service no longer exists.")
            return
        service.delete()
        messages.success(request, "Ad-hoc service deleted.")


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinancePayoutsView(View):
    """The Payouts tab - only owners paid on a regular schedule (Owner.is_paid_regularly=True) on
    properties whose booking_company has finances_managed_internally=True, grouped by due_date
    (bookings/payouts.py::compute_owner_payout's own due_date figure), same -1..+3-window/3-day-
    paging shape as StaffFinanceMemosView/StaffCleaningRotaView. Rows that already have a
    PayoutRecord stay visible showing who paid it and when, rather than disappearing - matching
    the cleaning rota's own completed-rows-stay-visible convention, so the list stays a complete
    record."""
    template_name = 'staff/finance_payouts.html'

    def get(self, request, *args, **kwargs):
        target_date = _parsed_date(request.GET.get('date')) or timezone.now().date()
        window_dates = [target_date + timedelta(days=offset) for offset in range(-1, 4)]

        due = payouts_due_in_range(window_dates[0], window_dates[-1])
        paid_records = {
            record.booking_id: record
            for record in PayoutRecord.objects.filter(booking__in=[b for b, _ in due]).select_related('paid_by')
        }

        rows_by_date = {}
        for booking, payout in due:
            rows_by_date.setdefault(payout['due_date'], []).append({
                'booking': booking,
                'payout': payout,
                'record': paid_records.get(booking.pk),
            })
        for rows in rows_by_date.values():
            rows.sort(key=lambda row: row['booking'].property.title)
        days = [{'date': day, 'rows': rows_by_date.get(day, [])} for day in window_dates]

        return render(request, self.template_name, {
            'days': days,
            'target_date': target_date,
            'window_start': window_dates[0],
            'window_end': window_dates[-1],
            'prev_date': target_date - timedelta(days=3),
            'next_date': target_date + timedelta(days=3),
            'active_tab': 'payouts',
        })


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinancePayoutMarkPaidView(View):
    """Snapshots the booking's current owner_balance into a new PayoutRecord - see that model's
    own docstring for why the amount is frozen at mark-paid time rather than re-derived live."""

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            messages.error(request, "That booking no longer exists.")
            return redirect('staff:finance_payouts')
        if PayoutRecord.objects.filter(booking=booking).exists():
            messages.error(request, "That payout has already been marked as paid.")
            return redirect('staff:finance_payouts')

        payout = compute_owner_payout(booking)
        if not payout['available']:
            messages.error(request, "That booking's payout can't be computed right now.")
            return redirect('staff:finance_payouts')

        PayoutRecord.objects.create(booking=booking, amount=payout['owner_balance'], paid_by=request.user)
        messages.success(request, "Payout marked as paid.")

        redirect_date = request.POST.get('date', '').strip()
        redirect_url = reverse('staff:finance_payouts')
        if redirect_date:
            redirect_url = f"{redirect_url}?date={redirect_date}"
        return redirect(redirect_url)


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceDepositsView(View):
    """The Deposits tab - bookings whose cash security deposit is due for return: taken at
    check-in (Checkin.deposit_collected) and the end-of-stay clean now marked done, grouped by the
    clean's completion date, same -1..+3-window/3-day-paging shape as StaffFinanceMemosView/
    StaffFinancePayoutsView. Rows that already have a DepositReturn stay visible showing who
    returned it and when, rather than disappearing - same completed-rows-stay-visible convention
    as Payouts."""
    template_name = 'staff/finance_deposits.html'

    def get(self, request, *args, **kwargs):
        target_date = _parsed_date(request.GET.get('date')) or timezone.now().date()
        window_dates = [target_date + timedelta(days=offset) for offset in range(-1, 4)]

        due = deposits_due_in_range(window_dates[0], window_dates[-1])
        returned_records = {
            record.booking_id: record
            for record in DepositReturn.objects.filter(booking__in=[b for b, _ in due]).select_related('returned_by')
        }

        rows_by_date = {}
        for booking, completed_date in due:
            rows_by_date.setdefault(completed_date, []).append({
                'booking': booking,
                'record': returned_records.get(booking.pk),
            })
        for rows in rows_by_date.values():
            rows.sort(key=lambda row: row['booking'].property.title)
        days = [{'date': day, 'rows': rows_by_date.get(day, [])} for day in window_dates]

        return render(request, self.template_name, {
            'days': days,
            'target_date': target_date,
            'window_start': window_dates[0],
            'window_end': window_dates[-1],
            'prev_date': target_date - timedelta(days=3),
            'next_date': target_date + timedelta(days=3),
            'deposit_amount': BookingSettings.load().security_deposit_amount,
            'active_tab': 'deposits',
        })


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceDepositReturnMarkReturnedView(View):
    """Snapshots BookingSettings.security_deposit_amount into a new DepositReturn - see that
    model's own docstring for why the amount is frozen at mark-returned time rather than
    re-derived live."""

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            messages.error(request, "That booking no longer exists.")
            return redirect('staff:finance_deposits')
        if DepositReturn.objects.filter(booking=booking).exists():
            messages.error(request, "That deposit has already been marked as returned.")
            return redirect('staff:finance_deposits')

        amount = BookingSettings.load().security_deposit_amount
        DepositReturn.objects.create(booking=booking, amount=amount, returned_by=request.user)
        messages.success(request, "Deposit marked as returned.")

        redirect_date = request.POST.get('date', '').strip()
        redirect_url = reverse('staff:finance_deposits')
        if redirect_date:
            redirect_url = f"{redirect_url}?date={redirect_date}"
        return redirect(redirect_url)


@method_decorator(staff_page_required('can_view_finance'), name='dispatch')
class StaffFinanceStatementView(View):
    """Generates a printable Statement for one Owner or one Property over a date range - the
    Memo section and the payout section are gated independently per property (confirmed with
    Thomas): a property's sent Memos show whenever its cleaning_company has
    finances_managed_internally=True, regardless of its booking_company; its net payout figure
    shows whenever its booking_company has finances_managed_internally=True AND its owner has
    is_paid_regularly=False, regardless of its cleaning_company. A property with both shows both
    sections together - the combined case where bookings/payouts.py::compute_owner_payout's
    already-net owner_balance (it already deducts clean_fee/meet_greet_fee once) must not be
    reduced by the Memo's fee lines a second time - only by the AdHocService total, per the plan
    this was built from."""
    template_name = 'staff/finance_statement.html'

    def get(self, request, *args, **kwargs):
        scope = request.GET.get('scope', '').strip()
        today = timezone.now().date()
        start = _parsed_date(request.GET.get('start')) or today.replace(day=1)
        end = _parsed_date(request.GET.get('end')) or _last_day_of_month(today)
        owner = None
        property = None
        if scope == 'owner' and request.GET.get('owner_id', '').isdigit():
            owner = Owner.objects.filter(pk=request.GET['owner_id']).first()
        elif scope == 'property' and request.GET.get('property_id', '').isdigit():
            property = Property.objects.select_related(
                'owner', 'booking_company', 'cleaning_company',
            ).filter(pk=request.GET['property_id']).first()

        context = {
            'owners': Owner.objects.order_by('name'),
            'properties': Property.objects.order_by('title'),
            'scope': scope,
            'owner': owner,
            'property': property,
            'start': start,
            'end': end,
            'sections': None,
            'active_tab': 'statement',
        }
        if (owner or property) and start and end:
            context['sections'] = self._sections(owner, property, start, end)
        return render(request, self.template_name, context)

    def _sections(self, owner, property, start, end):
        if property:
            properties = [property]
        else:
            properties = list(Property.objects.filter(owner=owner).select_related(
                'owner', 'booking_company', 'cleaning_company',
            ))
        sections = []
        for prop in properties:
            memo_section = None
            if prop.cleaning_company_id and prop.cleaning_company.finances_managed_internally:
                memos = list(Memo.objects.filter(
                    property=prop, sent_at__date__range=(start, end),
                ).prefetch_related('ad_hoc_services').order_by('sent_at'))
                memo_section = {
                    'memos': memos,
                    'total': sum((memo.total() for memo in memos), Decimal('0')),
                }

            payout_section = None
            if (
                prop.owner_id and not prop.owner.is_paid_regularly
                and prop.booking_company_id and prop.booking_company.finances_managed_internally
            ):
                property_due = owner_balance_in_range(prop, start, end)
                gross = sum((p['owner_balance'] for _, p in property_due), Decimal('0'))
                ad_hoc_total = AdHocService.objects.filter(
                    property=prop, memo__sent_at__date__range=(start, end),
                ).aggregate(total=Sum('cost'))['total'] or Decimal('0')
                payout_section = {
                    'bookings': property_due,
                    'gross': gross,
                    'ad_hoc_total': ad_hoc_total,
                    'net': gross - ad_hoc_total,
                }

            if memo_section or payout_section:
                sections.append({'property': prop, 'memo_section': memo_section, 'payout_section': payout_section})
        return sections


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsView(View):
    """The booking-listing report - a period + property filter over staff.reports.py::
    booking_report_rows, with every REPORT_COLUMNS figure individually toggleable via GET params
    so the URL itself captures a chosen view (shareable/bookmarkable). Same row-builder as the
    owner-facing Reports tab (owners/views.py::OwnerReportView) - see [[project_klt_web_reporting]]
    in memory for the phased plan this is step one of."""
    template_name = 'staff/reports.html'
    COLUMNS = REPORT_COLUMNS

    def get(self, request, *args, **kwargs):
        today = timezone.now().date()
        start = _parsed_date(request.GET.get('start')) or today.replace(day=1)
        end = _parsed_date(request.GET.get('end')) or _last_day_of_month(today)
        property_id = request.GET.get('property_id', '')
        property = Property.objects.filter(pk=property_id).first() if property_id.isdigit() else None

        # No column params at all (a fresh visit) means "show everything" - only an explicit
        # visit with some columns unchecked should narrow the set, so a bookmarked/shared URL
        # with columns=... behaves the same for everyone who opens it.
        if 'columns' in request.GET:
            selected_columns = set(request.GET.getlist('columns'))
        else:
            selected_columns = {key for key, _label in self.COLUMNS}

        rows = booking_report_rows(start, end, properties=[property] if property else None)

        return render(request, self.template_name, {
            'properties': Property.objects.order_by('title'),
            'property': property,
            'start': start,
            'end': end,
            'columns': self.COLUMNS,
            'selected_columns': selected_columns,
            'rows': rows,
            'totals': report_totals(rows),
            'active_tab': 'bookings',
        })


def _selected_revenue_groups(request):
    """No groups param at all (a fresh visit) means "show everything" - same convention as the
    Bookings tab's own selected_columns, so a bookmarked/shared URL with groups=... behaves the
    same for everyone who opens it. Shared by every Monthly-tab view keyed off REVENUE_GROUPS
    (Revenue, Guest Stays, ...) - Total is never one of the toggle options itself, it's the
    reference point the % columns are computed against, not one of the four sources."""
    if 'groups' in request.GET:
        return set(request.GET.getlist('groups'))
    return set(REVENUE_GROUPS)


def _requested_year(request):
    today = timezone.now().date()
    year_param = request.GET.get('year', '').strip()
    return int(year_param) if year_param.isdigit() else today.year


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsMonthlyView(View):
    """The Monthly tab's Revenue sub-view - Phase 2 of the reporting plan (see
    [[project_klt_web_reporting]] in memory), a business-wide (not per-property) month-by-month
    aggregate mirroring the reference "Monthly Business Report" workbook's own Revenue sheet:
    Total plus a Direct/Airbnb/Booking.com/Vrbo breakdown, each with what guests paid, what
    actually reached KLT after any platform commission, that group's % share of the month, and a
    year-over-year delta - see staff/monthly_reports.py::monthly_revenue_rows() for the full
    calculation. Also renders a "since records began" growth-over-time line chart
    (revenue_trend_rows(), Chart.js) below the year-scoped table - the table stays the
    month-vs-month comparison tool, the chart is the long-run trend view, per Thomas 2026-08-30.
    Revenue and Stays are both built now (see StaffReportsStaysView below); the workbook's other
    7 sheets (All Stays, Bookings, Accounts, Commissions, Management, Extras, Properties) are
    still just the reference file, not yet implemented here."""
    template_name = 'staff/reports_monthly.html'

    def get(self, request, *args, **kwargs):
        year = _requested_year(request)
        selected_groups = _selected_revenue_groups(request)

        return render(request, self.template_name, {
            'year': year,
            'revenue_groups': REVENUE_GROUPS,
            'selected_groups': selected_groups,
            'rows': monthly_revenue_rows(year),
            'trend_rows': revenue_trend_rows(),
            'active_tab': 'monthly',
        })


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsStaysView(View):
    """The Monthly tab's Stays sub-view - same shape as StaffReportsMonthlyView above (Total plus
    Direct/Airbnb/Booking.com/Vrbo, % share, year-over-year delta) but Arrivals/Nights counts
    instead of money, plus an independent "Include owner stays" toggle - one merged sheet
    covering both the reference workbook's Guest Stays and All Stays sheets, per Thomas
    2026-08-30 (originally planned as two separate tabs). See staff/monthly_reports.py::
    monthly_stays_rows() for why the owner toggle behaves differently from the platform ones."""
    template_name = 'staff/reports_stays.html'

    def get(self, request, *args, **kwargs):
        year = _requested_year(request)
        selected_groups = _selected_revenue_groups(request)
        include_owner = request.GET.get('include_owner') == 'on'

        return render(request, self.template_name, {
            'year': year,
            'revenue_groups': REVENUE_GROUPS,
            'selected_groups': selected_groups,
            'include_owner': include_owner,
            'rows': monthly_stays_rows(year, include_owner=include_owner),
            'trend_rows': stays_trend_rows(include_owner=include_owner),
            'active_tab': 'stays',
        })


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsEnquiriesView(View):
    """The Monthly tab's Enquiries sub-view - same shape as StaffReportsMonthlyView/
    StaffReportsStaysView above (Total plus Direct/Airbnb/Booking.com/Vrbo, % share,
    year-over-year delta) but Bookings/Enquiries counts - mirrors the reference workbook's
    Bookings sheet (named "Enquiries" here in the nav instead, to avoid colliding with the
    existing "Bookings" sub-tab - the original booking-listing report - which this isn't a
    variant of). See staff/monthly_reports.py::monthly_bookings_rows() for what distinguishes a
    "booking" from an "enquiry" here."""
    template_name = 'staff/reports_enquiries.html'

    def get(self, request, *args, **kwargs):
        year = _requested_year(request)
        selected_groups = _selected_revenue_groups(request)

        return render(request, self.template_name, {
            'year': year,
            'revenue_groups': REVENUE_GROUPS,
            'selected_groups': selected_groups,
            'rows': monthly_bookings_rows(year),
            'trend_rows': bookings_trend_rows(),
            'active_tab': 'enquiries',
        })


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsExtrasView(View):
    """The Monthly tab's Extras sub-view - Tot + year-over-year Lst Yr only, no Direct/Airbnb/
    Booking.com/Vrbo breakdown or % column (unlike every other Monthly-tab sheet) - six
    independent counts (Airport Transfers, Welcome Packs, Cots, High Chairs, Mid-stay Cleans,
    Late Check-outs) side by side, mirroring the reference workbook's own Extras sheet exactly.
    See staff/monthly_reports.py::monthly_extras_rows()."""
    template_name = 'staff/reports_extras.html'

    def get(self, request, *args, **kwargs):
        year = _requested_year(request)

        return render(request, self.template_name, {
            'year': year,
            'extras_metrics': EXTRAS_METRICS,
            'rows': monthly_extras_rows(year),
            'trend_rows': extras_trend_rows(),
            'active_tab': 'extras',
        })


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsCommissionsView(View):
    """The Monthly tab's Commissions sub-view - same shape as StaffReportsMonthlyView above
    (Total plus Direct/Airbnb/Booking.com/Vrbo, % share, year-over-year delta) but Pre-IVA/
    Post-IVA commission totals - mirrors the reference workbook's Commissions sheet, minus its
    "Maria" column (a legacy second recipient, dropped per Thomas 2026-08-30 - documented
    elsewhere, out of scope here). See staff/monthly_reports.py::monthly_commissions_rows()."""
    template_name = 'staff/reports_commissions.html'

    def get(self, request, *args, **kwargs):
        year = _requested_year(request)
        selected_groups = _selected_revenue_groups(request)

        return render(request, self.template_name, {
            'year': year,
            'revenue_groups': REVENUE_GROUPS,
            'selected_groups': selected_groups,
            'rows': monthly_commissions_rows(year),
            'trend_rows': commissions_trend_rows(),
            'active_tab': 'commissions',
        })


@method_decorator(staff_page_required('can_view_reports'), name='dispatch')
class StaffReportsManagementView(View):
    """The Monthly tab's Management sub-view - same shape as StaffReportsMonthlyView above but
    grouped by property Location rather than Direct/Airbnb/Booking.com/Vrbo (per Thomas
    2026-08-30 - the reference workbook's own Management sheet is per-property instead, switched
    here for a smaller, more stable column set as the property count grows), Cleans/Meet & Greets
    counts. Groups are dynamic (fetched fresh per request via location_groups()), unlike
    REVENUE_GROUPS's fixed tuple, since locations are admin-editable data. See
    staff/monthly_reports.py::monthly_management_rows()/location_groups()."""
    template_name = 'staff/reports_management.html'

    def get(self, request, *args, **kwargs):
        year = _requested_year(request)
        groups = location_groups()
        if 'locations' in request.GET:
            selected_locations = set(request.GET.getlist('locations'))
        else:
            selected_locations = {key for key, _label in groups}

        return render(request, self.template_name, {
            'year': year,
            'location_groups': groups,
            'selected_locations': selected_locations,
            'rows': monthly_management_rows(year, groups),
            'trend_rows': management_trend_rows(groups),
            'active_tab': 'management',
        })
