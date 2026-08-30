from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from availability.utils import get_property_calendar
from bookings.models import (
    Arrival, Booking, Departure, Extra, ExtrasSettings, TravelMethod, WelcomePackDrinksChoice,
    WelcomePackFoodChoice, WelcomePackItem,
)
from bookings.utils import (
    FLIGHT_NUMBER_HINT, create_owner_booking, parsed_arrival_departure_time, parsed_travel_method,
    valid_flight_number,
)
from bookings.views import BookingFormMixin
from finance.models import Memo, PayoutRecord
from owners.permissions import owner_login_required
from properties.models import Property
from staff.models import TaskHistoryEntry
from staff.reports import REPORT_COLUMNS, booking_report_rows, report_totals
from staff.utils import CLOSED_STATUSES, last_day_of_month, parsed_date


class OwnerLoginView(auth_views.LoginView):
    """Owner Suite login - plain Django session auth (see properties.models.Owner.user's own
    docstring for why there's no self-service signup/reset). Rejects a valid username/password
    that isn't actually linked to an Owner (e.g. a staff account) before establishing a session,
    rather than letting them in and then bouncing off every page behind owner_login_required."""
    template_name = 'owners/login.html'
    # Deliberately not redirect_authenticated_user=True: Django's session/auth is shared across
    # this whole project (staff, guests, owners all use the same User model and cookie), so a
    # staff member who's logged in elsewhere and lands here would otherwise get bounced straight
    # to owners:home by Django's own pre-dispatch redirect, which then bounces them straight back
    # via owner_login_required (they have no owner_profile) - an infinite redirect loop. Showing
    # the login form again to an already-authenticated non-owner is harmless; looping isn't.

    def get_success_url(self):
        return self.get_redirect_url() or str(reverse_lazy('owners:home'))

    def form_valid(self, form):
        user = form.get_user()
        if getattr(user, 'owner_profile', None) is None:
            form.add_error(None, "This account isn't linked to an owner.")
            return self.form_invalid(form)
        return super().form_valid(form)


@method_decorator(owner_login_required, name='dispatch')
class OwnerHomeView(View):
    """Owner Suite landing page - a welcome + this owner's own property list, mirroring the shape
    of bookings.views.BookingManageHubView (sidebar + content, same visual language) but scoped to
    an authenticated owner rather than a single booking. See [[project_klt_web_reporting]] in
    memory for the plan this is the first page of."""
    template_name = 'owners/home.html'

    def get(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        properties = Property.objects.filter(owner=owner).select_related('location').order_by('title')
        return render(request, self.template_name, {
            'owner': owner,
            'properties': properties,
            'active_section': 'home',
        })


@method_decorator(owner_login_required, name='dispatch')
class OwnerReportView(View):
    """The owner-facing booking-listing report - same staff.reports.py::booking_report_rows
    row-builder the staff Reports page uses, scoped to this owner's own properties only, with the
    same REPORT_COLUMNS set as staff (per Thomas: "basically everything the owner bookings report
    has"). No further column hiding beyond the property scoping - admin fee is the one figure
    Thomas explicitly didn't want owners to see, and it was never part of this row set to begin
    with (basic_rental is Charge.total_rental, which excludes admin fee by construction - see
    staff/reports.py's own docstring)."""
    template_name = 'owners/reports.html'
    COLUMNS = REPORT_COLUMNS

    def get(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        properties = list(Property.objects.filter(owner=owner).order_by('title'))

        today = timezone.now().date()
        start = parsed_date(request.GET.get('start')) or today.replace(day=1)
        end = parsed_date(request.GET.get('end')) or last_day_of_month(today)
        property_id = request.GET.get('property_id', '')
        selected_property = next((p for p in properties if str(p.pk) == property_id), None)

        if 'columns' in request.GET:
            selected_columns = set(request.GET.getlist('columns'))
        else:
            selected_columns = {key for key, _label in self.COLUMNS}

        rows = booking_report_rows(
            start, end, properties=[selected_property] if selected_property else properties,
        )

        return render(request, self.template_name, {
            'owner': owner,
            'properties': properties,
            'property': selected_property,
            'start': start,
            'end': end,
            'columns': self.COLUMNS,
            'selected_columns': selected_columns,
            'rows': rows,
            'totals': report_totals(rows),
            'active_section': 'reports',
        })


@method_decorator(owner_login_required, name='dispatch')
class OwnerCalendarView(View):
    """The same month-by-month availability grid the public property page shows
    (availability/calendar.html + availability.utils.get_property_calendar - available/
    provisional/booked, no booking-level detail), scoped to a property this owner picks from a
    dropdown - defaults to whichever of their properties has the lowest pk ("first in the
    database", per Thomas 2026-08-30) when there's more than one and none was chosen yet."""
    template_name = 'owners/calendar.html'

    def get(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        properties = list(Property.objects.filter(owner=owner).order_by('pk'))

        property_id = request.GET.get('property_id', '')
        selected_property = next((p for p in properties if str(p.pk) == property_id), None) or (
            properties[0] if properties else None
        )

        return render(request, self.template_name, {
            'owner': owner,
            'properties': properties,
            'property': selected_property,
            'calendar_months': get_property_calendar(selected_property) if selected_property else [],
            'active_section': 'calendar',
        })


def _owner_booking_editable(booking):
    """An owner can only alter/cancel a stay that's still upcoming and not already cancelled -
    same boundary bookings.views._manage_nav_context() uses for the guest hub's own
    show_cancel_booking gate (booking.arrival_date > today), confirmed with Thomas 2026-08-30.
    A past or already-cancelled stay still shows on the My Stays list (read-only) rather than
    disappearing - "consult any dates they have reserved" - only the edit/cancel actions
    themselves are gated."""
    return booking.enquiry_status not in CLOSED_STATUSES and booking.arrival_date > timezone.now().date()


def _owner_action_needed(booking):
    """True when there's something the owner still needs to tell us before this stay - the
    Action-column red flag on My Stays (2026-08-30, per Thomas). Two independent checks:

    1. Travel details missing - nobody's said how this party is actually arriving. Covers both an
       owner-created reservation whose Arrival row was created eagerly-but-blank (see
       create_owner_booking()) and an iCal-imported owner-link booking, which never gets an
       Arrival row at all until someone fills one in - same three fields the legacy
       klt-management-software system's own "still missing" check reads (flight number / time /
       a plain travelling-from note covers the non-flight case too).

    2. Guest details missing, but ONLY when meet_greet is required (defaults True, same as
       clean/meet_greet everywhere else in this app) - name and phone are what Thomas called out
       as crucial ("Crucial for guest info are names and phone number"); email is deliberately
       excluded from this check ("a bonus but lack thereof shouldn't raise a flag")."""
    arrival = getattr(booking, 'arrival', None)
    travel_missing = arrival is None or not (arrival.flight_number or arrival.time or arrival.travelling_from)

    meet_greet_required = arrival.meet_greet if arrival else True
    guest = booking.guest
    guest_missing = meet_greet_required and not (guest.first_name and guest.last_name and guest.phone)

    return travel_missing or guest_missing


@method_decorator(owner_login_required, name='dispatch')
class OwnerBookingsListView(View):
    """My Stays - every is_owner=True booking across this owner's own properties (their own
    direct reservations AND any iCal-imported booking from a link they run themselves - see
    properties.models.iCalLink.is_owner_link's own docstring - deliberately unified into one tab
    rather than split, per Thomas: "keeping all owner stays in one tab... makes it less
    confusing"), split into Upcoming (editable, per _owner_booking_editable) and History (past or
    cancelled, read-only.) Source and a needs-action red flag (see _owner_action_needed) are both
    shown so an owner can tell at a glance which rows are their own vs. imported, and which still
    need something from them."""
    template_name = 'owners/bookings.html'

    def get(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        bookings = list(Booking.objects.filter(
            property__owner=owner, is_owner=True,
        ).select_related('property', 'arrival', 'guest').order_by('-arrival_date'))
        upcoming = sorted(
            (b for b in bookings if _owner_booking_editable(b)), key=lambda b: b.arrival_date,
        )
        history = [b for b in bookings if b not in upcoming]
        upcoming_rows = [
            {'booking': b, 'needs_action': _owner_action_needed(b)} for b in upcoming
        ]
        return render(request, self.template_name, {
            'owner': owner,
            'upcoming_rows': upcoming_rows,
            'history_bookings': history,
            'active_section': 'bookings',
        })


@method_decorator(owner_login_required, name='dispatch')
class OwnerBookingCreateView(View):
    """New reservation - the one genuinely new booking-creation path in this codebase outside the
    paid guest-reservation funnel (bookings/utils.py::create_booking()) and Django admin; see
    create_owner_booking()'s own docstring for how it differs (no pricing/Charge/Payment at all)."""
    template_name = 'owners/booking_create.html'

    def get(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        context = self._context(owner)
        context.update({
            'arrival_date': '', 'departure_date': '', 'adults': '2', 'children': '0', 'babies': '0',
            'clean_checked': True, 'meet_greet_checked': True,
        })
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        post = request.POST
        context = self._context(owner)
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
        property = Property.objects.filter(pk=property_id, owner=owner).first() if property_id.isdigit() else None
        if property is None:
            context['error'] = "Please choose one of your own properties."
            return render(request, self.template_name, context)

        arrival_date = parsed_date(post.get('arrival_date'))
        departure_date = parsed_date(post.get('departure_date'))
        if not arrival_date or not departure_date:
            context['error'] = "Please provide both an arrival and departure date."
            return render(request, self.template_name, context)

        try:
            booking = create_owner_booking(
                property, owner, arrival_date, departure_date,
                adults=_positive_int(post.get('adults'), default=1),
                children=_positive_int(post.get('children'), default=0),
                babies=_positive_int(post.get('babies'), default=0),
                clean=post.get('clean') == 'on', meet_greet=post.get('meet_greet') == 'on',
            )
        except ValidationError as error:
            context['error'] = " ".join(error.messages) if hasattr(error, 'messages') else str(error)
            return render(request, self.template_name, context)

        messages.success(request, "Reservation created.")
        return redirect('owners:booking_detail', reference=booking.reference)

    def _context(self, owner):
        return {
            'owner': owner,
            'properties': Property.objects.filter(owner=owner).order_by('title'),
            'active_section': 'bookings',
        }


def _positive_int(raw, default):
    raw = (raw or '').strip()
    return int(raw) if raw.isdigit() else default


@method_decorator(owner_login_required, name='dispatch')
class OwnerBookingDetailView(BookingFormMixin, View):
    """A single owner-stay booking - dates, cancel, Arrival & Departure (including the
    clean/meet & greet toggles Thomas asked for), and Extras all on one page, each its own POST
    `action`, mirroring StaffBookingDetailView's own per-panel-POST convention. Unlike the guest
    Manage Booking hub, meet_greet/clean ARE owner-editable here (not staff/ops-only) - matches
    StaffBookingDetailView._update_booking's own existing `if booking.is_owner:` guard on those
    same two fields, which already anticipated exactly this case.

    BookingFormMixin (bookings/views.py) supplies the Airport Transfers/Late Checkout parsing and
    persistence used below - those are fully self-contained per concern, safe to reuse as-is. Its
    _save_extras()/_extras_context() are NOT reused directly though - see _save_owner_extras()'s
    own docstring for why."""
    template_name = 'owners/booking_detail.html'

    def _get_booking(self, request, reference):
        owner = request.user.owner_profile
        return get_object_or_404(Booking, reference=reference, is_owner=True, property__owner=owner)

    def get(self, request, reference, *args, **kwargs):
        booking = self._get_booking(request, reference)
        return render(request, self.template_name, self._context(booking))

    def post(self, request, reference, *args, **kwargs):
        booking = self._get_booking(request, reference)
        if not _owner_booking_editable(booking):
            messages.error(request, "This stay can no longer be changed.")
            return redirect('owners:booking_detail', reference=booking.reference)
        handler = {
            'update_dates': self._update_dates,
            'update_guests': self._update_guests,
            'save_arrival_departure': self._save_arrival_departure,
            'update_extras': self._update_extras,
            'cancel': self._cancel,
        }.get(request.POST.get('action'))
        if handler is not None:
            handler(request, booking)
        return redirect('owners:booking_detail', reference=booking.reference)

    def _context(self, booking):
        arrival = getattr(booking, 'arrival', None)
        departure = getattr(booking, 'departure', None)
        context = {
            'owner': booking.property.owner,
            'booking': booking,
            'editable': _owner_booking_editable(booking),
            'arrival': arrival,
            'departure': departure,
            'arrival_meet_greet': arrival.meet_greet if arrival else True,
            'departure_clean': departure.clean if departure else True,
            # Airport Transfers are Faro-only (see that section's own heading) - showing it at
            # all only makes sense once a Faro flight has actually been selected for this stay,
            # in either direction, per Thomas 2026-08-30.
            'show_airport_transfers': (
                (arrival is not None and arrival.method == TravelMethod.FLIGHT_FARO)
                or (departure is not None and departure.method == TravelMethod.FLIGHT_FARO)
            ),
            'arrival_travel_methods': TravelMethod.choices,
            'departure_travel_methods': TravelMethod.departure_choices(),
            'active_section': 'bookings',
        }
        context.update(self._owner_extras_context(booking))
        context.update(self._transfer_context(booking))
        return context

    def _owner_extras_context(self, booking, post_data=None):
        """Trimmed mirror of BookingFormMixin._extras_context() - Cot & High Chair, Welcome Pack
        and Late Checkout only, the three Extra fields this page's form actually presents (per
        Thomas 2026-08-30's explicit ask - alongside Airport Transfers, handled separately by
        the mixin's own _transfer_context()). Deliberately excludes Mid-stay Clean and Special
        Requests - see _save_owner_extras() for why those two are left alone entirely rather than
        just hidden from this form."""
        if post_data is not None:
            welcome_pack = post_data.get('welcome_pack') == 'on'
            welcome_pack_food = post_data.get('welcome_pack_food') or WelcomePackFoodChoice.STANDARD
            welcome_pack_drinks = post_data.get('welcome_pack_drinks') or WelcomePackDrinksChoice.ALCOHOLIC
            welcome_pack_note = post_data.get('welcome_pack_note', '').strip()
            cot = post_data.get('cot') == 'on'
            high_chair = post_data.get('high_chair') == 'on'
            late_checkout = post_data.get('late_checkout') == 'on'
            late_checkout_time = post_data.get('late_checkout_time', '').strip()
            owner_is_paying = post_data.get('owner_is_paying') == 'on'
        else:
            extra = getattr(booking, 'extras', None)
            welcome_pack = bool(extra and extra.welcome_pack)
            welcome_pack_food = (extra.welcome_pack_food if extra and extra.welcome_pack_food
                                  else WelcomePackFoodChoice.STANDARD)
            welcome_pack_drinks = (extra.welcome_pack_drinks if extra and extra.welcome_pack_drinks
                                    else WelcomePackDrinksChoice.ALCOHOLIC)
            welcome_pack_note = extra.welcome_pack_note if extra and extra.welcome_pack_note else ''
            cot = bool(extra and extra.cot)
            high_chair = bool(extra and extra.high_chair)
            late_checkout = bool(extra and extra.late_checkout)
            late_checkout_time = (
                extra.late_checkout_time.strftime('%H:%M') if extra and extra.late_checkout_time else ''
            )
            owner_is_paying = bool(extra and extra.owner_is_paying)

        arrival = getattr(booking, 'arrival', None)
        settings = ExtrasSettings.load()
        nights = (booking.departure_date - booking.arrival_date).days
        return {
            'owner_is_paying': owner_is_paying,
            # Extra.owner_is_paying is a whole-booking flag (see its own model docstring), but
            # it's only ever meaningful to raise while a meet & greet is actually happening - per
            # Thomas 2026-08-30, gated on the same Arrival.meet_greet the Arrival & Departure
            # panel above already shows/saves.
            'show_owner_is_paying': bool(arrival is None or arrival.meet_greet),
            'welcome_pack_items': WelcomePackItem.objects.filter(active=True),
            'welcome_pack': welcome_pack,
            'welcome_pack_food': welcome_pack_food,
            'welcome_pack_drinks': welcome_pack_drinks,
            'welcome_pack_note': welcome_pack_note,
            'welcome_pack_price': settings.welcome_pack_price,
            'cot': cot,
            'high_chair': high_chair,
            # Guest-side Cot & High Chair gates on a typed BookingGuest age (see
            # BookingFormMixin._any_infant_age) - owner bookings have no guest-list party rows at
            # all, so "if infants > 0" here means Booking.babies, the headcount the owner
            # themselves set when reserving (or the original search picker, for an iCal-imported
            # stay) - the only infant signal that actually exists on this kind of booking.
            'show_cot_high_chair': booking.babies > 0,
            'cot_high_chair_pricing_config': {
                'nights': nights,
                'cot_short': str(settings.cot_price_short_stay),
                'cot_long': str(settings.cot_price_long_stay),
                'high_chair_short': str(settings.high_chair_price_short_stay),
                'high_chair_long': str(settings.high_chair_price_long_stay),
                'combo_discount_percent': str(settings.cot_and_high_chair_combo_discount_percent),
            },
            'late_checkout': late_checkout,
            'late_checkout_time': late_checkout_time,
            'late_checkout_price': settings.late_checkout_price,
        }

    def _save_owner_extras(self, booking, post_data):
        """Trimmed mirror of BookingFormMixin._save_extras() - only touches the Extra fields
        _owner_extras_context() above actually presents (welcome pack, cot/high chair, late
        checkout, owner_is_paying). Deliberately does NOT delete/rebuild booking.requested_extras
        or touch Extra.mid_stay_clean* the way the full _save_extras() does - Mid-stay Clean and
        Special Requests live on a different form entirely (the guest-facing Manage Booking hub's
        own Extras page), which this owner page doesn't show or know about. Reusing
        BookingFormMixin._save_extras() as-is here would silently wipe both out on every owner
        save (its unconditional requested_extras.all().delete() plus treating a missing
        mid_stay_clean checkbox as "guest unticked it") - a real data-loss risk, not a
        hypothetical one, so this stays a separate, narrower method instead."""
        extra, _ = Extra.objects.get_or_create(booking=booking)
        settings = ExtrasSettings.load()

        # owner_is_paying only exists as a concept while a meet & greet is actually happening
        # (see _owner_extras_context()'s show_owner_is_paying) - reading it here regardless would
        # silently reset a previously-set True back to False on every Extras save made after
        # meet & greet was later switched off elsewhere on the page, since the checkbox wouldn't
        # even be rendered (and so never posted) at that point. Left untouched, not reset, when
        # not applicable - same convention as every other conditionally-shown field on this page.
        arrival = getattr(booking, 'arrival', None)
        if arrival is None or arrival.meet_greet:
            extra.owner_is_paying = post_data.get('owner_is_paying') == 'on'
        extra.welcome_pack = post_data.get('welcome_pack') == 'on'
        if extra.welcome_pack:
            food = post_data.get('welcome_pack_food', '')
            extra.welcome_pack_food = food if food in WelcomePackFoodChoice.values else WelcomePackFoodChoice.STANDARD
            drinks = post_data.get('welcome_pack_drinks', '')
            extra.welcome_pack_drinks = (
                drinks if drinks in WelcomePackDrinksChoice.values else WelcomePackDrinksChoice.ALCOHOLIC
            )
            extra.welcome_pack_note = post_data.get('welcome_pack_note', '').strip()
            extra.welcome_pack_charge = settings.welcome_pack_price
        else:
            extra.welcome_pack_food = None
            extra.welcome_pack_drinks = None
            extra.welcome_pack_note = ''
            extra.welcome_pack_charge = None

        extra.cot = post_data.get('cot') == 'on'
        extra.high_chair = post_data.get('high_chair') == 'on'
        nights = (booking.departure_date - booking.arrival_date).days
        extra.cot_high_chair_charge = settings.compute_cot_high_chair_price(nights, extra.cot, extra.high_chair)

        extra.late_checkout, extra.late_checkout_time, _ = self._parse_late_checkout(post_data)
        extra.late_checkout_charge = settings.late_checkout_price if extra.late_checkout else None

        extra.save(update_fields=[
            'welcome_pack', 'welcome_pack_food', 'welcome_pack_drinks', 'welcome_pack_note', 'welcome_pack_charge',
            'cot', 'high_chair', 'cot_high_chair_charge',
            'late_checkout', 'late_checkout_time', 'late_checkout_charge',
            'owner_is_paying',
        ])

    def _update_extras(self, request, booking):
        transfer_rows, transfer_non_field_error = self._parse_transfer_rows(request.POST)
        _, _, late_checkout_error = self._parse_late_checkout(request.POST)
        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            messages.error(request, transfer_non_field_error or "Please fix the airport transfer details below.")
            return
        if late_checkout_error:
            messages.error(request, late_checkout_error)
            return

        with transaction.atomic():
            self._save_owner_extras(booking, request.POST)
            self._save_transfers(booking, transfer_rows)
        messages.success(request, "Extras saved.")

    def _update_dates(self, request, booking):
        arrival_date = parsed_date(request.POST.get('arrival_date'))
        departure_date = parsed_date(request.POST.get('departure_date'))
        if not arrival_date or not departure_date:
            messages.error(request, "Please provide both an arrival and departure date.")
            return
        if arrival_date < timezone.now().date():
            messages.error(request, "Arrival date can't be in the past.")
            return

        dates_before = (booking.arrival_date, booking.departure_date)
        booking.arrival_date = arrival_date
        booking.departure_date = departure_date
        try:
            booking.full_clean()
        except ValidationError as error:
            booking.arrival_date, booking.departure_date = dates_before
            messages.error(request, " ".join(error.messages) if hasattr(error, 'messages') else str(error))
            return

        booking.last_updated = timezone.now()
        booking.save(update_fields=['arrival_date', 'departure_date', 'last_updated'])
        if dates_before != (booking.arrival_date, booking.departure_date):
            TaskHistoryEntry.objects.create(
                booking=booking, description="Booking dates updated",
                detail=f"Arrival {dates_before[0]} → {booking.arrival_date}, "
                       f"Departure {dates_before[1]} → {booking.departure_date} (by owner)",
                created_by=request.user,
            )
        messages.success(request, "Dates updated.")

    def _update_guests(self, request, booking):
        post = request.POST
        try:
            adults = int(post.get('adults', ''))
            children = int(post.get('children', ''))
            babies = int(post.get('babies', ''))
        except (TypeError, ValueError):
            messages.error(request, "Please enter valid guest numbers.")
            return
        if adults < 1 or children < 0 or babies < 0:
            messages.error(request, "Please enter valid guest numbers.")
            return

        counts_before = (booking.adults, booking.children, booking.babies)
        booking.adults = adults
        booking.children = children
        booking.babies = babies
        try:
            # Also catches exceeding the property's max_guests - see Booking.clean()'s own
            # adults+children+babies check, same validation _update_dates() above relies on.
            booking.full_clean()
        except ValidationError as error:
            booking.adults, booking.children, booking.babies = counts_before
            messages.error(request, " ".join(error.messages) if hasattr(error, 'messages') else str(error))
            return

        booking.last_updated = timezone.now()
        booking.save(update_fields=['adults', 'children', 'babies', 'last_updated'])
        messages.success(request, "Guest numbers updated.")

    def _save_arrival_departure(self, request, booking):
        post = request.POST
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

        arrival, _ = Arrival.objects.get_or_create(
            booking=booking, defaults={'self_check_in': False, 'meet_greet': True},
        )
        arrival.method = arrival_method
        arrival.flight_number = arrival_flight_number
        arrival.travelling_from = post.get('arrival_travelling_from', '').strip()
        arrival.hiring_car = post.get('arrival_hiring_car') == 'on'
        arrival.time = parsed_arrival_departure_time(post.get('arrival_time'))
        arrival.details = post.get('arrival_details', '').strip()[:140]
        # Unlike the guest-facing Manage Booking hub (which never lets a guest touch this),
        # meet_greet IS owner-editable here - see this view's own docstring.
        arrival.meet_greet = post.get('meet_greet') == 'on'
        arrival.save()

        departure, _ = Departure.objects.get_or_create(booking=booking, defaults={'clean': True})
        departure.method = departure_method
        departure.flight_number = departure_flight_number
        departure.travelling_from = post.get('departure_travelling_from', '').strip()
        departure.time = parsed_arrival_departure_time(post.get('departure_time'))
        departure.details = post.get('departure_details', '').strip()[:140]
        departure.clean = post.get('clean') == 'on'
        departure.save()

        # Only touched while Meet & Greet is actually ticked - the Guest Details panel is hidden
        # AND disabled client-side when it isn't (owners/static/owners/booking_detail.js), so a
        # disabled field is omitted from the POST body entirely per the HTML form spec. Without
        # this guard, unticking Meet & Greet and saving would silently blank out whatever guest
        # details were already on record - same class of bug staff/views.py::_update_booking's
        # own is_owner guard on this exact pair of fields was written to avoid.
        if arrival.meet_greet:
            guest = booking.guest
            guest.first_name = post.get('guest_first_name', '').strip()
            guest.last_name = post.get('guest_last_name', '').strip() or guest.last_name
            guest.phone = post.get('guest_phone', '').strip()
            guest.email = post.get('guest_email', '').strip()
            guest.save()

        messages.success(request, "Arrival & departure details saved.")

    def _cancel(self, request, booking):
        old_status = booking.enquiry_status
        booking.enquiry_status = 'Cancelled by owner'
        booking.save(update_fields=['enquiry_status'])
        TaskHistoryEntry.objects.create(
            booking=booking, description="Status changed",
            detail=f"From '{old_status}' to 'Cancelled by owner'", created_by=request.user,
        )
        messages.success(request, "Stay cancelled.")


@method_decorator(owner_login_required, name='dispatch')
class OwnerPayoutsMemosView(View):
    """Payouts & Memos - a unified, reverse-chronological ledger of every finance.models.
    PayoutRecord (staff marked a booking's rental payout as paid, on the staff Payouts tab) and
    Memo (staff clicked Send on a cleaning-fee memo, on the staff Memos tab) against this owner's
    own properties, per Thomas 2026-08-30. Payouts are money coming TO the owner (black); Memos
    are a charge deducted FROM the owner (the same dark red as Reports' deduction columns -
    .owner-table-deduction, reused here).

    Both models are strictly one row per booking (see PayoutRecord/Memo's own docstrings - there
    is no batch/period payout entity anywhere in this codebase), so "view more" always resolves
    to exactly one real booking either way. It differs by row type and, for Payouts, by
    Owner.is_paid_regularly - see _payout_detail_url()'s own docstring for why."""
    template_name = 'owners/payouts_memos.html'

    def get(self, request, *args, **kwargs):
        owner = request.user.owner_profile
        payout_records = PayoutRecord.objects.filter(
            booking__property__owner=owner,
        ).select_related('booking', 'booking__property')
        memos = Memo.objects.filter(
            property__owner=owner, sent_at__isnull=False,
        ).select_related('property', 'cleaning_task__booking')

        rows = []
        for record in payout_records:
            rows.append({
                'type': 'Payout',
                'property': record.booking.property,
                'reference': record.booking.reference,
                'date': record.paid_at,
                'amount': record.amount,
                'is_charge': False,
                'detail_url': self._payout_detail_url(owner, record.booking),
            })
        for memo in memos:
            booking = memo.cleaning_task.booking if memo.cleaning_task else None
            rows.append({
                'type': 'Memo',
                'property': memo.property,
                'reference': booking.reference if booking else '—',
                'date': memo.sent_at,
                'amount': memo.total(),
                'is_charge': True,
                'detail_url': reverse('owners:memo_detail', kwargs={'pk': memo.pk}),
            })
        rows.sort(key=lambda row: row['date'], reverse=True)

        return render(request, self.template_name, {
            'owner': owner,
            'rows': rows,
            'active_section': 'payouts_memos',
        })

    def _payout_detail_url(self, owner, booking):
        """Regularly-paid owners (Owner.is_paid_regularly) are paid individually per booking -
        bookings/payouts.py::_due_date() gives each one its own due date (arrival + a fixed
        number of days) with no natural grouping - so "view more" scopes Reports to just that
        one booking's own dates. Every other owner is effectively settled in a monthly batch
        instead (due date = last day of the arrival month, and staff's own Statement view
        aggregates a non-regular owner's payout by calendar month - see
        StaffFinanceStatementView._sections()'s payout_section) even though the underlying
        PayoutRecord is still one row per booking - so "view more" scopes Reports to that whole
        month instead, matching what the payout actually represents."""
        if owner.is_paid_regularly:
            start, end = booking.arrival_date, booking.departure_date
        else:
            start = booking.arrival_date.replace(day=1)
            end = last_day_of_month(booking.arrival_date)
        return (
            f"{reverse('owners:reports')}?property_id={booking.property_id}"
            f"&start={start.isoformat()}&end={end.isoformat()}"
        )


@method_decorator(owner_login_required, name='dispatch')
class OwnerMemoDetailView(View):
    """Trimmed, owner-facing mirror of staff/views.py::StaffFinanceMemoDetailView - same content
    (Clean/Meet & Greet/Ad-hoc lines, total, sent status), scoped strictly to this owner's own
    properties and to already-sent memos only (sent_at__isnull=False) - an owner has no business
    seeing a still-editable, not-yet-final memo even by guessing a pk, and there's no Send button
    here at all, since dispatching a memo is staff's own action."""
    template_name = 'owners/memo_detail.html'

    def get(self, request, pk, *args, **kwargs):
        owner = request.user.owner_profile
        memo = Memo.objects.select_related('property', 'cleaning_task__booking', 'sent_by').filter(
            pk=pk, property__owner=owner, sent_at__isnull=False,
        ).first()
        if memo is None:
            raise Http404("No memo found.")
        return render(request, self.template_name, {
            'owner': owner,
            'memo': memo,
            'ad_hoc_services': memo.ad_hoc_services.order_by('date'),
            'active_section': 'payouts_memos',
        })
