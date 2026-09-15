import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django_countries import countries

import env_settings
from bookings.forms import BookingLookupForm, GuestContactDetailsForm
from bookings.models import (
    AirportTransfer, AirportTransferDirection, Arrival, BalancePayment,
    Booking, BookingCondition, BookingGuest, BookingRequestedExtra, BookingSettings, DepositBankDetails,
    Departure, Extra, ExtrasSettings, FAQ, GuestListAdjustment, GuestRegistration, LocalGuideEntry, RequestType,
    ReservationGroup, SupplementaryPayment, TouristTax, TravelMethod, WelcomePackDrinksChoice, WelcomePackFoodChoice,
    WelcomePackItem,
)
from bookings.utils import (
    FLIGHT_NUMBER_HINT, append_guest_rows, booking_confirmation_context, cancel_booking_hold,
    compute_effective_self_check_in, compute_eta_from_given_time, compute_initial_hold_expiry,
    compute_tourist_tax, extras_request_windows, tourist_tax_in_season,
    determine_payment_provider, extras_summary, guest_counts_by_age, mid_stay_clean_window,
    parsed_arrival_departure_time, parsed_travel_method, recalculate_balance_for_party,
    recalculate_costs_for_dates, recalculate_costs_for_party, reservation_retry_url,
    resolve_shared_postbox_path, valid_flight_number,
)
from availability.utils import date_string_to_date, get_property_calendar
from libraries.banking.revolut import Revolut
from libraries.phone_country_codes import split_phone

MAX_GUEST_AGE = 120


def is_paid(booking):
    """A booking with no Payment row at all predates this feature or was platform-synced - never
    part of the deposit-payment flow, so treat it as paid (i.e. don't gate it)."""
    payment = getattr(booking, 'payment', None)
    return payment is None or payment.status == 'paid'


def next_unpaid_sibling_reference(booking):
    """If `booking` belongs to a ReservationGroup (see bookings/models.py - a multi-property
    reservation, 2026-09-13) and a sibling Booking in the same group hasn't been paid yet, that
    sibling's own reference - used once `booking` itself is confirmed paid, to route the guest
    straight into paying for their other apartment next instead of the normal single-booking
    confirmation page. None if there's no group, or every sibling is already paid (time for the
    normal confirmation) - so this is a safe no-op for every booking that predates grouping."""
    if not booking.reservation_group_id:
        return None
    sibling = booking.reservation_group.bookings.exclude(pk=booking.pk).first()
    if sibling is None or is_paid(sibling):
        return None
    return sibling.reference


def redirect_to_next_step_after_payment(request, booking):
    """Where the guest goes once `booking` itself is confirmed paid - the sibling leg's own
    BookingDetailsView (updating the session's pending_booking_reference to match, the same way
    properties/views.py::ReserveView.post()/MultiPropertyReserveView.post() first set it, so that
    view's own gate and BookingPaymentCancelView keep working completely unmodified against
    whichever leg is currently active) if one's still unpaid, otherwise the normal confirmation.
    Used by BookingDetailsView.get() and BookingPaymentView.get(); BookingConfirmationView.get()
    has its own near-identical check since its else-branch renders in place rather than redirecting."""
    next_reference = next_unpaid_sibling_reference(booking)
    if next_reference:
        request.session['pending_booking_reference'] = next_reference
        return redirect('bookings:details', reference=next_reference)
    return redirect('bookings:confirmation', reference=booking.reference)


def booking_for_reference_and_email(reference, email):
    """The Booking a guest's reference+email lookup (ManageBookingView) should resolve to -
    2026-09-13, wiring up the shared reference a multi-property reservation's ReservationGroup
    generates (see that model's own docstring: a guest is meant to use this ONE reference for
    either apartment, not have to remember two individual Booking.reference values).

    `reference` matches an individual Booking directly in the normal case. Failing that, it might
    be a ReservationGroup's own reference instead - in which case this returns the first still-
    unpaid Booking in the group for that guest, or (once every leg is paid) simply the first one by
    pk. Never the first Booking regardless of paid status - a guest who still owes for one
    apartment must never be routed straight to a fully-paid-looking hub for the other and left
    thinking their whole reservation is done. Returns None if nothing matches either way."""
    booking = Booking.objects.filter(reference=reference, guest__email__iexact=email).first()
    if booking is not None:
        return booking
    group = ReservationGroup.objects.filter(reference=reference).first()
    if group is None:
        return None
    bookings = list(group.bookings.filter(guest__email__iexact=email).order_by('pk'))
    if not bookings:
        return None
    return next((candidate for candidate in bookings if not is_paid(candidate)), bookings[0])


def _first_unpaid_leg(bookings):
    """The first still-unpaid leg of a stay, or None once every leg is paid - the "go pay first"
    gate every Manage Booking hub section shares (BookingManageHubView and each Holiday Info
    section view), now checked across every leg of a multi-property stay rather than just one."""
    return next((booking for booking in bookings if not is_paid(booking)), None)


def merged_stay_redirect(request, bookings):
    """Move a guest who reached a merged hub section via ONE apartment's own reference onto the
    stay's shared reference instead - or None when they're already in the right place.

    bookings_for_stay_reference() happily resolves an individual leg's reference (it has to: that's
    the normal single-property case), which meant every merged section stayed fully reachable at a
    leg's own URL and rendered there in SINGLE-apartment mode - showing a guest a hub covering half
    their stay. Old bookmarks, pre-merge confirmation emails and forwarded links all land that way,
    so this isn't hypothetical (2026-09-15, per Thomas: reaching a per-apartment hub at all is not
    the intended effect of the merge).

    Preserves the query string, so a post-save redirect that carries e.g. ?guests_saved=<ref>
    through this still lands with its confirmation note intact. Uses the resolved view name, so it
    returns the guest to the SAME section rather than dumping them on the hub landing page.

    Only ever applies to a leg that genuinely belongs to a ReservationGroup with a reference - a
    normal single-property booking is its own whole stay and is left completely alone."""
    if len(bookings) != 1:
        return None
    booking = bookings[0]
    if not booking.reservation_group_id:
        return None
    group_reference = booking.reservation_group.reference
    if not group_reference or group_reference == booking.reference:
        return None
    url = reverse(request.resolver_match.view_name, kwargs={'reference': group_reference})
    query = request.META.get('QUERY_STRING', '')
    return redirect(f"{url}?{query}" if query else url)


def resolve_stay(request, reference):
    """(bookings, redirect_or_None) for a merged hub section - bookings_for_stay_reference() plus
    the two checks every one of those sections needs before doing anything else: 404 if the
    reference matches nothing at all, and redirect to the shared reference if the guest arrived via
    a single leg of a grouped stay (see merged_stay_redirect()).

    Deliberately NOT used by the genuinely per-apartment views (Edit Dates, Pay Balance, the
    deposit/balance checkouts, supplementary payments) - those act on one booking's own calendar
    slot or charge and are supposed to be reached by its own reference."""
    bookings = bookings_for_stay_reference(reference)
    if not bookings:
        raise Http404("No booking found for this reference.")
    return bookings, merged_stay_redirect(request, bookings)


def _stay_transfers(booking):
    """Every AirportTransfer belonging to the STAY `booking` is part of, not just that one leg.

    A multi-property party's airport transfers are stored against a single leg on purpose - they
    are one transfer for the whole party, and duplicating the row would double-charge the guest
    via extras_summary() and double-count the staff monthly report (see
    BookingManageExtrasView's docstring). So the places that read transfers to decide what a guest
    is *told* have to look across the stay, otherwise the apartment that doesn't hold the row
    would tell its guest no transfer is booked while the other says there is one.

    Unchanged for a single-property booking, which is its own whole stay."""
    if not booking.reservation_group_id:
        return booking.airport_transfers.all()
    return AirportTransfer.objects.filter(booking__reservation_group_id=booking.reservation_group_id)


def _leg_for_post(bookings, leg_reference):
    """Which apartment a merged multi-property form's POST is acting on, from the hidden
    `leg_reference` field every duplicated form carries (see Stage D's dual-form pattern).

    A single-property stay ignores the field entirely and returns its only booking - those forms
    still render it, but there's nothing to disambiguate and no reason to start rejecting a POST
    that predates it. A multi-property stay must match an actual leg: returns None for a missing
    or unrecognised reference rather than silently falling back to the primary leg, which would
    write one apartment's guest list onto the other."""
    if len(bookings) == 1:
        return bookings[0]
    return next((booking for booking in bookings if booking.reference == leg_reference), None)


def _sweep_supplementary_payments(bookings):
    """Apply any paid-but-not-yet-applied SupplementaryPayment across every leg of a stay - a
    guest who paid for a guest-addition on their SECOND apartment and closed the tab needs it
    applied next time they load the page, not just when the primary leg happens to be the one
    that was paid for. _manage_nav_context() does the same for its single `booking`; this is the
    every-leg version the merged sections need (see _manage_hub_context(), which already had its
    own copy of this loop for the hub landing page)."""
    for booking in bookings:
        for payment in booking.supplementary_payments.filter(status='paid', applied_at__isnull=True):
            payment.apply(booking=booking)


def _all_equal(values):
    """True if every value is equal to the first (an empty/single-item iterable trivially counts
    as equal). Used to decide whether a subsection genuinely differs between a multi-property
    stay's legs - if not, it renders once with no per-apartment label at all rather than
    repeating identical content under each property's own heading (2026-09-14, per Thomas: two
    apartments in the same building are often furnished/managed identically, and duplicating
    identical content per apartment reads as noise, not useful distinction). Callers pass a
    generator of hashable/comparable keys (tuples of plain values, not model instances or
    querysets directly - see each call site for how it builds one)."""
    values = list(values)
    return all(value == values[0] for value in values)


def bookings_for_stay_reference(reference):
    """Every Booking making up "the stay" `reference` points at - a list of one for a normal
    single-property reference (unchanged, the overwhelming majority), or every sibling leg (query-
    ordered by pk, stable/deterministic - the same ordering ReservationGroup's own docstring and
    booking_for_reference_and_email() above already use) for a ReservationGroup's own shared
    reference. No email check here (unlike booking_for_reference_and_email) - this is for
    bearer-readable-by-reference views (BookingManageHubView et al), same trust model every other
    post-deposit view in this file already uses. Empty list, never None, if nothing matches either
    way - callers 404 on that, same as a plain failed Booking lookup would.

    select_related is deliberately wide - every relation any merged hub section currently reads
    off a leg (is_paid's payment/charges/balance_payment, the Holiday Info sections' property__
    booking_company/cleaning_company/amenities/location, Arrival & Departure's arrival/departure) -
    caught 2026-09-14 when a guest reported a "very long" save on Arrival & Departure: this
    project's remote Postgres has real, noticeable per-round-trip latency (see project memory on
    preferring bulk DB ops here), so a 2-leg stay whose views each lazily fetch half a dozen
    relations per leg turns into a dozen-plus sequential round trips instead of the one JOINed
    query below. A single-property stay (a list of one) pays the same one query either way, so
    this is free for the common case, not just a multi-property optimization."""
    relations = (
        'property__booking_company', 'property__cleaning_company', 'property__amenities',
        'property__location', 'guest', 'payment', 'charges', 'balance_payment', 'arrival', 'departure',
    )
    group = ReservationGroup.objects.filter(reference=reference).first()
    if group is not None:
        return list(group.bookings.select_related(*relations).order_by('pk'))
    booking = Booking.objects.filter(reference=reference).select_related(*relations).first()
    return [booking] if booking is not None else []


def is_balance_paid(booking):
    """A booking with no BalancePayment row at all is either collapsed (paid in full at deposit
    time - see BookingSettings.compute_costs()) or predates this feature - either way there's
    nothing left to collect, so treat it as paid the same way is_paid() does for a missing Payment."""
    balance_payment = getattr(booking, 'balance_payment', None)
    return balance_payment is None or balance_payment.status == 'paid'


def is_tourist_tax_paid(booking):
    """No TouristTax row yet just means the guest hasn't visited that Manage hub section yet
    (lazily created there, unlike Payment/BalancePayment which are always created at booking
    time) - not the same as "nothing owed", so this only reflects an existing row's status."""
    tourist_tax = getattr(booking, 'tourist_tax', None)
    return tourist_tax is not None and tourist_tax.status == 'paid'


def is_fully_paid(booking):
    """Deposit paid, and (no balance stage at all, or the balance is paid too) - the single switch
    the Manage Booking hub uses to decide whether a guest-list edit still goes through the existing
    pre-balance-paid flows (BookingDetailsView/BookingBalanceDetailsView, which can still reprice
    Charge) or the add/remove, cash-at-check-in GuestListAdjustment flow instead (Charge is
    frozen for good by this point - removals never refund anything already paid, only additions
    can add a cash charge). is_balance_paid() alone is NOT sufficient here - it returns
    True for a still-unpaid collapsed booking too (no BalancePayment row exists at all), so it must
    always be combined with is_paid() first."""
    return is_paid(booking) and is_balance_paid(booking)


def extras_edit_locked(booking):
    """Whether EVERY extra has closed for this booking - i.e. there's nothing left the guest can
    change online at all. Per-extra cutoffs are the real gate now (2026-09-08, see
    bookings/utils.py::extras_request_windows); this just answers "is the whole page read-only",
    which is what the page-level copy and the Save button still need to know.

    Unrelated to payment status either way: Extras are cash-at-check-in and were never priced into
    Charge (see extras_summary()'s docstring), so there's nothing here for payment state to gate."""
    windows = extras_request_windows(booking)
    return not any(windows[slug] for slug in windows if slug != 'request_types') \
        and not any(windows['request_types'].values())


def is_cancelled(booking):
    """Guest self-service cancellation status - see BookingCancelView. Reuses the exact string
    cancel_booking_hold() already uses for cancelling a not-yet-paid hold, since it's semantically
    identical ("the guest cancelled") regardless of whether a deposit had been paid yet - that
    older helper is a no-op on anything already paid, so there's no overlap between the two."""
    return booking.enquiry_status == 'Cancelled by guest'


class BookingConfirmationView(View):
    """Landing page after a successful reservation - looked up by reference alone (a bearer link,
    like a checkout confirmation), not requiring the email too."""
    template_name = 'bookings/confirmation.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return redirect('bookings:pay', reference=reference)
        # Own near-identical check to redirect_to_next_step_after_payment() rather than reusing it
        # directly - that helper always redirects, which would loop this view back onto itself
        # once every leg is paid (next_reference None); here that case renders in place instead.
        next_reference = next_unpaid_sibling_reference(booking)
        if next_reference:
            request.session['pending_booking_reference'] = next_reference
            return redirect('bookings:details', reference=next_reference)
        return render(request, self.template_name, booking_confirmation_context(booking))


class BookingFormMixin:
    """Extras section logic (Welcome Pack, Cot/High Chair, Late Checkout, Airport Transfers,
    RequestType rows) shared between BookingDetailsView (the collapsed-booking case, where Extras
    are chosen alongside the deposit) and BookingBalanceDetailsView (the two-stage case, where
    Extras move to the balance stage instead - see BalancePayment's docstring). All cash-at-checkin
    - never touches Charge/Payment/BalancePayment."""

    def _save_extras(self, booking, post_data):
        """Welcome Pack + RequestType selections are cash-at-checkin (see the plan this was built
        from) so, unlike the guest list, they never touch Charge/Payment - just persisted as-is.
        The pack's food/drinks choices are only meaningful (and only stored) when welcome_pack is
        actually wanted - a fixed pair of picks, not a freeform swap request (see the memory this
        was rebuilt from after the first version's freeform text field turned out to invite too
        much back-and-forth for a two-person operation)."""
        extra, _ = Extra.objects.get_or_create(booking=booking)
        settings = ExtrasSettings.load()
        # An extra past its own cutoff keeps whatever is already stored - the form doesn't render
        # its inputs at all by then, so a POST that omits them must not be read as "the guest
        # unticked it" (2026-09-08, see bookings/utils.py::extras_request_windows).
        windows = extras_request_windows(booking)
        update_fields = []

        if windows['welcome_pack']:
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
            update_fields += [
                'welcome_pack', 'welcome_pack_food', 'welcome_pack_drinks', 'welcome_pack_note',
                'welcome_pack_charge',
            ]

        if windows['cot_high_chair']:
            extra.cot = post_data.get('cot') == 'on'
            extra.high_chair = post_data.get('high_chair') == 'on'
            nights = (booking.departure_date - booking.arrival_date).days
            extra.cot_high_chair_charge = settings.compute_cot_high_chair_price(nights, extra.cot, extra.high_chair)
            update_fields += ['cot', 'high_chair', 'cot_high_chair_charge']

        if windows['late_checkout']:
            wants_late_checkout, requested_time, _ = self._parse_late_checkout(booking, post_data)
            extra.late_checkout, extra.late_checkout_time = self._apply_late_checkout_request(
                booking, wants_late_checkout, requested_time,
            )
            extra.late_checkout_charge = settings.late_checkout_price if extra.late_checkout else None
            update_fields += ['late_checkout', 'late_checkout_time', 'late_checkout_charge']

        if windows['mid_stay_clean']:
            extra.mid_stay_clean, extra.mid_stay_clean_date, _ = self._parse_mid_stay_clean(booking, post_data)
            extra.mid_stay_clean_charge = (
                settings.compute_mid_stay_clean_price(booking.property) if extra.mid_stay_clean else None
            )
            update_fields += ['mid_stay_clean', 'mid_stay_clean_date', 'mid_stay_clean_charge']

        if update_fields:
            extra.save(update_fields=update_fields)

        # Same rule per catalog item: only the still-open ones are rebuilt from the POST, so a
        # closed item's existing request survives untouched rather than being deleted.
        open_request_type_ids = [
            request_type_id for request_type_id, is_open in windows['request_types'].items() if is_open
        ]
        booking.requested_extras.filter(request_type_id__in=open_request_type_ids).delete()
        new_requests = []
        for request_type in RequestType.objects.filter(active=True, id__in=open_request_type_ids):
            try:
                quantity = int(post_data.get(f'request_qty_{request_type.id}', '0'))
            except (TypeError, ValueError):
                quantity = 0
            if quantity > 0:
                new_requests.append(BookingRequestedExtra(
                    booking=booking,
                    request_type=request_type,
                    quantity=quantity,
                    note=post_data.get(f'request_note_{request_type.id}', '').strip(),
                    price_at_request=request_type.default_price,
                ))
        BookingRequestedExtra.objects.bulk_create(new_requests)

    def _extras_context(self, booking, post_data=None):
        """Welcome Pack + RequestType-row context shared by GET (DB-backed prefill) and a POST
        re-render after a guest-list validation error or price-change interstitial (form-backed,
        so nothing the guest typed into the extras section is lost when the page re-renders)."""
        active_types = list(RequestType.objects.filter(active=True))

        if post_data is not None:
            welcome_pack = post_data.get('welcome_pack') == 'on'
            welcome_pack_food = post_data.get('welcome_pack_food') or WelcomePackFoodChoice.STANDARD
            welcome_pack_drinks = post_data.get('welcome_pack_drinks') or WelcomePackDrinksChoice.ALCOHOLIC
            welcome_pack_note = post_data.get('welcome_pack_note', '').strip()
            cot = post_data.get('cot') == 'on'
            high_chair = post_data.get('high_chair') == 'on'
            late_checkout = post_data.get('late_checkout') == 'on'
            late_checkout_time = post_data.get('late_checkout_time', '').strip()
            mid_stay_clean = post_data.get('mid_stay_clean') == 'on'
            quantities = {t.id: post_data.get(f'request_qty_{t.id}', '0').strip() or '0' for t in active_types}
            notes = {t.id: post_data.get(f'request_note_{t.id}', '').strip() for t in active_types}
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
            mid_stay_clean = bool(extra and extra.mid_stay_clean)
            existing = {r.request_type_id: r for r in booking.requested_extras.all()}
            quantities = {t.id: str(existing[t.id].quantity) if t.id in existing else '0' for t in active_types}
            notes = {t.id: existing[t.id].note if t.id in existing else '' for t in active_types}

        settings = ExtrasSettings.load()
        windows = extras_request_windows(booking)
        nights = (booking.departure_date - booking.arrival_date).days

        # Late check-out permissibility (2026-09-08, per Thomas) - only worth computing while the
        # section is even open at all (windows['late_checkout']). If a grant already exists for
        # this booking, show exactly what it says rather than a fresh live eligibility check - the
        # grant is a committed decision (see LateCheckoutGrant's own docstring: durable, not
        # auto-revoked when later bookings change what's eligible), so re-deriving live here could
        # show a guest "nothing available" on a page reload despite them already holding a valid
        # grant. late_checkout_available_times is sorted for a stable radio-button order, not
        # because ordering matters otherwise.
        late_checkout_unlimited, late_checkout_available_times = False, []
        if windows['late_checkout']:
            existing_grant = getattr(booking, 'late_checkout_grant', None)
            if existing_grant is not None:
                late_checkout_unlimited = existing_grant.time is None
                late_checkout_available_times = [] if late_checkout_unlimited else [existing_grant.time]
            else:
                from staff.utils import late_checkout_still_available
                late_checkout_unlimited, _eligible, available = late_checkout_still_available(booking)
                late_checkout_available_times = sorted(available)

        return {
            'welcome_pack_items': WelcomePackItem.objects.filter(active=True),
            'welcome_pack': welcome_pack,
            'welcome_pack_food': welcome_pack_food,
            'welcome_pack_drinks': welcome_pack_drinks,
            'welcome_pack_note': welcome_pack_note,
            'welcome_pack_price': settings.welcome_pack_price,
            'cot': cot,
            'high_chair': high_chair,
            'cot_high_chair_pricing_config': {
                'nights': nights,
                'cot_short': str(settings.cot_price_short_stay),
                'cot_long': str(settings.cot_price_long_stay),
                'high_chair_short': str(settings.high_chair_price_short_stay),
                'high_chair_long': str(settings.high_chair_price_long_stay),
                'combo_discount_percent': str(settings.cot_and_high_chair_combo_discount_percent),
                'child_min_age': BookingSettings.load().child_min_age,
            },
            'late_checkout': late_checkout,
            'late_checkout_time': late_checkout_time,
            'late_checkout_price': settings.late_checkout_price,
            'late_checkout_unlimited': late_checkout_unlimited,
            'late_checkout_available_times': late_checkout_available_times,
            'late_checkout_offerable': late_checkout_unlimited or bool(late_checkout_available_times),
            'mid_stay_clean': mid_stay_clean,
            'mid_stay_clean_price': settings.compute_mid_stay_clean_price(booking.property),
            # ExtrasSettings.mid_stay_clean_minimum_nights (staff-configurable, floor of 2 - a
            # 1-night stay has no day strictly between its own arrival/departure at all) - see
            # _parse_mid_stay_clean's docstring for the same rule enforced server-side on save,
            # not just this display gate.
            'show_mid_stay_clean': nights >= settings.mid_stay_clean_minimum_nights,
            'request_rows': [
                {'request_type': t, 'quantity': quantities[t.id], 'note': notes[t.id],
                 'open': windows['request_types'].get(t.id, False)}
                for t in active_types
            ],
            # Per-extra ordering windows (2026-09-08) - every page that renders
            # _extras_form.html reads these, so the same extra can't show as editable on one and
            # be rejected on save by another.
            'extras_windows': windows,
            'all_request_types_open': all(windows['request_types'].get(t.id, False) for t in active_types),
        }

    def _save_transfers(self, booking, rows):
        """Prices are always recomputed here from ExtrasSettings, never trusted from the client -
        the JS-side estimate in airport_transfers.js is display-only. A no-op once transfers are
        past their own cutoff, same reason _save_extras() skips a closed extra: the form stops
        rendering the rows, so an empty POST there means "not editable", not "delete them all"."""
        if not extras_request_windows(booking)['airport_transfer']:
            return
        booking.airport_transfers.all().delete()
        settings = ExtrasSettings.load()
        new_transfers = []
        for row in rows:
            total_guests = row['adults'] + row['children'] + row['infants']
            new_transfers.append(AirportTransfer(
                booking=booking,
                direction=row['direction'],
                is_faro=row['is_faro'],
                flight_number=row['flight_number'],
                time=row['parsed_time'],
                adults=row['adults'],
                children=row['children'],
                infants=row['infants'],
                child_seats=row['child_seats'],
                excess_baggage=row['excess_baggage'],
                notes=row['notes'],
                price_at_request=settings.compute_transfer_price(total_guests, row['parsed_time']),
            ))
        AirportTransfer.objects.bulk_create(new_transfers)

    def _transfer_context(self, booking, rows=None, non_field_error=None):
        """Airport Transfer row context, plus the pricing config (the two fixed guest-count
        tiers + night-surcharge window) embedded for the client-side live price estimate in
        airport_transfers.js - purely a display convenience, the authoritative price is always
        recomputed server-side in _save_transfers(), never trusted from the client. Still shaped
        as a 'bands' list (rather than the two ExtrasSettings fields directly) so
        airport_transfers.js's smallest-fitting-band lookup needs no changes even though there
        are now always exactly two, fixed at 4 and 8 guests (see ExtrasSettings.compute_transfer_price)."""
        if rows is None:
            rows = [
                {
                    'direction': t.direction, 'is_faro': t.is_faro, 'flight_number': t.flight_number,
                    'time': t.time.strftime('%H:%M') if t.time else '', 'adults': t.adults,
                    'children': t.children, 'infants': t.infants, 'child_seats': t.child_seats,
                    'excess_baggage': t.excess_baggage, 'notes': t.notes, 'errors': {},
                }
                for t in booking.airport_transfers.all()
            ]

        settings = ExtrasSettings.load()
        return {
            'transfer_rows': rows,
            'transfer_non_field_error': non_field_error,
            'transfer_pricing_config': {
                'bands': [
                    {'max_guests': 4, 'price': str(settings.airport_transfer_price_1_4_guests)},
                    {'max_guests': 8, 'price': str(settings.airport_transfer_price_5_8_guests)},
                ],
                'night_start': settings.airport_transfer_night_window_start.strftime('%H:%M'),
                'night_end': settings.airport_transfer_night_window_end.strftime('%H:%M'),
                'night_surcharge': str(settings.airport_transfer_night_surcharge),
            },
        }

    def _parse_transfer_rows(self, post_data):
        """Ten parallel arrays, same convention as _parse_rows() - see that method's docstring for
        why (not a Django formset). Unlike the Guest List, Airport Transfers are entirely optional
        and dynamically added/removed, so a fully empty submission is not an error - only a genuine
        length mismatch (a malformed submission) is."""
        directions = post_data.getlist('transfer_direction[]')
        airports = post_data.getlist('transfer_airport[]')
        flight_numbers = post_data.getlist('transfer_flight_number[]')
        times = post_data.getlist('transfer_time[]')
        adults_raw = post_data.getlist('transfer_adults[]')
        children_raw = post_data.getlist('transfer_children[]')
        infants_raw = post_data.getlist('transfer_infants[]')
        child_seats = post_data.getlist('transfer_child_seats[]')
        excess_baggage = post_data.getlist('transfer_excess_baggage[]')
        notes = post_data.getlist('transfer_notes[]')

        lengths = {
            len(directions), len(airports), len(flight_numbers), len(times), len(adults_raw),
            len(children_raw), len(infants_raw), len(child_seats), len(excess_baggage), len(notes),
        }
        if len(lengths) > 1:
            return [], "Something went wrong submitting your airport transfers - please try again."

        def parse_count(raw):
            try:
                value = int(raw)
                return value if value >= 0 else 0
            except (TypeError, ValueError):
                return 0

        rows = []
        for i in range(len(directions)):
            errors = {}
            direction = (
                directions[i] if directions[i] in AirportTransferDirection.values
                else AirportTransferDirection.INBOUND
            )
            is_faro = airports[i] != 'other'

            time_raw = times[i].strip()
            parsed_time = None
            if not time_raw:
                errors['time'] = "Enter a pickup/drop-off time."
            else:
                try:
                    parsed_time = datetime.strptime(time_raw, '%H:%M').time()
                except ValueError:
                    errors['time'] = "Enter a valid time."

            adults = parse_count(adults_raw[i])
            children = parse_count(children_raw[i])
            infants = parse_count(infants_raw[i])
            if adults + children + infants < 1:
                errors['guests'] = "Enter at least one guest."

            rows.append({
                'direction': direction,
                'is_faro': is_faro,
                'flight_number': flight_numbers[i].strip(),
                'time': time_raw,
                'parsed_time': parsed_time,
                'adults': adults,
                'children': children,
                'infants': infants,
                'child_seats': child_seats[i].strip(),
                'excess_baggage': excess_baggage[i].strip(),
                'notes': notes[i].strip(),
                'errors': errors,
            })
        return rows, None

    def _parse_late_checkout(self, booking, post_data):
        """Validates the late-checkout portion of the Extras form - whether it's well-formed
        enough to save, NOT whether it's currently actually available (that's a live check,
        deliberately performed only once, at the moment of actually saving in
        _apply_late_checkout_request() below - this function is also called from several read-only
        "did anything on this page fail validation" call sites that must never have side effects).

        Returns (late_checkout, requested_time, error). requested_time is a `time` object, or None
        - which means "whatever the system can offer" when late_checkout is on and no specific time
        was submitted, only valid when late_checkout_still_available() isn't currently offering a
        fixed-time choice at all (the unlimited case, or nothing available). A submitted time must
        be one of staff.models.LATE_CHECKOUT_TIMES - no freeform choice any more (2026-09-08, per
        Thomas: the whole point of the new permissibility rules is a guest can no longer just type
        in any time and have it silently accepted)."""
        from staff.models import LATE_CHECKOUT_TIMES
        from staff.utils import late_checkout_still_available

        late_checkout = post_data.get('late_checkout') == 'on'
        if not late_checkout:
            return False, None, None

        time_raw = post_data.get('late_checkout_time', '').strip()
        if not time_raw:
            unlimited, _eligible, available_times = late_checkout_still_available(booking)
            if not unlimited and available_times:
                return True, None, "Choose a checkout time."
            return True, None, None

        try:
            requested_time = datetime.strptime(time_raw, '%H:%M').time()
        except ValueError:
            return True, None, "Enter a valid checkout time."
        if requested_time not in LATE_CHECKOUT_TIMES:
            return True, None, "Enter a valid checkout time."
        return True, requested_time, None

    def _apply_late_checkout_request(self, booking, wants_late_checkout, requested_time):
        """Reconciles a guest's late-checkout choice (already validated by _parse_late_checkout
        above) against any existing LateCheckoutGrant for this booking - called from _save_extras()
        on every Extras-form save, not just when late checkout itself changed, so it must be a safe
        no-op when nothing here actually changed. Returns (late_checkout, late_checkout_time) for
        Extra's own fields - deliberately no error return: by the time this runs, the calling view
        has already re-rendered on any _parse_late_checkout() validation error, so a grant failure
        here would only ever be the live-availability race (another guest claimed the slot between
        page load and this submit) - rare enough to just silently fall back to "no late checkout"
        rather than block the rest of this save over it.

        A guest switching from one granted time to a different one revokes the old grant before
        attempting the new one, since grant_late_checkout() itself refuses a booking that already
        has a grant. Accepted trade-off: if the new time then turns out unavailable, the guest
        loses the old grant too, rather than never letting a guest switch times at all - expected
        to be a rare edge case, not worth a "try new, restore old on failure" path."""
        from staff.utils import grant_late_checkout, revoke_late_checkout

        existing_grant = getattr(booking, 'late_checkout_grant', None)

        if not wants_late_checkout:
            if existing_grant is not None:
                revoke_late_checkout(existing_grant)
            return False, None

        if existing_grant is not None and existing_grant.time == requested_time:
            return True, existing_grant.time

        if existing_grant is not None:
            revoke_late_checkout(existing_grant)

        grant, _error = grant_late_checkout(booking, requested_time)
        if grant is None:
            return False, None
        return True, grant.time

    def _mid_stay_clean_default_date(self, booking):
        """The one date shown to the guest for a mid-stay clean - not guest-editable (an earlier
        version let the guest pick, then nudge ±1 day from this default, but a date this small
        cleaning team can't reliably staff around is better presented as a fixed estimate than a
        negotiation - see _parse_mid_stay_clean, which no longer reads a date from the guest at
        all). See bookings.utils.mid_stay_clean_window for where this actually comes from, and for
        the ±1-day min/max staff can still nudge it within on the cleaning calendar."""
        return mid_stay_clean_window(booking)[0]

    def _parse_mid_stay_clean(self, booking, post_data):
        """Same optional-field-behind-a-checkbox shape as _parse_late_checkout above. The date
        itself is never read from post_data - it's always _mid_stay_clean_default_date(booking) -
        so the only thing left to validate is the stay meeting
        ExtrasSettings.mid_stay_clean_minimum_nights; _extras_form.html only offers this section
        when that's already satisfied (see _extras_context's show_mid_stay_clean), but it's
        re-checked here too since the display gate is just a convenience, not the real guard."""
        mid_stay_clean = post_data.get('mid_stay_clean') == 'on'
        if not mid_stay_clean:
            return False, None, None

        nights = (booking.departure_date - booking.arrival_date).days
        if nights < ExtrasSettings.load().mid_stay_clean_minimum_nights:
            return True, None, "A mid-stay clean isn't available for a stay this short."
        return True, self._mid_stay_clean_default_date(booking), None

    def _any_infant_age(self, rows, child_min_age):
        """Whether any current guest-list row is age'd as an infant (below child_min_age) - the
        sole condition for showing the Cot & High Chair section server-side on first render.
        cot_high_chair.js recomputes this same check client-side as the guest edits ages, so the
        section can appear/disappear live without a page reload - see that file. Deliberately does
        NOT also consider Booking.babies (the original search's infant count): on a fresh booking
        every age field starts blank regardless of what was picked in search (see
        _seed_or_prefill_rows), so there's no age to check yet - the guest must actually type an
        infant age into a row for either the initial render or the live JS check to show it."""
        for row in rows:
            age = str(row.get('age', '')).strip()
            if age.isdigit() and int(age) < child_min_age:
                return True
        return False

    def _seed_or_prefill_rows(self, booking):
        """Row dicts in the same shape _parse_rows() produces, so the template has one rendering
        path for both a fresh GET and a POST re-display after a validation error."""
        existing = list(booking.party.all())
        if existing:
            return [
                {'first_name': guest.first_name, 'last_name': guest.last_name, 'age': guest.age, 'errors': {}}
                for guest in existing
            ]
        rows = [{
            'first_name': booking.guest.first_name or '',
            'last_name': booking.guest.last_name,
            'age': '',
            'errors': {},
        }]
        blank_count = booking.adults + booking.children + booking.babies - 1
        for _ in range(max(0, blank_count)):
            rows.append({'first_name': '', 'last_name': '', 'age': '', 'errors': {}})
        return rows

    def _seed_guest_add_rows(self, booking, existing_party):
        """Blank rows for the fully_paid stage's 'Add a guest' mini-form, same shape as
        _seed_or_prefill_rows() above - pre-seeded to the gap between the party already named and
        the original adults+children+babies count from booking time (whichever side last set it -
        the guest's own reservation, or staff editing it afterwards), so a guest/staff member sees
        exactly enough blank rows to name everyone rather than clicking '+ Add another guest'
        repeatedly. Deliberately the raw adults+children+babies fields, not Booking.total_guests()
        - that method prefers the real party count once any exists, which would collapse this gap
        to 0 the moment even one guest is named; this needs the ORIGINAL expected count regardless
        of how many are already named, exactly like _seed_or_prefill_rows() does for the
        pre_balance stage's own seeding. Always at least 1 row, even with no gap, so the form never
        renders with zero rows to fill in."""
        expected_total = booking.adults + booking.children + booking.babies
        blank_count = max(1, expected_total - len(existing_party))
        return [{'first_name': '', 'last_name': '', 'age': '', 'errors': {}} for _ in range(blank_count)]

    def _guests_leg_context(self, booking):
        """One apartment's worth of Guest List state, in the exact shape manage_guests.html has
        always expected at top level for a single-property booking - so the merged multi-property
        page (`legs`, one of these per apartment) and the single-property page render through the
        same keys, just at different nesting depths.

        `stage` is per-leg on purpose, not taken from _manage_nav_context()'s own top-level value:
        a multi-property stay's two apartments each have their own Charge/BalancePayment and can
        genuinely be at different stages at once (one balance paid, the other not), so one leg can
        need the full editable repricing form while the other simultaneously needs the fully-paid
        add/remove controls. See BookingManageGuestsView's docstring."""
        stage = 'fully_paid' if is_fully_paid(booking) else 'pre_balance'
        context = {
            'booking': booking,
            'stage': stage,
            'max_guests': booking.property.specs.max_guests,
        }
        if stage == 'fully_paid':
            party = list(booking.party.all())
            context['party'] = party
            context['guest_add_rows'] = self._seed_guest_add_rows(booking, party)
        else:
            context['rows'] = self._seed_or_prefill_rows(booking)
        return context

    def _merged_guest_legs(self, bookings, target=None, overrides=None):
        """Every leg's _guests_leg_context(), with `overrides` merged into whichever one is
        `target` - the "re-render the whole merged page, but keep the submitting apartment's own
        typed values and errors" path every POST below needs on a validation failure or a
        price-change interstitial. Same shape as Stage D3's guest-registrations equivalent.

        The non-target legs are rebuilt from the database, deliberately: a failed POST against one
        apartment must never silently discard or re-display stale state for the other."""
        legs = []
        for booking in bookings:
            leg = self._guests_leg_context(booking)
            if target is not None and booking.pk == target.pk and overrides:
                leg.update(overrides)
            legs.append(leg)
        return legs

    def _parse_rows(self, post_data):
        """Three parallel arrays (first_name[]/last_name[]/age[]), not a Django formset - see the
        plan this was built from for why. Returns (rows, non_field_error); rows is [] only when
        non_field_error is set (a malformed submission, not a normal validation failure)."""
        first_names = post_data.getlist('first_name[]')
        last_names = post_data.getlist('last_name[]')
        ages_raw = post_data.getlist('age[]')

        if not first_names or not (len(first_names) == len(last_names) == len(ages_raw)):
            return [], "Something went wrong submitting the guest list - please try again."

        rows = []
        for first_name, last_name, age_raw in zip(first_names, last_names, ages_raw):
            errors = {}
            first_name = first_name.strip()
            last_name = last_name.strip()
            if not first_name:
                errors['first_name'] = "First name is required."
            if not last_name:
                errors['last_name'] = "Last name is required."
            try:
                age_value = int(age_raw)
                if age_value < 0 or age_value > MAX_GUEST_AGE:
                    errors['age'] = "Enter a real age."
            except (TypeError, ValueError):
                errors['age'] = "Enter a real age."
            rows.append({'first_name': first_name, 'last_name': last_name, 'age': age_raw.strip(), 'errors': errors})
        return rows, None

    def _save_guest_list(self, booking, rows, new_guests):
        """Persists a validated guest list - shared by BookingDetailsView (deposit stage) and
        BookingBalanceDetailsView (balance stage, see recalculate_balance_for_party()). Does NOT
        touch Charge - the caller applies whichever pricing rule is appropriate for its stage
        first, then calls this."""
        booking.party.all().delete()
        BookingGuest.objects.bulk_create([
            BookingGuest(
                booking=booking,
                first_name=row['first_name'],
                last_name=row['last_name'],
                age=int(row['age']),
                is_lead=(index == 0),
            )
            for index, row in enumerate(rows)
        ])
        booking.adults = new_guests['adults']
        booking.children = new_guests['children']
        booking.babies = new_guests['infants']
        booking.last_updated = timezone.now()
        booking.save(update_fields=['adults', 'children', 'babies', 'last_updated'])

    def _append_guest_rows(self, booking, rows, adjustment, new_guests):
        """Used only by BookingManageGuestAddView, once the booking is already fully paid - see
        bookings/utils.py::append_guest_rows(), shared with apply_supplementary_payment() for the
        online-payment-gated case."""
        append_guest_rows(booking, rows, adjustment, new_guests)


class BookingOfferOpenView(View):
    """Activates a staff-generated guest-offer link (staff/views.py::StaffGuestOfferCreateView) -
    sets pending_booking_reference exactly like ReserveView.post() does for a self-service
    booking, so every existing session-gated check downstream (BookingDetailsView.post,
    BookingPaymentCancelView) treats this guest's browser as the booking's owner, letting them
    land straight on the guest-list step without ever seeing the name/email/phone/country form.

    Deliberately scoped to enquiry_source='Staff offer' bookings only, NOT a bare reference
    lookup - pending_booking_reference exists specifically so that "anyone who happened to see
    someone else's reference" (it appears in URLs, browser history, screenshots) can't grief a
    live reservation, e.g. cancel it via BookingPaymentCancelView (see that view's own docstring).
    An unscoped version of this view would turn every ordinary booking's already-bearer-readable
    reference into a bearer-*cancellable* one too - ordinary Website/Owner Suite bookings must
    never be activatable this way, only the staff-offer bookings this view exists for."""

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference, enquiry_source='Staff offer').first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        request.session['pending_booking_reference'] = reference
        return redirect('bookings:details', reference=reference)


class BookingDetailsView(BookingFormMixin, View):
    """Booking-details step shown right after a reservation is created, before payment - the
    guest-list section (first/last name + age per party member) plus, for a collapsed (single-
    payment) booking only, the Extras section (Welcome Pack, Cot/High Chair, Late Checkout,
    Airport Transfers, RequestType catalog items - cash-at-checkin, no interaction with the online
    Charge/Payment total). A two-stage booking (hasattr(booking, 'balance_payment')) skips Extras
    here entirely - they move to BookingBalanceDetailsView instead, see BalancePayment's docstring
    for why. GET is bearer-readable like every other reference-based view; POST is a write (it can
    change Booking.adults/children/babies and the locked Charge, plus the guest's extras on a
    collapsed booking) and is gated the same way BookingPaymentCancelView gates cancellation - see
    that class's docstring."""
    template_name = 'bookings/details.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if is_paid(booking):
            return redirect_to_next_step_after_payment(request, booking)

        is_two_stage = hasattr(booking, 'balance_payment')
        payment = booking.payment
        rows = self._seed_or_prefill_rows(booking)
        child_min_age = BookingSettings.load().child_min_age
        context = {
            'booking': booking,
            'rows': rows,
            'max_guests': booking.property.specs.max_guests,
            'hold_expired': booking.hold_expires_at is not None and booking.hold_expires_at <= timezone.now(),
            'payment_in_progress': payment.status == 'in_progress',
            'child_min_age': child_min_age,
            'show_cot_high_chair': self._any_infant_age(rows, child_min_age),
            'is_two_stage': is_two_stage,
        }
        if not is_two_stage:
            context.update(self._extras_context(booking))
            context.update(self._transfer_context(booking))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if request.session.get('pending_booking_reference') != reference:
            return redirect('bookings:pay', reference=reference)

        hold_expired = booking.hold_expires_at is not None and booking.hold_expires_at <= timezone.now()
        payment = booking.payment
        if hold_expired or booking.enquiry_status != 'Awaiting payment' or payment.status == 'in_progress':
            return redirect('bookings:pay', reference=reference)

        is_two_stage = hasattr(booking, 'balance_payment')
        max_guests = booking.property.specs.max_guests
        rows, non_field_error = self._parse_rows(request.POST)
        if not is_two_stage:
            transfer_rows, transfer_non_field_error = self._parse_transfer_rows(request.POST)
            _, _, late_checkout_error = self._parse_late_checkout(booking, request.POST)
            _, _, mid_stay_clean_error = self._parse_mid_stay_clean(booking, request.POST)
        else:
            transfer_rows, transfer_non_field_error, late_checkout_error = [], None, None
            mid_stay_clean_error = None
        child_min_age = BookingSettings.load().child_min_age
        context = {
            'booking': booking,
            'rows': rows,
            'max_guests': max_guests,
            'hold_expired': False,
            'payment_in_progress': False,
            'non_field_error': non_field_error,
            'child_min_age': child_min_age,
            'show_cot_high_chair': self._any_infant_age(rows, child_min_age),
            'is_two_stage': is_two_stage,
        }
        if not is_two_stage:
            context.update(self._extras_context(booking, post_data=request.POST))
            context.update(self._transfer_context(booking, rows=transfer_rows, non_field_error=transfer_non_field_error))
            context['late_checkout_error'] = late_checkout_error
            context['mid_stay_clean_error'] = mid_stay_clean_error

        if non_field_error or any(row['errors'] for row in rows):
            return render(request, self.template_name, context)

        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            return render(request, self.template_name, context)

        if late_checkout_error or mid_stay_clean_error:
            return render(request, self.template_name, context)

        if len(rows) > max_guests:
            context['non_field_error'] = f"This property allows a maximum of {max_guests} guests."
            return render(request, self.template_name, context)

        ages = [int(row['age']) for row in rows]
        new_guests, new_costs, changed = recalculate_costs_for_party(booking, ages)
        if new_guests is None:
            context['non_field_error'] = (
                "This stay can no longer be priced automatically - please contact us to complete your booking."
            )
            return render(request, self.template_name, context)
        if new_guests['adults'] == 0:
            context['non_field_error'] = "At least one adult must be included in the party."
            return render(request, self.template_name, context)

        if changed and request.POST.get('confirmed') != '1':
            context['price_changed'] = True
            context['old_charge'] = booking.charges
            context['new_costs'] = new_costs
            return render(request, self.template_name, context)

        with transaction.atomic():
            self._save_guest_list(booking, rows, new_guests)

            charge = booking.charges
            charge.basic_rental = new_costs['basic_rental']
            charge.discount_total = new_costs['discount_total']
            charge.extra_guest_total = new_costs['extra_guest_total']
            charge.admin = new_costs['admin_fee']
            # security deliberately NOT touched here - it's a one-time waiver-aware calculation
            # at create_booking() time, staff-owned from then on (see compute_deposit_waiver()'s
            # docstring, bookings/utils.py) - a party-size change must never silently reset a
            # staff override back to the flat default.
            charge.due_at_booking = new_costs['due_at_booking']
            charge.due_at_balance = new_costs['due_at_balance']
            charge.balance_due_date = new_costs['balance_due_date']
            charge.save(update_fields=[
                'basic_rental', 'discount_total', 'extra_guest_total', 'admin',
                'due_at_booking', 'due_at_balance', 'balance_due_date',
            ])

            # A price change after a Revolut checkout URL already exists (guest went details ->
            # pay -> back -> details, changed something) would otherwise leave the guest paying a
            # stale amount - clear it so BookingPaymentView.get() rebuilds the order fresh.
            if changed and payment.revolut_checkout_url:
                payment.revolut_order_id = None
                payment.revolut_checkout_url = None
                payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])

            if not is_two_stage:
                self._save_extras(booking, request.POST)
                self._save_transfers(booking, transfer_rows)

        return redirect('bookings:pay', reference=booking.reference)


class BookingBalanceDetailsView(BookingFormMixin, View):
    """Guest-list, Arrival & Departure, and Extras step for a two-stage booking's balance stage
    (see BalancePayment's docstring). The guest list stays editable here up to the property's
    max_guests, same as BookingDetailsView - but unlike that view, the deposit (due_at_booking) is
    already paid and frozen by this point, so a change here can only move due_at_balance, never
    retroactively redefine the deposit - see bookings/utils.py::recalculate_balance_for_party() for
    why this can't just reuse recalculate_costs_for_party(). Reached via a link sent manually to
    the guest around BookingSettings.balance_reminder_days_before_arrival (no automated email yet),
    or self-serve from the confirmation/manage-booking pages once the deposit is paid - see
    bookings/utils.py::booking_confirmation_context(). Bearer-readable by reference alone, like
    every other post-deposit reference-based view here - unlike BookingDetailsView's POST, there's
    no same-session pending_booking_reference to check, since this is reached from an emailed link
    days later with no session continuity at all.

    Arrival & Departure (embedded via the shared _arrival_departure_form.html partial, right
    between the guest list and Extras per Thomas's placement) is a second, parallel entry point to
    the exact same Arrival/Departure rows BookingManageArrivalDepartureView edits on the Manage hub
    - same module-level _arrival_data_from_model()/_save_arrival()/etc. helpers, so whichever page
    a guest used most recently is what shows up on the other. Its flight-number validation is
    checked alongside the guest-list/transfer/late-checkout row errors, before the price-changed
    interstitial, so a bad flight number never falls through to a real save; it's saved inside the
    same atomic block as everything else so it waits for that interstitial's confirmation too, same
    as the guest list and extras do."""
    template_name = 'bookings/balance_details.html'

    def _get_gated_booking(self, reference):
        """Returns (booking, redirect_response). redirect_response is None if the booking is in a
        state where this view should actually render - otherwise it's where the guest belongs instead."""
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not hasattr(booking, 'balance_payment'):
            return booking, redirect('bookings:confirmation', reference=reference)
        if not is_paid(booking):
            return booking, redirect('bookings:pay', reference=reference)
        if is_balance_paid(booking):
            return booking, redirect('bookings:confirmation', reference=reference)
        if booking.balance_payment.status == 'in_progress':
            return booking, redirect('bookings:balance_pay', reference=reference)
        return booking, None

    def get(self, request, reference, *args, **kwargs):
        booking, redirect_response = self._get_gated_booking(reference)
        if redirect_response is not None:
            return redirect_response

        rows = self._seed_or_prefill_rows(booking)
        child_min_age = BookingSettings.load().child_min_age
        context = {
            'booking': booking,
            'rows': rows,
            'max_guests': booking.property.specs.max_guests,
            'child_min_age': child_min_age,
            'show_cot_high_chair': self._any_infant_age(rows, child_min_age),
        }
        context.update(_arrival_departure_field_context(
            _arrival_data_from_model(getattr(booking, 'arrival', None)),
            _departure_data_from_model(getattr(booking, 'departure', None)),
        ))
        context.update(self._extras_context(booking))
        context.update(self._transfer_context(booking))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        booking, redirect_response = self._get_gated_booking(reference)
        if redirect_response is not None:
            return redirect_response

        max_guests = booking.property.specs.max_guests
        rows, non_field_error = self._parse_rows(request.POST)
        transfer_rows, transfer_non_field_error = self._parse_transfer_rows(request.POST)
        _, _, late_checkout_error = self._parse_late_checkout(booking, request.POST)
        _, _, mid_stay_clean_error = self._parse_mid_stay_clean(booking, request.POST)
        arrival_data = _arrival_data_from_post(request.POST)
        departure_data = _departure_data_from_post(request.POST)
        child_min_age = BookingSettings.load().child_min_age
        context = {
            'booking': booking,
            'rows': rows,
            'max_guests': max_guests,
            'non_field_error': non_field_error,
            'child_min_age': child_min_age,
            'show_cot_high_chair': self._any_infant_age(rows, child_min_age),
        }
        context.update(_arrival_departure_field_context(arrival_data, departure_data))
        context.update(self._extras_context(booking, post_data=request.POST))
        context.update(self._transfer_context(booking, rows=transfer_rows, non_field_error=transfer_non_field_error))
        context['late_checkout_error'] = late_checkout_error
        context['mid_stay_clean_error'] = mid_stay_clean_error

        if non_field_error or any(row['errors'] for row in rows):
            return render(request, self.template_name, context)

        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            return render(request, self.template_name, context)

        if late_checkout_error or mid_stay_clean_error:
            return render(request, self.template_name, context)

        arrival_departure_errors = _arrival_departure_flight_number_errors(arrival_data, departure_data)
        if arrival_departure_errors:
            context['errors'] = arrival_departure_errors
            return render(request, self.template_name, context)

        if len(rows) > max_guests:
            context['non_field_error'] = f"This property allows a maximum of {max_guests} guests."
            return render(request, self.template_name, context)

        ages = [int(row['age']) for row in rows]
        new_guests, new_costs, changed = recalculate_balance_for_party(booking, ages)
        if new_guests is None:
            context['non_field_error'] = (
                "This stay can no longer be priced automatically - please contact us to complete your booking."
            )
            return render(request, self.template_name, context)
        if new_guests['adults'] == 0:
            context['non_field_error'] = "At least one adult must be included in the party."
            return render(request, self.template_name, context)

        if changed and request.POST.get('confirmed') != '1':
            context['price_changed'] = True
            context['old_charge'] = booking.charges
            context['new_costs'] = new_costs
            return render(request, self.template_name, context)

        with transaction.atomic():
            self._save_guest_list(booking, rows, new_guests)

            charge = booking.charges
            charge.basic_rental = new_costs['basic_rental']
            charge.discount_total = new_costs['discount_total']
            charge.extra_guest_total = new_costs['extra_guest_total']
            charge.admin = new_costs['admin_fee']
            # security deliberately NOT touched here - see the equivalent comment in
            # BookingDetailsView above.
            charge.due_at_balance = new_costs['due_at_balance']
            charge.save(update_fields=[
                'basic_rental', 'discount_total', 'extra_guest_total', 'admin', 'due_at_balance',
            ])

            # A price change after a Revolut checkout URL already exists (guest went balance ->
            # pay -> back -> balance, changed something) would otherwise leave the guest paying a
            # stale amount - clear it so BookingBalancePaymentView.get() rebuilds the order fresh.
            balance_payment = booking.balance_payment
            if changed and balance_payment.revolut_checkout_url:
                balance_payment.revolut_order_id = None
                balance_payment.revolut_checkout_url = None
                balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])

            self._save_extras(booking, request.POST)
            self._save_transfers(booking, transfer_rows)
            _save_arrival(booking, arrival_data)
            _save_departure(booking, departure_data)

        return redirect('bookings:balance_pay', reference=booking.reference)


class BookingPaymentView(View):
    """Deposit-payment step shown right after a reservation is created. Revolut-path bookings get
    a hosted checkout link (created lazily here, on first visit); Wise-path bookings get a static
    pay-page link with instructions, since there's no per-booking API object to create for Wise."""
    template_name = 'bookings/pay.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if is_paid(booking):
            return redirect_to_next_step_after_payment(request, booking)

        payment = booking.payment
        charge = booking.charges
        pay_amount, pay_currency = charge.due_at_booking_in_charge_currency()
        context = {
            'booking': booking,
            'charge': charge,
            'payment': payment,
            'pay_amount': pay_amount,
            'pay_currency': pay_currency,
            'hold_expired': booking.hold_expires_at is not None and booking.hold_expires_at <= timezone.now(),
            'extras': extras_summary(booking),
        }

        if not context['hold_expired'] and payment.provider == 'revolut' and not payment.revolut_checkout_url:
            self._create_revolut_order(booking, payment, pay_amount, pay_currency)

        context['payment_error'] = payment.provider == 'revolut' and not payment.revolut_checkout_url and not context['hold_expired']
        context['wise_payment_link'] = env_settings.WISE_BASE_PAYMENT_LINK

        return render(request, self.template_name, context)

    def _create_revolut_order(self, booking, payment, pay_amount, pay_currency):
        order = Revolut(secretKey=env_settings.REVOLUT_API_SECRET_KEY).payment
        order.amount = int(pay_amount * 100)  # Revolut wants minor units (cents/pence), not major units
        order.currency = pay_currency  # whatever currency the guest was quoted at booking time
        order.description = f"Deposit for booking {booking.reference}"
        order.customerEmail = booking.guest.email
        order.customerName = f"{booking.guest.first_name} {booking.guest.last_name}".strip()
        order.create()

        if order.id and order.has('checkout_url'):
            payment.revolut_order_id = order.id
            payment.revolut_checkout_url = order.checkoutUrl
            payment.save()
        # else: order.create() already logged the failure via logerror(); leave payment.revolut_checkout_url
        # unset so payment_error renders and the guest can retry on reload.


class BookingBalancePaymentView(View):
    """Balance-payment step for a two-stage booking, reached after BookingBalanceDetailsView (or
    directly, if the guest already chose their Extras and is just returning to pay). Mirrors
    BookingPaymentView closely - same lazy Revolut order creation, same static Wise link - but
    against BalancePayment/due_at_balance instead of Payment/due_at_booking, same provider as the
    deposit (no need to recompute - determine_payment_provider() is a pure function of arrival_date
    anyway). No hold/countdown here: the calendar slot was already locked in by the confirmed
    deposit, so there's nothing to expire, and no cancel-and-restart flow either (nothing to release)."""
    template_name = 'bookings/balance_pay.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not hasattr(booking, 'balance_payment'):
            return redirect('bookings:confirmation', reference=reference)
        if not is_paid(booking):
            return redirect('bookings:pay', reference=reference)
        if is_balance_paid(booking):
            return redirect('bookings:confirmation', reference=reference)

        balance_payment = booking.balance_payment
        charge = booking.charges
        pay_amount, pay_currency = charge.due_at_balance_in_charge_currency()
        context = {
            'booking': booking,
            'charge': charge,
            'balance_payment': balance_payment,
            'pay_amount': pay_amount,
            'pay_currency': pay_currency,
            'extras': extras_summary(booking),
        }

        if balance_payment.provider == 'revolut' and not balance_payment.revolut_checkout_url:
            self._create_revolut_order(booking, balance_payment, pay_amount, pay_currency)

        context['payment_error'] = balance_payment.provider == 'revolut' and not balance_payment.revolut_checkout_url
        context['wise_payment_link'] = env_settings.WISE_BASE_PAYMENT_LINK

        return render(request, self.template_name, context)

    def _create_revolut_order(self, booking, balance_payment, pay_amount, pay_currency):
        order = Revolut(secretKey=env_settings.REVOLUT_API_SECRET_KEY).payment
        order.amount = int(pay_amount * 100)  # Revolut wants minor units (cents/pence), not major units
        order.currency = pay_currency
        order.description = f"Balance for booking {booking.reference}"
        order.customerEmail = booking.guest.email
        order.customerName = f"{booking.guest.first_name} {booking.guest.last_name}".strip()
        order.create()

        if order.id and order.has('checkout_url'):
            balance_payment.revolut_order_id = order.id
            balance_payment.revolut_checkout_url = order.checkoutUrl
            balance_payment.save()
        # else: order.create() already logged the failure via logerror(); leave revolut_checkout_url
        # unset so payment_error renders and the guest can retry on reload.


SUPPLEMENTARY_PAYMENT_DESCRIPTIONS = {
    'date_change': 'Date change',
    'guest_add': 'Added guest(s)',
}


class BookingManageSupplementaryPaymentView(View):
    """Checkout page for a SupplementaryPayment - the top-up a guest owes for a self-serve date
    change or guest addition that raised the price after the balance was already paid (see that
    model's own docstring; BookingManageDatesView/BookingManageGuestAddView are what create these
    rows). Mirrors BookingBalancePaymentView closely: same lazy Revolut order creation, same
    static Wise link, same lack of any polling - a guest who pays here and closes the tab has
    their date-change/guest-add applied the next time they load any Manage hub page (see
    _manage_nav_context()'s apply-pending-supplementary-payments step), exactly the same
    "confirmed on next visit, not via live polling" norm balance_pay.html already has while the
    webhook pipeline is dormant.

    Bearer-readable by reference + payment id together, same norm as every other Manage hub view -
    the payment_id is only ever handed out via BookingManageDatesView/BookingManageGuestAddView's
    own redirect, right after the guest themselves requested the change."""
    template_name = 'bookings/manage_supplementary_pay.html'

    def get(self, request, reference, payment_id, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        payment = SupplementaryPayment.objects.filter(pk=payment_id, booking=booking).first()
        if payment is None:
            raise Http404("No supplementary payment found for this booking.")

        siblings = self._siblings(payment)
        if payment.status == 'paid':
            for sibling in siblings:
                sibling.apply()
            return redirect(self._success_url(payment))

        # One charge for the whole party (Stage D7): a multi-property date change stages a row per
        # apartment so each owner's revenue stays attributed to their own booking, but the guest
        # pays once, for the sum - see BookingManageDatesView._stage_date_change().
        pay_amount = sum((sibling.amount for sibling in siblings), Decimal('0'))
        context = {
            'booking': booking, 'payment': payment,
            'pay_amount': pay_amount, 'pay_currency': payment.currency,
            'payment_legs': siblings if len(siblings) > 1 else None,
        }
        context.update(_manage_nav_context(booking, 'dates' if payment.kind == 'date_change' else 'guests'))

        if payment.provider == 'revolut' and not payment.revolut_checkout_url:
            self._create_revolut_order(payment, pay_amount=pay_amount, siblings=siblings)

        context['payment_error'] = payment.provider == 'revolut' and not payment.revolut_checkout_url
        context['wise_payment_link'] = env_settings.WISE_BASE_PAYMENT_LINK
        return render(request, self.template_name, context)

    def _success_url(self, payment):
        booking = payment.booking
        if payment.kind == 'date_change':
            return f"{reverse('bookings:manage_dates', args=[booking.reference])}?dates_updated=1"
        # Back to the merged Guest List for a multi-property stay (Stage D5) rather than this one
        # apartment's own page - this checkout is per-apartment, but the page the guest returns to
        # isn't. guest_added carries the paid-for leg so the note lands against the right one.
        stay_reference = (
            booking.reservation_group.reference if booking.reservation_group_id else booking.reference
        )
        return f"{reverse('bookings:manage_guests', args=[stay_reference])}?guest_added={booking.reference}"

    def _siblings(self, payment):
        """Every SupplementaryPayment staged by the same request as `payment`. A single-property
        booking's is always just itself; a multi-property date change stages one per apartment (so
        each owner's revenue stays attributed to their own booking) but the guest pays once, for
        the sum, against one shared Revolut order - see
        BookingManageDatesView._stage_date_change(). Matched on the staged dates rather than a
        grouping column: a stay only ever has one date change in flight at a time (that's enforced
        in BookingManageDatesView.post), so the same kind + same held dates + same group is
        unambiguous."""
        booking = payment.booking
        if payment.kind != 'date_change' or not booking.reservation_group_id:
            return [payment]
        return list(
            SupplementaryPayment.objects.filter(
                booking__reservation_group_id=booking.reservation_group_id,
                kind='date_change',
                new_arrival_date=payment.new_arrival_date,
                new_departure_date=payment.new_departure_date,
            ).select_related('booking', 'booking__property').order_by('pk')
        )

    def _create_revolut_order(self, payment, pay_amount=None, siblings=None):
        booking = payment.booking
        siblings = siblings or [payment]
        pay_amount = payment.amount if pay_amount is None else pay_amount
        order = Revolut(secretKey=env_settings.REVOLUT_API_SECRET_KEY).payment
        order.amount = int(pay_amount * 100)  # Revolut wants minor units (cents/pence), not major units
        order.currency = payment.currency
        references = ', '.join(sibling.booking.reference for sibling in siblings)
        order.description = f"{SUPPLEMENTARY_PAYMENT_DESCRIPTIONS[payment.kind]} for booking {references}"
        order.customerEmail = booking.guest.email
        order.customerName = f"{booking.guest.first_name} {booking.guest.last_name}".strip()
        order.create()

        if order.id and order.has('checkout_url'):
            # The SAME order id on every sibling: klt-hooks' mark_supplementary_payment_paid() does
            # an unlimited `UPDATE ... WHERE revolut_order_id = %s`, so one guest payment marks
            # them all paid and the hub's own sweep then applies every apartment's staged dates
            # together - no klt-hooks change needed, same as Stage D4's tourist tax.
            for sibling in siblings:
                sibling.revolut_order_id = order.id
                sibling.revolut_checkout_url = order.checkoutUrl
                sibling.save()
        # else: order.create() already logged the failure via logerror(); leave revolut_checkout_url
        # unset so payment_error renders and the guest can retry on reload.


class BookingPaymentCancelView(View):
    """Lets a guest back out of their own not-yet-paid hold (e.g. picked the wrong currency) and
    redoes the reservation, rather than being stuck until the hold times out - see
    bookings/utils.py::cancel_booking_hold() for why this can't be used against an already-paid
    or already-failed booking, and why the Revolut order (if any) is deliberately left alone.

    Every other reference-based view here is deliberately read-only (a bearer link, like a
    checkout confirmation), so this is the one place a bare reference isn't enough - cancelling is
    a write, and anyone who happened to see someone else's reference could otherwise grief their
    still-active reservation. Requires it to match this session's own pending_booking_reference,
    set in ReserveView.post() at creation time.
    """

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if request.session.get('pending_booking_reference') == reference:
            cancel_booking_hold(booking)
            request.session.pop('pending_booking_reference', None)
        return redirect(reservation_retry_url(booking))


class BookingPaymentStatusView(View):
    """Read-only JSON status for the pay page's polling JS. All writes to Payment/Booking happen
    from klt-hooks via the Revolut webhook - this endpoint never mutates anything."""

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        payment = getattr(booking, 'payment', None)
        return JsonResponse({
            'status': payment.status if payment else 'paid',
            'enquiry_status': booking.enquiry_status,
            'hold_expires_at': booking.hold_expires_at.isoformat() if booking.hold_expires_at else None,
        })


class BookingConditionsView(View):
    """Public summary of the terms a guest should understand before reserving."""
    template_name = 'bookings/conditions.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {'conditions': BookingCondition.objects.all()})


class TermsAndConditionsView(View):
    """The full legal Terms and Conditions document (the actual Booking Contract text a guest
    agrees to at reservation time - see properties/views.py::ReserveView and
    ReservationForm.terms_accepted), distinct from BookingConditionsView above - that page is a
    short plain-language summary, this is the real contract. Static template, not an
    admin/staff-editable model like BookingCondition - a legal document should go through a
    deliberate edit-and-review pass, not get retyped from a Settings form (2026-09-13, per
    Thomas)."""
    template_name = 'bookings/terms.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)


class ManageBookingView(View):
    """Reference + email lookup for a guest returning later without their confirmation link. Once
    the lookup succeeds, hands off to BookingManageHubView (bearer-readable by reference alone,
    like every other post-deposit view here) rather than rendering the booking in place - so a
    guest who bookmarks/reloads the hub URL doesn't need to re-prove their email every time.

    Also accepts a ReservationGroup's own shared reference (2026-09-13, see
    booking_for_reference_and_email()) - the reference a multi-property reservation's confirmation
    page actually tells the guest to keep, rather than either individual apartment's own."""
    template_name = 'bookings/manage.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {'form': BookingLookupForm()})

    def post(self, request, *args, **kwargs):
        form = BookingLookupForm(request.POST)
        context = {'form': form}
        if form.is_valid():
            booking = booking_for_reference_and_email(
                form.cleaned_data['reference'], form.cleaned_data['email'],
            )
            if booking is not None and not is_paid(booking):
                return redirect('bookings:pay', reference=booking.reference)
            elif booking is not None:
                # The merged hub's own reference (bookings_for_stay_reference()'s inverse) - the
                # group's shared reference once every leg is paid, same individual reference as
                # always for a normal single-property booking.
                stay_reference = (
                    booking.reservation_group.reference if booking.reservation_group_id else booking.reference
                )
                return redirect('bookings:manage_hub', reference=stay_reference)
            else:
                context['not_found'] = True
        return render(request, self.template_name, context)


def _manage_nav_context(booking, active_section, all_bookings=None):
    """Sidebar context shared by every Manage Booking hub section view, so the nav renders
    identically (and highlights the right item) everywhere. show_pay_balance mirrors
    BookingBalanceDetailsView's own gating - Pay Balance only makes sense to show while there's a
    two-stage balance still outstanding; that view's own internal redirects handle every other case
    (unpaid deposit, already paid, payment in progress), so this doesn't need to re-derive those.
    show_cancel_booking is the single source of truth BookingCancelView's own gate also uses (never
    show/allow it once already cancelled, for a platform-sourced booking Thomas doesn't control
    cancellation for from this system, or once the stay has already started).

    Also the single place that sweeps any paid-but-unapplied SupplementaryPayment for this booking
    (see that model's own docstring) - called by every hub section view, so a guest who paid for a
    date change/guest addition and closed the tab gets it applied the moment they next load any
    page here, without needing a dedicated polling endpoint.

    all_bookings (2026-09-14, Stage D): optional full leg list for a multi-property stay, from
    whichever bookings_for_stay_reference() call the caller already made. show_security_deposit is
    the one flag that genuinely needs to know about every leg, not just `booking` (the primary,
    used for every other flag/the sweep above, unchanged) - each apartment's Charge.security is set
    independently, so a group where only the SECOND leg owes a deposit must still show the sidebar
    link, even though the primary (first) leg doesn't need one itself. Defaults to [booking] so
    every not-yet-multi-leg-aware call site behaves exactly as before."""
    for payment in booking.supplementary_payments.filter(status='paid', applied_at__isnull=True):
        payment.apply(booking=booking)

    cancelled = is_cancelled(booking)
    legs = all_bookings or [booking]
    return {
        'active_section': active_section,
        # The reference every already-merged sidebar link should use (bookings_for_stay_reference()'s
        # inverse) - the shared ReservationGroup reference for a multi-property stay, same individual
        # reference as always otherwise. Computed off `booking` alone (not the full leg list this
        # function doesn't receive) since it only depends on whether THIS booking belongs to a group,
        # not on which reference the current page happened to be reached through - see the Holiday
        # Info sections (Amenities/Location/Local Rules/Last Days/FAQ/Local Guide) and the "Booking"
        # link itself, all switched onto this 2026-09-14; a section not yet merged (Guest List,
        # Extras, etc.) keeps using booking.reference directly in the sidebar for now.
        'stay_reference': booking.reservation_group.reference if booking.reservation_group_id else booking.reference,
        'show_pay_balance': hasattr(booking, 'balance_payment') and not is_balance_paid(booking) and not cancelled,
        'show_cancel_booking': (
            not cancelled
            and booking.enquiry_source not in env_settings.PLATFORMS
            and booking.arrival_date > timezone.now().date()
        ),
        # Only shown for a booking that actually has a deposit owed - Charge.security is the
        # actual source of truth (see its own docstring, bookings/models.py), not recomputed here.
        # Any leg, not just the primary - see this function's own docstring.
        'show_security_deposit': not cancelled and any(
            getattr(leg, 'charges', None) and leg.charges.security for leg in legs
        ),
        # Always shown once not cancelled (same style as show_security_deposit) - the page itself
        # handles "no party yet"/"nothing owed"/"already paid", no need to hide the link for those.
        'show_tourist_tax': not cancelled,
        # Narrower than show_tourist_tax above - only for the hub landing page's own explainer
        # bullet (2026-09-08, per Thomas: "if it's applicable"), which unlike the sidebar link isn't
        # meant to always show and let the destination page explain "nothing due" - worth mentioning
        # up front only when the booking's own dates actually fall in tourist-tax season.
        'tourist_tax_in_season': not cancelled and tourist_tax_in_season(booking),
        # Online-direct only. NOT hasattr('charges') alone - fixed 2026-09-08, per Thomas: a
        # platform-synced booking DOES get a Charge row too (platform_fee/basic_rental, needed for
        # Owner Payout accounting - see sync_ical_link()), so that check let Edit Dates show for
        # every platform booking despite the docstring's original "a platform-synced booking has
        # no Charge" assumption being false. enquiry_source (same signal show_cancel_booking
        # already uses) is the real "online-direct" test; hasattr('charges') stays as a second
        # guard against the one legacy-migrated direct booking with no Charge row at all, so this
        # view never crashes reading booking.charges. Same not-cancelled/not-started guard as
        # show_cancel_booking - editing dates on a cancelled or already-arrived stay makes no sense
        # either, and a platform booking's dates are owned by iCal sync, not a guest edit (see
        # BookingManageDatesView).
        'show_edit_dates': (
            not cancelled
            and booking.enquiry_source not in env_settings.PLATFORMS
            and hasattr(booking, 'charges')
            and booking.arrival_date > timezone.now().date()
        ),
        'cancelled': cancelled,
        'stage': 'fully_paid' if is_fully_paid(booking) else 'pre_balance',
    }


def _manage_hub_context(bookings):
    """Context for the hub's landing ("Booking") section. `bookings` is every leg of the stay
    (bookings_for_stay_reference()) - one for a normal single-property booking (unchanged output,
    keyed entirely off that one booking, same as before this function took a list), two-or-more
    for a multi-property ReservationGroup, which additionally gets a `legs` list (one
    booking_confirmation_context() per apartment, rendered by manage_hub.html as a card per leg
    instead of the single-leg confirmation-details block) alongside the primary (first) leg's own
    flat context - keeps the page <title>, the platform/direct welcome-note section, and every
    sidebar link (still per-leg for now - Contact Details/Extras/etc aren't merged yet, later
    stages) working exactly as they did for a single booking, now just anchored on bookings[0]
    rather than the only booking there is.

    Sweeps pending SupplementaryPayments across every leg, not just the primary one - a guest who
    paid for a date-change/guest-addition on their SECOND apartment and closed the tab needs that
    applied the moment they next load this page too, same reasoning _manage_nav_context()'s own
    sweep already documents for the single-booking case. _manage_nav_context() runs after (it
    re-sweeps the primary leg, harmlessly idempotent) so booking_confirmation_context() below sees
    the up-to-date state, not a stale snapshot from before either sweep ran."""
    for booking in bookings:
        for payment in booking.supplementary_payments.filter(status='paid', applied_at__isnull=True):
            payment.apply(booking=booking)

    primary = bookings[0]
    nav_context = _manage_nav_context(primary, 'booking', all_bookings=bookings)
    context = booking_confirmation_context(primary)
    context.update(nav_context)
    if len(bookings) > 1:
        context['legs'] = [booking_confirmation_context(booking) for booking in bookings]
        context['stay_reference'] = primary.reservation_group.reference
    return context


class BookingManageHubView(View):
    """The self-service "Manage Your Booking" hub's landing page - reached either via
    ManageBookingView's email-gated lookup, or directly by a bookmarked/emailed reference link
    (bearer-readable by reference alone, same norm as every other post-deposit view in this file).
    Just the booking summary; Guest List/Arrival & Departure/Extras are their own sidebar-navigated
    sections (BookingManageGuestsView/BookingManageArrivalDepartureView/BookingManageExtrasView),
    each independently editable as soon as the deposit is paid - see the plan this was built from
    for why that's a second, parallel entry point to the same edits BookingBalanceDetailsView
    already supports, not a replacement for it. Pay Balance in the sidebar links to
    bookings:balance_details (unchanged) rather than straight to payment, preserving that existing
    review-before-pay click-through.

    2026-09-14 - `reference` can now also be a ReservationGroup's own shared reference (see
    bookings_for_stay_reference()), landing on one merged page listing every apartment in the
    party instead of forcing the guest to visit each leg's own hub separately (Stage A of the
    merge - deeper sections stay per-leg for now, see project memory)."""
    template_name = 'bookings/manage_hub.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        # Same "not paid yet -> go pay" gate as before, just checked across every leg - the first
        # still-unpaid one (order matches ReservationGroup's own guest-facing pay sequencing, see
        # next_unpaid_sibling_reference()) is where the guest actually needs to go next.
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)
        return render(request, self.template_name, _manage_hub_context(bookings))


class BookingManageContactDetailsView(View):
    """Contact Details section of the Manage Booking hub - self-service edit of the lead guest's
    own email/phone, mirroring owners.views.OwnerContactDetailsView (added 2026-09-07) for guests,
    per Thomas 2026-09-08. Available any time once the deposit is paid, no stage/cutoff
    distinction - same as Arrival & Departure.

    Unlike Owner.email/phone/nif_number, Guest.email/phone carry no unique constraint (a family
    booking under one lead guest can already share a phone/email with other Guest rows), so
    there's nothing here like staff.views._flash_validation_error's unique-collision handling -
    GuestContactDetailsForm's own EmailField format validation is the only check.

    Phone is posted as two fields (phone_country_code, phone) and joined back into the single
    Guest.phone string by the form's own clean() - see libraries/phone_country_codes.py.

    NB: this site has no guest accounts - ManageBookingView's reference+email lookup matches
    against whatever Guest.email currently holds. Unlike Owner.email (a separate User.username
    backs owner login), a guest who changes their email here must use the new address for that
    lookup afterward. Not flagged in the UI - every hub page is normally reached via an emailed
    link, not the lookup form, so this is a much rarer path than the equivalent question was for
    owners (whose portal login IS the email).

    2026-09-14 (Stage C of the multi-property hub merge - see project memory): needs no per-leg
    loop at all, unlike the Holiday Info sections - create_booking()'s existing "filter-then-create
    by email" Guest lookup already means every leg of a ReservationGroup shares the exact same
    Guest row (confirmed directly against the live DB, not assumed), so editing it via the primary
    leg here already updates what every leg sees. The only change needed was accepting the group's
    own reference and using the primary leg for the form/gating."""
    template_name = 'bookings/manage_contact_details.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        phone_code, phone_local = split_phone(booking.guest.phone)
        form = GuestContactDetailsForm(initial={
            'email': booking.guest.email, 'phone_country_code': phone_code, 'phone': phone_local,
        })
        context = {'booking': booking, 'form': form}
        context.update(_manage_nav_context(booking, 'contact_details', all_bookings=bookings))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        form = GuestContactDetailsForm(request.POST)
        if form.is_valid():
            guest = booking.guest
            guest.email = form.cleaned_data['email']
            guest.phone = form.cleaned_data['phone'] or None
            guest.save(update_fields=['email', 'phone'])
            url = reverse('bookings:manage_contact_details', kwargs={'reference': reference})
            return redirect(f"{url}?saved=1")

        context = {'booking': booking, 'form': form}
        context.update(_manage_nav_context(booking, 'contact_details', all_bookings=bookings))
        return render(request, self.template_name, context)


class BookingManageGuestsView(BookingFormMixin, View):
    """Guest List section of the Manage Booking hub - reachable as soon as the deposit is paid, no
    balance-paid gate. Behavior branches on stage (see is_fully_paid()):

    pre_balance: a full editable guest-list form, same underlying save mechanics
    BookingBalanceDetailsView already uses (_parse_rows/recalculate_balance_for_party/the
    price-change interstitial/_save_guest_list) - a second, independent entry point to the same
    edit, not a replacement (see the plan this was built from). Unlike that view, saving here
    redirects back to this same section rather than auto-advancing to payment - Pay Balance is now
    its own deliberate sidebar action.

    fully_paid: a Remove control on each non-lead party row (POSTing to the separate
    BookingManageGuestRemoveView) plus an add-guest mini-form (POSTing to
    BookingManageGuestAddView) - both GuestListAdjustment-tracked, both their own separate views -
    this view's own POST only exists for the pre_balance case.

    2026-09-15 (Stage D5 of the multi-property hub merge - see project memory): a multi-property
    stay renders one full, independent copy of whichever treatment each apartment's OWN stage calls
    for (`legs`, see _guests_leg_context()), each form carrying a hidden leg_reference so this
    view's POST knows which apartment it's acting on (_leg_for_post()). The two legs can genuinely
    be at different stages simultaneously - one apartment's balance paid and frozen, the other
    still repricing on every edit - so the merged page really can show the editable form and the
    add/remove controls side by side; that's the intended behaviour, not a state to collapse. Every
    write path stays strictly per-apartment: each leg keeps its own Charge, BalancePayment,
    GuestListAdjustment trail and max_guests, and nothing here ever writes across legs."""
    template_name = 'bookings/manage_guests.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        _sweep_supplementary_payments(bookings)
        primary = bookings[0]
        context = _manage_nav_context(primary, 'guests', all_bookings=bookings)
        context.update(self._guests_leg_context(primary))
        if len(bookings) > 1:
            context['legs'] = self._merged_guest_legs(bookings)
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = _leg_for_post(bookings, request.POST.get('leg_reference'))
        if booking is None:
            return redirect('bookings:manage_guests', reference=reference)

        primary = bookings[0]
        nav = _manage_nav_context(primary, 'guests', all_bookings=bookings)
        # This POST is the pre_balance treatment's own; the fully-paid one goes to the separate
        # add/remove views. Checked against the SUBMITTING leg's stage, not the primary's.
        if is_fully_paid(booking):
            return redirect('bookings:manage_guests', reference=reference)

        max_guests = booking.property.specs.max_guests
        rows, non_field_error = self._parse_rows(request.POST)

        def rendered(**overrides):
            """The merged page re-rendered with this leg's own typed rows/errors kept in place
            (and the single-property page rendered exactly as before)."""
            leg_state = {'rows': rows, 'max_guests': max_guests, **overrides}
            context = {'booking': booking, **leg_state}
            context.update(nav)
            context['stage'] = 'pre_balance'
            if len(bookings) > 1:
                context['legs'] = self._merged_guest_legs(bookings, target=booking, overrides=leg_state)
            return render(request, self.template_name, context)

        if non_field_error or any(row['errors'] for row in rows):
            return rendered(non_field_error=non_field_error)

        if len(rows) > max_guests:
            return rendered(non_field_error=f"This property allows a maximum of {max_guests} guests.")

        ages = [int(row['age']) for row in rows]
        new_guests, new_costs, changed = recalculate_balance_for_party(booking, ages)
        if new_guests is None:
            return rendered(non_field_error=(
                "This stay can no longer be priced automatically - please contact us to complete your booking."
            ))
        if new_guests['adults'] == 0:
            return rendered(non_field_error="At least one adult must be included in the party.")

        if changed and request.POST.get('confirmed') != '1':
            return rendered(price_changed=True, old_charge=booking.charges, new_costs=new_costs)

        with transaction.atomic():
            self._save_guest_list(booking, rows, new_guests)

            charge = booking.charges
            charge.basic_rental = new_costs['basic_rental']
            charge.discount_total = new_costs['discount_total']
            charge.extra_guest_total = new_costs['extra_guest_total']
            charge.admin = new_costs['admin_fee']
            # security deliberately NOT touched here - see the equivalent comment in
            # BookingDetailsView above.
            charge.due_at_balance = new_costs['due_at_balance']
            charge.save(update_fields=[
                'basic_rental', 'discount_total', 'extra_guest_total', 'admin', 'due_at_balance',
            ])

            # Same reason BookingBalanceDetailsView clears this - a stale checkout URL would
            # otherwise leave the guest paying an amount that no longer matches Charge.
            balance_payment = booking.balance_payment
            if changed and balance_payment.revolut_checkout_url:
                balance_payment.revolut_order_id = None
                balance_payment.revolut_checkout_url = None
                balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])

        # Back to whichever reference the guest is actually browsing (the shared one for a
        # multi-property stay), carrying the saved leg so the merged page can put its "updated"
        # note against the right apartment rather than ambiguously at the top. Still simply
        # truthy for the single-property page's own existing check.
        url = reverse('bookings:manage_guests', args=[reference])
        return redirect(f"{url}?guests_saved={booking.reference}")


def _arrival_data_from_model(arrival):
    # A legacy row flagged time_unknown pre-fills blank, not "00:00" - showing the old sentinel
    # value back to the guest would read as "you already told us midnight", when we actually don't
    # know it at all (see Arrival.time_unknown's own docstring).
    has_real_time = arrival and arrival.time and not arrival.time_unknown
    return {
        'method': arrival.method if arrival else TravelMethod.FLIGHT_FARO,
        'flight_number': arrival.flight_number if arrival else '',
        'travelling_from': arrival.travelling_from if arrival else '',
        'hiring_car': arrival.hiring_car if arrival else False,
        'time': arrival.time.strftime('%H:%M') if has_real_time else '',
        'details': arrival.details if arrival else '',
    }


def _departure_data_from_model(departure):
    return {
        'method': departure.method if departure else TravelMethod.FLIGHT_FARO,
        'flight_number': departure.flight_number if departure else '',
        'travelling_from': departure.travelling_from if departure else '',
        'time': departure.time.strftime('%H:%M') if departure and departure.time else '',
        'details': departure.details if departure else '',
    }


def _arrival_data_from_post(post_data):
    return {
        'method': parsed_travel_method(post_data.get('arrival_method')),
        'flight_number': post_data.get('arrival_flight_number', '').strip(),
        'travelling_from': post_data.get('arrival_travelling_from', '').strip(),
        'hiring_car': post_data.get('arrival_hiring_car') == 'yes',
        'time': post_data.get('arrival_time', '').strip(),
        'details': post_data.get('arrival_details', '').strip()[:140],
    }


def _departure_data_from_post(post_data):
    return {
        'method': parsed_travel_method(post_data.get('departure_method')),
        'flight_number': post_data.get('departure_flight_number', '').strip(),
        'travelling_from': post_data.get('departure_travelling_from', '').strip(),
        'time': post_data.get('departure_time', '').strip(),
        'details': post_data.get('departure_details', '').strip()[:140],
    }


def _arrival_departure_field_context(arrival_data, departure_data):
    """Just the arrival/departure form-field keys _arrival_departure_form.html needs - deliberately
    no 'booking' key or nav context here, since BookingBalanceDetailsView's own context already has
    those and shouldn't get BookingManageArrivalDepartureView's sidebar nav mixed in."""
    return {
        'arrival_travel_methods': TravelMethod.choices,
        'departure_travel_methods': TravelMethod.departure_choices(),
        'arrival_method': arrival_data['method'],
        'arrival_flight_number': arrival_data['flight_number'],
        'arrival_travelling_from': arrival_data['travelling_from'],
        'arrival_hiring_car': arrival_data['hiring_car'],
        'arrival_time': arrival_data['time'],
        'arrival_details': arrival_data['details'],
        'departure_method': departure_data['method'],
        'departure_flight_number': departure_data['flight_number'],
        'departure_travelling_from': departure_data['travelling_from'],
        'departure_time': departure_data['time'],
        'departure_details': departure_data['details'],
    }


def _arrival_departure_flight_number_errors(arrival_data, departure_data):
    errors = {}
    if not valid_flight_number(arrival_data['method'], arrival_data['flight_number']):
        errors['arrival_flight_number'] = FLIGHT_NUMBER_HINT
    if not valid_flight_number(departure_data['method'], departure_data['flight_number']):
        errors['departure_flight_number'] = FLIGHT_NUMBER_HINT
    return errors


def _save_arrival(booking, data):
    arrival, _ = Arrival.objects.get_or_create(booking=booking, defaults={
        'self_check_in': False, 'meet_greet': True,
    })
    arrival.method = data['method']
    arrival.flight_number = data['flight_number']
    arrival.travelling_from = data['travelling_from']
    arrival.hiring_car = data['hiring_car']
    arrival.time = parsed_arrival_departure_time(data['time'])
    arrival.details = data['details']
    # A fresh save's own `time` (even a genuine None) is never a legacy time(0,0) placeholder -
    # see Arrival.time_unknown's own docstring.
    arrival.time_unknown = False
    update_fields = [
        'method', 'flight_number', 'travelling_from', 'hiring_car', 'time', 'time_unknown', 'details',
    ]
    computed_self_check_in = compute_effective_self_check_in(
        booking.property, arrival.method, arrival.time, arrival.time_unknown,
    )
    if computed_self_check_in is not None:
        arrival.self_check_in = computed_self_check_in
        update_fields.append('self_check_in')
    arrival.save(update_fields=update_fields)


def _save_departure(booking, data):
    departure, _ = Departure.objects.get_or_create(booking=booking, defaults={'clean': True})
    departure.method = data['method']
    departure.flight_number = data['flight_number']
    departure.travelling_from = data['travelling_from']
    departure.time = parsed_arrival_departure_time(data['time'])
    departure.details = data['details']
    departure.save(update_fields=['method', 'flight_number', 'travelling_from', 'time', 'details'])


class BookingManageArrivalDepartureView(View):
    """Arrival & Departure section of the Manage Booking hub - available any time once the deposit
    is paid, no cutoff (unlike Extras) and no stage distinction (unlike Guest List) - purely
    informational, doesn't touch Charge or any pricing, so there's no reason to lock it the way
    Extras is locked for fulfilment lead time. method is a guest-chosen TravelMethod (flight to
    Faro/Lisbon, bus, train, driving, other) set independently for arrival and departure - falls
    back to FLIGHT_FARO if missing/invalid, this section has never hard-required fields and
    shouldn't start now. Same stored TravelMethod values for both directions, but the dropdown
    label wording differs (TravelMethod.departure_choices()) since "Flight to Faro" reads
    backwards for a departing guest. details is capped at 140 chars server-side (also enforced via
    maxlength in the template) - a short note, not a support channel. flight_number IS validated
    (see valid_flight_number()) - the one hard validation on this page; on failure the page
    re-renders with the guest's other entries preserved, same pattern as
    BookingManageGuestAddView's row errors. self_check_in/meet_greet on Arrival and clean on
    Departure are all staff/ops-only - only ever supplied as creation defaults (via
    get_or_create), never touched on a later guest save, so a staff edit made on the staff booking
    detail page afterward is never clobbered. The module-level _arrival_data_from_model()/
    _save_arrival()/etc. helpers above
    are shared with BookingBalanceDetailsView, which embeds the same _arrival_departure_form.html
    partial as a second entry point to these same rows (see that view's docstring).

    2026-09-14 (Stage C of the multi-property hub merge - see project memory): one shared form
    covering the whole party's travel plans (same flight/arrival almost always applies to every
    apartment) - a submit here calls _save_arrival()/_save_departure() once per leg of the stay,
    not just the one in the URL, so the two apartments' Arrival/Departure rows can never drift out
    of sync the way editing them independently would let happen. Each call still computes
    self_check_in per-property (compute_effective_self_check_in reads booking.property), so this
    stays correct even when the two apartments have different check-in policies."""
    template_name = 'bookings/manage_arrival_departure.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        arrival = getattr(booking, 'arrival', None)
        departure = getattr(booking, 'departure', None)
        context = {'booking': booking}
        context.update(_arrival_departure_field_context(
            _arrival_data_from_model(arrival), _departure_data_from_model(departure),
        ))
        context.update(_manage_nav_context(booking, 'arrival_departure', all_bookings=bookings))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        arrival_data = _arrival_data_from_post(request.POST)
        departure_data = _departure_data_from_post(request.POST)

        errors = _arrival_departure_flight_number_errors(arrival_data, departure_data)
        if errors:
            context = {'booking': booking}
            context.update(_arrival_departure_field_context(arrival_data, departure_data))
            context.update(_manage_nav_context(booking, 'arrival_departure', all_bookings=bookings))
            context['errors'] = errors
            return render(request, self.template_name, context)

        for leg in bookings:
            _save_arrival(leg, arrival_data)
            _save_departure(leg, departure_data)

        return redirect(f"{reverse('bookings:manage_arrival_departure', args=[reference])}?saved=1")


class BookingManageDatesView(View):
    """Self-serve stay-date editing section of the Manage Booking hub (2026-09, per Thomas) -
    online-direct bookings only (see _manage_nav_context()'s show_edit_dates gate - a platform
    booking's dates are owned by iCal sync, not a guest edit; it DOES still get a Charge row of
    its own for payout accounting, so enquiry_source, not hasattr('charges'), is what actually
    excludes it here - fixed 2026-09-08, per Thomas, after this let a platform guest edit their
    stay dates directly), any time from deposit-paid onward. Branches on is_balance_paid(booking),
    same style as BookingManageGuestsView/recalculate_costs_for_dates():

    pre_balance: any price change (up or down) just moves due_at_balance - nothing's been
    collected for the balance stage yet, so there's nothing to check out online for. Same
    confirm-if-price-changed interstitial as BookingBalanceDetailsView.

    fully_paid: a lower-or-equal price applies immediately - no refund, matching this codebase's
    no-refund-after-payment stance (see GuestListAdjustment's own docstring). A higher price is
    staged onto a SupplementaryPayment and the guest is sent to pay it online before the dates
    actually change - see that model's own docstring for why (no more cash-in-hand for a
    management-side charge like this)."""
    template_name = 'bookings/manage_dates.html'

    def _gate(self, request, reference):
        """(bookings, redirect_or_None). Every leg has to qualify, not just the primary: the party
        moves together, so a stay with any platform-sourced or Charge-less leg can't be date-edited
        here at all."""
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return bookings, merged
        blocked = any(
            not is_paid(leg)
            or leg.enquiry_source in env_settings.PLATFORMS
            or not hasattr(leg, 'charges')
            for leg in bookings
        )
        if blocked:
            return bookings, redirect('bookings:manage_hub', reference=reference)
        return bookings, None

    def get(self, request, reference, *args, **kwargs):
        bookings, redirect_response = self._gate(request, reference)
        if redirect_response is not None:
            return redirect_response
        booking = bookings[0]

        context = {
            'booking': booking,
            'arrival_value': booking.arrival_date.strftime('%d/%m/%Y'),
            'departure_value': booking.departure_date.strftime('%d/%m/%Y'),
        }
        context.update(_manage_nav_context(booking, 'dates', all_bookings=bookings))
        context.update(self._calendar_context(booking, bookings))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        bookings, redirect_response = self._gate(request, reference)
        if redirect_response is not None:
            return redirect_response
        booking = bookings[0]

        arrival_raw = request.POST.get('arrival', '').strip()
        departure_raw = request.POST.get('departure', '').strip()

        context = {'booking': booking, 'arrival_value': arrival_raw, 'departure_value': departure_raw}
        context.update(_manage_nav_context(booking, 'dates', all_bookings=bookings))

        def error(message):
            context['dates_error'] = message
            context.update(self._calendar_context(booking, bookings))
            return render(request, self.template_name, context)

        try:
            new_arrival = date_string_to_date(arrival_raw)
            new_departure = date_string_to_date(departure_raw)
        except (ValueError, TypeError):
            return error("Please enter valid check-in and check-out dates.")

        if new_departure <= new_arrival:
            return error("Check-out must be after check-in.")
        if new_arrival < timezone.now().date():
            return error("Check-in can't be in the past.")

        # Checked before the overlap conflict check below (which would otherwise self-conflict
        # against this booking's own hold) - a booking only ever has one pending date_change at a
        # time; a second submission while one's already in flight goes straight back to paying for
        # it, not through a fresh availability check for (possibly overlapping) new dates.
        # A stay only ever has one date change in flight at a time - a second submission while one
        # is still being paid for goes straight back to paying it, not through a fresh availability
        # check for (possibly overlapping) new dates. Checked across every leg and before the
        # overlap check below, which would otherwise self-conflict against the stay's own hold.
        existing_payment = SupplementaryPayment.objects.filter(
            booking__in=bookings, kind='date_change', status__in=('pending', 'in_progress'),
            hold_expires_at__gt=timezone.now(),
        ).order_by('pk').first()
        if existing_payment is not None:
            return redirect(
                'bookings:manage_supplementary_pay',
                reference=existing_payment.booking.reference, payment_id=existing_payment.pk,
            )

        # Every apartment has to be free on the new dates - the party moves together, so one
        # unavailable apartment blocks the whole change. Named, so the guest knows which.
        for leg in bookings:
            conflict = Booking.objects.overlapping(
                leg.property, new_arrival, new_departure,
            ).exclude(pk__in=[b.pk for b in bookings]).exists() or SupplementaryPayment.objects.overlapping_dates(
                leg.property, new_arrival, new_departure,
            ).exists()
            if conflict:
                if len(bookings) > 1:
                    return error(
                        f"Those dates aren't available for {leg.property} - please choose another range. "
                        f"Both apartments need to be free for the same dates."
                    )
                return error("Those dates aren't available for this property - please choose another range.")

        priced = []
        for leg in bookings:
            new_costs, changed = recalculate_costs_for_dates(leg, new_arrival, new_departure)
            if new_costs is None:
                return error(
                    "These dates can no longer be priced automatically - please contact us to change them."
                )
            priced.append({'booking': leg, 'charge': leg.charges, 'new_costs': new_costs, 'changed': changed})

        confirmed = request.POST.get('confirmed') == '1'
        any_changed = any(entry['changed'] for entry in priced)

        # Split by each leg's OWN balance state - the two apartments of one party can genuinely be
        # in different states at once, exactly as on the merged Guest List. A leg whose balance is
        # still due just has that balance moved (up or down); a leg already paid in full can only
        # ever be asked for MORE, never refunded - per Thomas, "no refunds, only smaller balance
        # payments if applicable".
        for entry in priced:
            charge, new_costs = entry['charge'], entry['new_costs']
            entry['balance_paid'] = is_balance_paid(entry['booking'])
            entry['price_diff'] = (
                (new_costs['rental_total'] + new_costs['admin_fee']) - (charge.total_rental + charge.admin)
            )
        amount_due_now = sum(
            (entry['price_diff'] for entry in priced if entry['balance_paid'] and entry['price_diff'] > 0),
            Decimal('0'),
        )

        # Confirm only when there's something for the guest to actually agree to: a balance that
        # moves (either way) on a leg that hasn't paid it yet, or real money now owed. A leg whose
        # balance is already paid getting CHEAPER is applied straight away with no interstitial and
        # no refund - exactly as the single-property flow has always done.
        pre_balance_changed = any(entry['changed'] for entry in priced if not entry['balance_paid'])
        if (pre_balance_changed or amount_due_now > 0) and not confirmed:
            context['price_changed'] = True
            context['old_charge'] = priced[0]['charge']
            context['new_costs'] = priced[0]['new_costs']
            if amount_due_now > 0:
                context['price_increase'] = True
                context['price_diff'] = amount_due_now
            if len(bookings) > 1:
                context['priced_legs'] = priced
            context.update(self._calendar_context(booking, bookings))
            return render(request, self.template_name, context)

        if amount_due_now > 0:
            return self._stage_date_change(bookings, priced, new_arrival, new_departure, amount_due_now)

        with transaction.atomic():
            for entry in priced:
                leg, charge, new_costs = entry['booking'], entry['charge'], entry['new_costs']
                if entry['balance_paid']:
                    # Dates only, never Charge - see _apply_dates_only()'s docstring for why
                    # writing the cheaper price down here would be a real bug, not a cosmetic one.
                    self._apply_dates_only(leg, new_arrival, new_departure)
                    continue
                self._apply_dates_and_charge(leg, charge, new_arrival, new_departure, new_costs)
                # Same stale-checkout-URL guard as BookingBalanceDetailsView.post() - a guest who
                # already generated a balance checkout link at the old amount must not be able to
                # pay that stale amount after changing their dates.
                balance_payment = getattr(leg, 'balance_payment', None)
                if entry['changed'] and balance_payment and balance_payment.revolut_checkout_url:
                    balance_payment.revolut_order_id = None
                    balance_payment.revolut_checkout_url = None
                    balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])

        url = reverse('bookings:manage_dates', args=[reference])
        return redirect(f"{url}?dates_updated=1")

    def _stage_date_change(self, bookings, priced, new_arrival, new_departure, amount_due_now):
        """Money is owed on at least one apartment, so NOTHING moves until it's paid.

        A SupplementaryPayment is staged for EVERY leg, not just the ones with something to pay -
        including zero-amount ones. That's what keeps the party's dates moving together: the whole
        change is held, and the sweep applies every leg's staged dates/charge at once when the
        payment lands. Moving the free legs immediately and leaving the paid-for one behind would
        split one party across two date ranges if the guest never paid.

        The legs share ONE Revolut order (created by BookingManageSupplementaryPaymentView for the
        summed amount), the same trick Stage D4 uses for Tourist Tax: klt-hooks'
        mark_supplementary_payment_paid() does an unlimited `UPDATE ... WHERE revolut_order_id`,
        so one guest payment marks every row paid. Each leg still keeps its own row with its own
        amount, so each apartment's owner revenue stays correctly attributed - the shared order is
        only how the money is collected."""
        primary_charge = priced[0]['charge']
        currency = primary_charge.currency
        provider, hold_expires_at = compute_initial_hold_expiry(new_arrival, BookingSettings.load())

        created = []
        with transaction.atomic():
            for entry in priced:
                leg, charge, new_costs = entry['booking'], entry['charge'], entry['new_costs']
                owed = entry['price_diff'] if entry['balance_paid'] and entry['price_diff'] > 0 else Decimal('0')
                pay_amount = charge.to_gbp(owed) if currency == 'GBP' else owed
                # Charge is restaged with the new price EXCEPT for an already-paid leg getting
                # cheaper: there's no refund, so its Charge must stay pinned to what was actually
                # collected. Writing the lower price down would erase the record of the unrefunded
                # excess, making a later change back towards the original dates look like a fresh
                # increase - see _apply_dates_only()'s docstring for the same reasoning applied to
                # the immediate (nothing-owed) path.
                dates_only = entry['balance_paid'] and entry['price_diff'] <= 0
                created.append(SupplementaryPayment.objects.create(
                    booking=leg, kind='date_change', amount=pay_amount,
                    currency='GBP' if currency == 'GBP' else 'EUR',
                    provider=provider, hold_expires_at=hold_expires_at,
                    new_arrival_date=new_arrival, new_departure_date=new_departure,
                    pending_charge_fields=None if dates_only else {
                        'basic_rental': new_costs['basic_rental'],
                        'discount_total': new_costs['discount_total'],
                        'extra_guest_total': new_costs['extra_guest_total'],
                        'admin': new_costs['admin_fee'],
                        'due_at_balance': new_costs['due_at_balance'],
                    },
                ))

        # The checkout page is reached via whichever row is first - it sums its own siblings and
        # charges once for the party, so which one the guest lands on doesn't change what they pay.
        first = created[0]
        return redirect(
            'bookings:manage_supplementary_pay', reference=first.booking.reference, payment_id=first.pk,
        )

    def _apply_dates_and_charge(self, booking, charge, new_arrival, new_departure, new_costs):
        booking.arrival_date = new_arrival
        booking.departure_date = new_departure
        booking.manual_override = True
        booking.save(update_fields=['arrival_date', 'departure_date', 'manual_override'])

        charge.basic_rental = new_costs['basic_rental']
        charge.discount_total = new_costs['discount_total']
        charge.extra_guest_total = new_costs['extra_guest_total']
        charge.admin = new_costs['admin_fee']
        charge.due_at_balance = new_costs['due_at_balance']
        charge.save(update_fields=[
            'basic_rental', 'discount_total', 'extra_guest_total', 'admin', 'due_at_balance',
        ])

    def _apply_dates_only(self, booking, new_arrival, new_departure):
        """Used for a post-balance-paid price decrease: moves the booking's dates without
        touching Charge at all, since there's no refund to reconcile it against - see the
        price_diff <= 0 branch above for why overwriting it here would be a real bug, not a
        cosmetic one."""
        booking.arrival_date = new_arrival
        booking.departure_date = new_departure
        booking.manual_override = True
        booking.save(update_fields=['arrival_date', 'departure_date', 'manual_override'])

    def _occupied_ranges(self, booking):
        today = timezone.now().date()
        return Booking.objects.holding().filter(
            property=booking.property, departure_date__gte=today,
        ).exclude(pk=booking.pk).values_list('arrival_date', 'departure_date')

    def _calendar_context(self, booking, bookings=None):
        """occupied_ranges is inlined as JSON for manage_dates.js to feed straight into the date
        pickers' disabledRanges (see static/pickers/dates.js) - no separate endpoint, since the
        guest already has to reload this page to see fresh availability anyway (same server-
        rendered norm as the rest of this app, no live/SPA refresh anywhere else either).
        calendar_months reuses the same property-calendar builder the property page itself uses,
        with this booking's own current stay highlighted as a distinct 'mine' status.

        Multi-property (2026-09-15): the PICKERS get the UNION of every apartment's occupied
        ranges, because the party moves together - a date that isn't free in both apartments isn't
        offerable at all, so the guest simply can't pick it rather than picking it and being
        refused. The visible month grids stay one PER APARTMENT (`calendar_legs`), since that's
        what explains *why* a week is blocked; a single merged grid would show the same disabled
        weeks with no way to tell which apartment caused them."""
        legs = bookings or [booking]
        occupied = []
        for leg in legs:
            occupied.extend(self._occupied_ranges(leg))
        context = {
            'occupied_ranges_json': json.dumps([
                [arrival.strftime('%d/%m/%Y'), departure.strftime('%d/%m/%Y')]
                for arrival, departure in occupied
            ]),
            'calendar_months': get_property_calendar(
                booking.property, mine_range=(booking.arrival_date, booking.departure_date),
            ),
        }
        if len(legs) > 1:
            context['calendar_legs'] = [
                {
                    'booking': leg,
                    'calendar_months': get_property_calendar(
                        leg.property, mine_range=(leg.arrival_date, leg.departure_date),
                    ),
                }
                for leg in legs
            ]
        return context


class BookingManageGuestAddView(BookingFormMixin, View):
    """Guest-list *increases* once a booking is already fully paid (see is_fully_paid()) - the
    hub's only guest-list write path at that point, since Charge is frozen for good by then. Never
    edits or deletes an existing BookingGuest row - only ever appends new ones, each tagged
    added_via_adjustment so the charge owed for them is traceable back to its GuestListAdjustment
    audit row. GET has nothing to show on its own, so it just bounces back to the Guest List
    section; POST is stateless (two-step confirm via a repeated `confirmed` field, not session
    state) for the same reason BookingBalanceDetailsView's POST is - this is reached from a
    bookmarked/emailed link days or weeks later, no session continuity to lean on.

    A guest added within the property's base occupancy (additional_charge == 0) is appended
    immediately - nothing to collect. One that crosses into extra-guest-fee territory is staged
    onto a SupplementaryPayment and the guest is sent to pay it online first (2026-09, per Thomas:
    a management-side charge like this is no longer cash-at-check-in) - see that model's own
    docstring; the row is only actually appended once that payment is paid
    (apply_supplementary_payment(), via _manage_nav_context()'s sweep)."""
    template_name = 'bookings/manage_guests.html'

    def get(self, request, reference, *args, **kwargs):
        return redirect('bookings:manage_guests', reference=reference)

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        booking = _leg_for_post(bookings, request.POST.get('leg_reference'))
        if booking is None or not is_fully_paid(booking):
            return redirect('bookings:manage_guests', reference=reference)

        max_guests = booking.property.specs.max_guests
        new_rows, non_field_error = self._parse_rows(request.POST)
        nav = _manage_nav_context(bookings[0], 'guests', all_bookings=bookings)

        def rendered(**overrides):
            """The merged page re-rendered with this apartment's own add-form state kept in
            place (and the single-property page rendered exactly as before)."""
            leg_state = {
                'party': list(booking.party.all()), 'max_guests': max_guests,
                'guest_add_rows': new_rows, **overrides,
            }
            context = {'booking': booking, **leg_state}
            context.update(nav)
            context['stage'] = 'fully_paid'
            if len(bookings) > 1:
                context['legs'] = self._merged_guest_legs(bookings, target=booking, overrides=leg_state)
            return render(request, self.template_name, context)

        if non_field_error or any(row['errors'] for row in new_rows):
            return rendered(guest_add_error=non_field_error)

        existing_party = list(booking.party.all())
        if len(existing_party) + len(new_rows) > max_guests:
            return rendered(guest_add_error=f"This property allows a maximum of {max_guests} guests.")

        ages = [guest.age for guest in existing_party] + [int(row['age']) for row in new_rows]
        new_guests, new_costs, _ = recalculate_costs_for_party(booking, ages)
        if new_guests is None:
            return rendered(guest_add_error=(
                "This stay can no longer be priced automatically - please contact us to add a guest."
            ))

        charge = booking.charges
        additional_charge = max(new_costs['subtotal'] - (charge.total_rental + charge.admin), Decimal('0'))

        # Only worth an extra confirm click when it's actually asking the guest to accept a
        # charge - added guests still within the property's base occupancy (no per-extra-guest
        # rate kicks in) cost nothing, so there's nothing to confirm (same "skip the interstitial
        # when there's no price change to review" spirit as Extras never having one at all - see
        # BookingManageExtrasView's own docstring).
        if additional_charge > 0 and request.POST.get('confirmed') != '1':
            return rendered(pending_guest_addition={'rows': new_rows, 'additional_charge': additional_charge})

        if additional_charge > 0:
            pay_amount, pay_currency = (
                (charge.to_gbp(additional_charge), 'GBP') if charge.currency == 'GBP' else (additional_charge, 'EUR')
            )
            payment = SupplementaryPayment.objects.create(
                booking=booking, kind='guest_add', amount=pay_amount, currency=pay_currency,
                provider=determine_payment_provider(booking.arrival_date),
                pending_guest_rows=[
                    {'first_name': row['first_name'], 'last_name': row['last_name'], 'age': row['age']}
                    for row in new_rows
                ],
            )
            return redirect('bookings:manage_supplementary_pay', reference=booking.reference, payment_id=payment.pk)

        with transaction.atomic():
            adjustment = GuestListAdjustment.objects.create(
                booking=booking,
                previous_party_size=len(existing_party),
                new_party_size=len(existing_party) + len(new_rows),
                additional_charge=Decimal('0'),
            )
            self._append_guest_rows(booking, new_rows, adjustment, new_guests)

        url = reverse('bookings:manage_guests', args=[reference])
        return redirect(f"{url}?guest_added={booking.reference}")


class BookingManageGuestRemoveView(View):
    """Guest-list *removals* once a booking is already fully paid - the mirror of
    BookingManageGuestAddView. Never touches Charge or issues any refund - removing a guest is a
    pure headcount correction (wrong entry, a guest who can no longer make it), not a request for
    money back, matching this codebase's existing no-refund-after-payment stance (see
    BookingCancelView's own docstring). The lead guest (Booking.guest's own row, is_lead=True) can
    never be removed this way - it's the one party row every other part of the app assumes exists
    - so the template never renders a Remove control next to it, and this re-checks that
    server-side too rather than trusting the template alone. Still creates a GuestListAdjustment
    row (additional_charge=0) purely as an audit entry, same "why did the headcount change" trail
    the add flow already leaves - see that model's own docstring. Also re-syncs
    Booking.adults/children/babies from the remaining party's real ages, same as
    _append_guest_rows() already does on the add side - staff's own booking-detail page reads
    those fields directly, so leaving them stale here (confirmed the hard way: a removed infant
    left Booking.babies sitting at its original count) would silently drift staff's view of the
    booking out of sync with the guest list itself."""

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        booking = _leg_for_post(bookings, request.POST.get('leg_reference'))
        if booking is None or not is_fully_paid(booking):
            return redirect('bookings:manage_guests', reference=reference)

        # Scoped to the submitting apartment's own party, so a guest_id belonging to the OTHER
        # apartment can never be removed through this leg's form.
        guest = booking.party.filter(pk=request.POST.get('guest_id')).first()
        if guest is None or guest.is_lead:
            return redirect('bookings:manage_guests', reference=reference)

        with transaction.atomic():
            party_count = booking.party.count()
            GuestListAdjustment.objects.create(
                booking=booking, previous_party_size=party_count, new_party_size=party_count - 1,
                additional_charge=Decimal('0'),
            )
            remaining_ages = list(booking.party.exclude(pk=guest.pk).values_list('age', flat=True))
            guest.delete()
            counts = guest_counts_by_age(remaining_ages, BookingSettings.load())
            booking.adults = counts['adults']
            booking.children = counts['children']
            booking.babies = counts['infants']
            booking.last_updated = timezone.now()
            booking.save(update_fields=['adults', 'children', 'babies', 'last_updated'])

        url = reverse('bookings:manage_guests', args=[reference])
        return redirect(f"{url}?guest_removed={booking.reference}")


class BookingManageExtrasView(BookingFormMixin, View):
    """Extras section of the Manage Booking hub - reachable as soon as the deposit is paid, same as
    Guest List and Arrival & Departure now (no balance-paid gate). Gated only by
    extras_edit_locked()'s fulfilment-lead-time cutoff, not by payment status, since Extras are
    cash-at-check-in and were never priced into Charge (see extras_summary()'s docstring). No
    price-change interstitial needed here at all for the same reason.

    2026-09-15 (Stage D6 of the multi-property hub merge - see project memory): the last section to
    be merged, and the only one that is deliberately NOT uniformly per-apartment. Per Thomas, a
    party travelling together needs ONE airport transfer, not one per apartment they happen to have
    booked - so Airport Transfers renders once for the whole stay in its own form, while every
    other extra (Cot & High Chair, Late Checkout, Mid-stay Clean, Welcome Pack, Special Requests)
    repeats per apartment under an "Extra - Property" heading, since those are genuinely consumed
    in one specific apartment.

    The shared transfer is stored against ONE leg (`_transfer_leg()`), never duplicated across
    them: AirportTransfer rows are summed per booking by extras_summary() (what the guest pays at
    check-in) and counted per booking by the staff monthly report, so duplicating one would both
    double-charge the guest and double-count the report. The two places that *read* transfers to
    decide what a guest is told - Location & Check-in and Last Days & Check-out - resolve them
    across the whole stay instead (see _stay_transfers()), so the apartment that doesn't hold the
    row still shows the party's transfer rather than "none booked"."""
    template_name = 'bookings/manage_extras.html'

    def _show_cot_high_chair(self, booking):
        """Same infant-age check _any_infant_age() does for a freshly-typed guest-list form, but
        against the booking's actual saved party - this page no longer has a guest-list form on it
        to check ages from directly, since Guest List is now its own separate section."""
        child_min_age = BookingSettings.load().child_min_age
        rows = [{'age': guest.age} for guest in booking.party.all()]
        return self._any_infant_age(rows, child_min_age)

    def _transfer_leg(self, bookings):
        """The one leg a multi-property stay's shared airport transfers are stored against - the
        primary (lowest-pk) leg, the same one every other part of the hub already treats as
        primary. Arbitrary but stable; what matters is that it's exactly one, for the
        double-charging reason in this class's docstring."""
        return bookings[0]

    def _extras_leg_context(self, booking, post_data=None, **overrides):
        """One apartment's worth of per-apartment extras state, in the same shape the single-
        property page has always used at top level."""
        context = {
            'booking': booking,
            'show_cot_high_chair': self._show_cot_high_chair(booking),
            'extras_locked': extras_edit_locked(booking),
            'leg_id_suffix': f"-{booking.reference}",
            'transfers_hoisted': True,
        }
        context.update(self._extras_context(booking, post_data=post_data))
        context.update(overrides)
        return context

    def _merged_extras_legs(self, bookings, target=None, overrides=None):
        """Every leg's _extras_leg_context(), with `overrides` merged into whichever one is
        `target` - the "re-render the whole merged page but keep the submitting apartment's own
        values and errors" path, same as Stage D5's guest-list equivalent."""
        legs = []
        for booking in bookings:
            leg = self._extras_leg_context(booking)
            if target is not None and booking.pk == target.pk and overrides:
                leg.update(overrides)
            legs.append(leg)
        return legs

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        primary = bookings[0]
        context = {'booking': primary, 'extras_locked': extras_edit_locked(primary),
                   'show_cot_high_chair': self._show_cot_high_chair(primary)}
        context.update(_manage_nav_context(primary, 'extras', all_bookings=bookings))
        context.update(self._extras_context(primary))
        context.update(self._transfer_context(self._transfer_leg(bookings)))
        if len(bookings) > 1:
            context['legs'] = self._merged_extras_legs(bookings)
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        # Two kinds of form post here on a merged page: the single shared Airport Transfers form
        # (no leg_reference - it belongs to the stay), and one per-apartment extras form.
        if request.POST.get('form') == 'transfers':
            return self._post_transfers(request, reference, bookings)
        return self._post_extras(request, reference, bookings)

    def _post_transfers(self, request, reference, bookings):
        leg = self._transfer_leg(bookings)
        if extras_edit_locked(leg):
            return redirect('bookings:manage_extras', reference=reference)

        transfer_rows, transfer_non_field_error = self._parse_transfer_rows(request.POST)
        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            primary = bookings[0]
            context = {'booking': primary, 'extras_locked': False,
                       'show_cot_high_chair': self._show_cot_high_chair(primary)}
            context.update(_manage_nav_context(primary, 'extras', all_bookings=bookings))
            context.update(self._extras_context(primary))
            context.update(self._transfer_context(
                leg, rows=transfer_rows, non_field_error=transfer_non_field_error,
            ))
            if len(bookings) > 1:
                context['legs'] = self._merged_extras_legs(bookings)
            return render(request, self.template_name, context)

        with transaction.atomic():
            self._save_transfers(leg, transfer_rows)

        url = reverse('bookings:manage_extras', args=[reference])
        return redirect(f"{url}?extras_saved=transfers")

    def _post_extras(self, request, reference, bookings):
        booking = _leg_for_post(bookings, request.POST.get('leg_reference'))
        if booking is None:
            return redirect('bookings:manage_extras', reference=reference)
        if extras_edit_locked(booking):
            return redirect('bookings:manage_extras', reference=reference)

        # A single-property page still posts everything in one form, transfers included - the
        # merged page splits those into their own form instead (see _post_transfers).
        single = len(bookings) == 1
        transfer_rows, transfer_non_field_error = (
            self._parse_transfer_rows(request.POST) if single else ([], None)
        )
        _, _, late_checkout_error = self._parse_late_checkout(booking, request.POST)
        _, _, mid_stay_clean_error = self._parse_mid_stay_clean(booking, request.POST)

        if (transfer_non_field_error or any(row['errors'] for row in transfer_rows)
                or late_checkout_error or mid_stay_clean_error):
            leg_state = {
                **self._extras_context(booking, post_data=request.POST),
                'late_checkout_error': late_checkout_error,
                'mid_stay_clean_error': mid_stay_clean_error,
            }
            context = {'booking': booking, 'extras_locked': False,
                       'show_cot_high_chair': self._show_cot_high_chair(booking)}
            context.update(_manage_nav_context(bookings[0], 'extras', all_bookings=bookings))
            context.update(leg_state)
            context.update(self._transfer_context(
                self._transfer_leg(bookings),
                rows=transfer_rows if single else None,
                non_field_error=transfer_non_field_error,
            ))
            if not single:
                context['legs'] = self._merged_extras_legs(
                    bookings, target=booking, overrides=leg_state,
                )
            return render(request, self.template_name, context)

        with transaction.atomic():
            self._save_extras(booking, request.POST)
            if single:
                self._save_transfers(booking, transfer_rows)

        url = reverse('bookings:manage_extras', args=[reference])
        return redirect(f"{url}?extras_saved={booking.reference}")


def _parsed_birth_date(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


class BookingManageGuestRegistrationsView(View):
    """Guest Registrations section of the Manage Booking hub - the guest-facing capture step for
    the mandatory Portuguese border-registration (SEF) details Thomas asked for, shown via a
    screenshot of the legacy klt-management-software equivalent form. One section per currently-
    named BookingGuest, not per adults/children/babies headcount - there's nothing to attach the
    details to for a guest who hasn't been named yet, so registering more guests means adding them
    via Guest List first (the blurb on this page says so). First/last name shown read-only,
    prefilled from that same BookingGuest row. Forwarding this on to SEF isn't built yet - see
    GuestRegistration's own docstring. Reachable as soon as the deposit is paid (same gate as
    Guest List/Arrival & Departure/Extras), no edit cutoff - a guest can come back and fix a typo
    any time before departure, same reasoning as Arrival & Departure's own no-cutoff choice.
    Validates server-side (required-ness, a real parseable non-future birth date) rather than
    trusting the required attribute alone, since this ends up as a legal record - on any error the
    whole submission re-renders with every guest's just-typed values preserved (via the in-memory
    GuestRegistration instances, not a separate raw-POST context var) and nothing is saved, rather
    than partially saving whichever guests happened to be valid this time. Only the lead (first)
    guest is asked whether they have a Portuguese NIF - client-side toggle in
    guest_registrations.js, mirroring arrival_departure.js's own show/hide-and-disable pattern -
    and that single answer governs the whole party (confirmed with Thomas, matches how this has
    always been handled operationally): a "yes" means nobody registers at all, not even the lead
    guest's own full form; a "no" reveals the lead guest's full form *and* every other guest's own
    section, each with the same 7 fields, no individual NIF question of their own.

    2026-09-14 (Stage D of the multi-property hub merge - see project memory): each apartment's
    party is entirely separate, so a multi-property stay shows one full, independently-submittable
    form per leg (`legs`, via `_manage_guest_registrations_leg.html`) - same dual-duplicate-form
    pattern as Security Deposit. Each form POSTs back to the STAY's own reference with a hidden
    leg_reference field; BookingGuest primary keys are globally unique (not scoped per booking), so
    the `guest_{pk}_...` field names this view already used never collide between two forms on one
    page - no extra name-prefixing needed beyond what already existed. guest_registrations.js was
    also made form-scoped (previously page-wide querySelectorAll, which would have toggled the
    SECOND apartment's guest sections based on the FIRST apartment's NIF answer)."""
    template_name = 'bookings/manage_guest_registrations.html'

    def _rows(self, party):
        # One dict per current party member, bundling the guest + its (lazily created)
        # registration + a place for that guest's own errors - built here rather than as three
        # separate context lists so the template never has to look a dict up by a loop variable
        # (Django's dotted template lookup can't do errors_by_guest.guest.pk; a plain attribute
        # read of row.errors inside the same {% for row in rows %} can).
        registrations = [GuestRegistration.objects.get_or_create(booking_guest=guest)[0] for guest in party]
        return [{'guest': guest, 'registration': registration, 'errors': {}}
                for guest, registration in zip(party, registrations)]

    def _context(self, booking, rows, all_bookings):
        context = _manage_nav_context(booking, 'guest_registrations', all_bookings=all_bookings)
        context.update({
            'booking': booking, 'rows': rows,
            'id_types': GuestRegistration.IDType.choices, 'countries': countries,
        })
        return context

    def _merged_context(self, bookings, target=None, target_rows=None):
        """get()'s context for either a single leg or a merged multi-leg stay, and post()'s
        error-path re-render. Every leg's rows come fresh from the DB, EXCEPT `target` (if given),
        which uses `target_rows` instead - the just-submitted, error-carrying rows from a failed
        POST, so that guest's own just-typed values and errors survive the re-render rather than
        being silently overwritten by a fresh read."""
        def rows_for(booking):
            if target is not None and booking.pk == target.pk and target_rows is not None:
                return target_rows
            return self._rows(list(booking.party.all()))

        primary = bookings[0]
        context = self._context(primary, rows_for(primary), bookings)
        if len(bookings) > 1:
            context['legs'] = [self._context(booking, rows_for(booking), bookings) for booking in bookings]
        return context

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        return render(request, self.template_name, self._merged_context(bookings))

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        leg_reference = request.POST.get('leg_reference') or bookings[0].reference
        booking = next((candidate for candidate in bookings if candidate.reference == leg_reference), bookings[0])

        party = list(booking.party.all())
        post = request.POST
        rows = self._rows(party)
        has_errors = False

        # Only the lead (first) guest is asked whether they have a Portuguese NIF - per Thomas,
        # that single answer governs the whole party: if they have one, nobody else registers
        # either; if they don't, everyone (including the lead guest) fills in the full form.
        lead_row = rows[0] if rows else None
        lead_has_nif = None
        if lead_row is not None:
            guest, registration = lead_row['guest'], lead_row['registration']
            prefix = f'guest_{guest.pk}_'
            raw_has_nif = post.get(f'{prefix}has_nif', '').strip()
            lead_has_nif = {'yes': True, 'no': False}.get(raw_has_nif)
            registration.has_nif = lead_has_nif
            registration.nif_number = post.get(f'{prefix}nif_number', '').strip()

            if lead_has_nif is None:
                lead_row['errors']['has_nif'] = "Please tell us whether this guest has a Portuguese NIF."
            elif lead_has_nif and not registration.nif_number:
                lead_row['errors']['nif_number'] = "NIF is required."
            if lead_row['errors']:
                has_errors = True

        if lead_has_nif is False:
            for row in rows:
                guest, registration = row['guest'], row['registration']
                prefix = f'guest_{guest.pk}_'
                raw_birth_date = post.get(f'{prefix}birth_date', '').strip()
                registration.birth_date = _parsed_birth_date(raw_birth_date)
                registration.place_of_birth = post.get(f'{prefix}place_of_birth', '').strip()
                registration.nationality = post.get(f'{prefix}nationality', '').strip()
                registration.country_of_residence = post.get(f'{prefix}country_of_residence', '').strip()
                registration.id_type = post.get(f'{prefix}id_type', '').strip()
                registration.id_number = post.get(f'{prefix}id_number', '').strip()
                registration.issued_by = post.get(f'{prefix}issued_by', '').strip()

                errors = row['errors']
                if not raw_birth_date:
                    errors['birth_date'] = "Birth date is required."
                elif registration.birth_date is None:
                    errors['birth_date'] = "Enter a valid date."
                elif registration.birth_date > date.today():
                    errors['birth_date'] = "Birth date can't be in the future."
                if not registration.place_of_birth:
                    errors['place_of_birth'] = "Place of birth is required."
                if not registration.nationality:
                    errors['nationality'] = "Nationality is required."
                if not registration.country_of_residence:
                    errors['country_of_residence'] = "Country of residence is required."
                if registration.id_type not in dict(GuestRegistration.IDType.choices):
                    errors['id_type'] = "Select ID card or Passport."
                if not registration.id_number:
                    errors['id_number'] = "ID/Passport number is required."
                if not registration.issued_by:
                    errors['issued_by'] = "Issuing country is required."
                if errors:
                    has_errors = True

        if has_errors:
            context = self._merged_context(bookings, target=booking, target_rows=rows)
            return render(request, self.template_name, context)

        with transaction.atomic():
            for row in rows:
                row['registration'].save()

        url = reverse('bookings:manage_guest_registrations', kwargs={'reference': reference})
        return redirect(f"{url}?registrations_saved=1")


def _tourist_tax_context(booking):
    booking_settings = BookingSettings.load()
    if not booking.party.exists():
        return {
            'booking': booking, 'no_party': True,
            'min_age': booking_settings.tourist_tax_min_age, 'max_nights': booking_settings.tourist_tax_max_nights,
        }

    total, qualifying_guests, nights = compute_tourist_tax(booking, booking_settings)
    tourist_tax, _created = TouristTax.objects.get_or_create(booking=booking, defaults={'total': total})
    if tourist_tax.status != 'paid' and tourist_tax.total != total:
        tourist_tax.total = total
        tourist_tax.revolut_checkout_url = None
        tourist_tax.save()

    return {
        'booking': booking,
        'tourist_tax': tourist_tax,
        'qualifying_guests': qualifying_guests,
        'nights': nights,
        'min_age': booking_settings.tourist_tax_min_age,
        'max_nights': booking_settings.tourist_tax_max_nights,
    }


def _combined_tourist_tax_context(bookings):
    """Multi-property counterpart to _tourist_tax_context() above - Stage D4 of the multi-property
    hub merge (see project memory): unlike every other Manage hub section, Tourist Tax gets ONE
    combined Revolut payment across every apartment rather than duplicate forms/links per leg,
    since (unlike Pay Balance) there's no owner-payout split to keep separate - it's a pass-through
    municipal tax, not rental revenue. Each leg still gets its own TouristTax row (so per-apartment
    accounting keeps working, and klt-hooks' mark_tourist_tax_paid() can still key off the row it
    already updates by revolut_order_id) - BookingManageTouristTaxPayView stamps the SAME
    revolut_order_id/revolut_checkout_url onto every payable leg's row, and that raw-SQL UPDATE has
    no LIMIT, so one guest payment marks every row sharing the order id paid at once - no
    klt-hooks change needed.

    total_due only counts a leg once its party is known and something's actually owed.
    missing_party is True if ANY leg still needs its Guest List filled in - the combined total
    can't be trusted until every leg's real ages are known, so the pay button stays hidden (each
    leg's own breakdown/prompt still renders individually via `legs`)."""
    legs = [_tourist_tax_context(booking) for booking in bookings]
    missing_party = any(leg.get('no_party') for leg in legs)
    any_due = any(not leg.get('no_party') and leg['tourist_tax'].total for leg in legs)
    payable_legs = [
        leg for leg in legs
        if not leg.get('no_party') and leg['tourist_tax'].total and leg['tourist_tax'].status != 'paid'
    ]
    total_due = sum((leg['tourist_tax'].total for leg in payable_legs), Decimal('0'))
    return {
        'legs': legs,
        'missing_party': missing_party,
        'any_due': any_due,
        'total_due': total_due,
        'all_settled': not missing_party and any_due and not payable_legs,
    }


class BookingManageTouristTaxView(View):
    """Tourist Tax section of the Manage Booking hub - shows the guest the computed municipal
    tourist tax owed (see bookings/utils.py::compute_tourist_tax()) and a way to pay it, mirroring
    how the legacy klt-management-software system bundled this into an arrival-registration email
    (see reference_klt_tourist_tax_legacy_pattern in memory) - klt-web has no automated email yet,
    so this is guest-initiated instead. Reachable as soon as the deposit is paid (same gate as
    Guest Registrations), no edit cutoff. Requires a named Guest List first, since the total
    depends on real per-guest ages - shows a "fill in your Guest List" prompt otherwise rather than
    computing anything from the adults/children/babies headcount (which uses a different age
    cutoff, see BookingSettings.tourist_tax_min_age's docstring).

    The TouristTax row is created lazily here (unlike Payment/BalancePayment, which always exist
    from booking creation) and its total is recomputed on every visit while unpaid, since the party
    can change right up until payment - any change clears revolut_checkout_url too, so the pay
    page creates a fresh Revolut order for the new amount instead of honouring a stale one.

    2026-09-14 (Stage D4 of the multi-property hub merge - see project memory): a multi-property
    stay shows one _tourist_tax_context() breakdown card per leg (same "one card per apartment"
    pattern Stage B established), but a single combined total/Pay link at the bottom instead of a
    pay link per leg - see _combined_tourist_tax_context() and BookingManageTouristTaxPayView."""
    template_name = 'bookings/manage_tourist_tax.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        primary = bookings[0]
        context = _manage_nav_context(primary, 'tourist_tax', all_bookings=bookings)
        # Always seed from the primary leg first (not just in the single-property case) - the
        # sidebar and every other chrome on this page still link off top-level `booking`, same
        # convention as BookingManageAmenitiesView etc. The multi-property overlay below only adds
        # `legs`/combined-total keys on top, it doesn't replace this.
        context.update(_tourist_tax_context(primary))
        if len(bookings) > 1:
            context.update(_combined_tourist_tax_context(bookings))
        return render(request, self.template_name, context)


class BookingManageTouristTaxPayView(View):
    """Checkout step for BookingManageTouristTaxView - mirrors BookingBalancePaymentView's lazy
    Revolut-order-creation pattern, but always provider='revolut' (no Wise branch - the legacy
    pattern this is ported from never had one for tourist tax) and always EUR (a Portuguese
    municipal tax, collected in EUR regardless of whatever currency the guest was quoted the
    rental in - matches the legacy code's own hardcoded payment.currency = 'EUR').

    2026-09-14 (Stage D4 of the multi-property hub merge - see project memory): unlike every other
    per-leg Manage hub payment, this now resolves `reference` via bookings_for_stay_reference() and
    creates ONE combined Revolut order covering every apartment that still owes tourist tax
    (`payable`), stamping the SAME revolut_order_id/revolut_checkout_url onto each of their
    TouristTax rows. klt-hooks' mark_tourist_tax_paid() already does a plain
    `UPDATE ... WHERE revolut_order_id = %s` with no LIMIT, so one guest payment marks every row
    sharing that order id paid at once - no klt-hooks change required (see also
    staff/views.py::StaffBookingDetailView._update_booking(), which propagates a manual staff
    confirm across the same shared order id for the dormant-webhook fallback case).

    `payable` is a list of one for the overwhelming majority (single-property) case, so this is
    byte-for-byte the same behaviour as before there."""
    template_name = 'bookings/tourist_tax_pay.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        # Deliberately 'bookings:pay' (that leg's own deposit checkout), not 'bookings:details' -
        # unlike the hub summary page (BookingManageTouristTaxView), this IS the checkout step
        # itself, matching BookingBalancePaymentView's own unpaid-deposit redirect target exactly.
        # In practice this only fires on a direct/stale URL - the summary page's own gate already
        # guarantees every leg's deposit is paid before a "Pay Tourist Tax" link is ever shown.
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:pay', reference=unpaid.reference)

        payable = [
            booking for booking in bookings
            if hasattr(booking, 'tourist_tax') and booking.tourist_tax.total and not is_tourist_tax_paid(booking)
        ]
        if not payable:
            return redirect('bookings:manage_tourist_tax', reference=reference)

        pay_amount = sum((booking.tourist_tax.total for booking in payable), Decimal('0'))
        primary_tax = payable[0].tourist_tax
        context = {
            'booking': bookings[0],
            'stay_reference': reference,
            'tourist_tax': primary_tax,
            'pay_amount': pay_amount,
            'pay_currency': 'EUR',
            'legs': payable if len(payable) > 1 else None,
        }

        # Regenerate the order if it's missing, OR if a leg's own total changed since it was
        # created (_tourist_tax_context nulls that leg's own revolut_checkout_url when that
        # happens, so its row would otherwise drift out of sync with the others' still-shared one).
        needs_order = not primary_tax.revolut_checkout_url or any(
            booking.tourist_tax.revolut_checkout_url != primary_tax.revolut_checkout_url for booking in payable
        )
        if needs_order:
            self._create_revolut_order(bookings[0], payable, pay_amount)

        context['payment_error'] = not primary_tax.revolut_checkout_url
        return render(request, self.template_name, context)

    def _create_revolut_order(self, primary_booking, payable, pay_amount):
        order = Revolut(secretKey=env_settings.REVOLUT_API_SECRET_KEY).payment
        order.amount = int(pay_amount * 100)  # Revolut wants minor units (cents/pence), not major units
        order.currency = 'EUR'
        references = ', '.join(booking.reference for booking in payable)
        order.description = f"Tourist Tax for booking {references}"
        order.customerEmail = primary_booking.guest.email
        order.customerName = f"{primary_booking.guest.first_name} {primary_booking.guest.last_name}".strip()
        order.create()

        if order.id and order.has('checkout_url'):
            for booking in payable:
                booking.tourist_tax.revolut_order_id = order.id
                booking.tourist_tax.revolut_checkout_url = order.checkoutUrl
                booking.tourist_tax.save()
        # else: order.create() already logged the failure via logerror(); leave revolut_checkout_url
        # unset so payment_error renders and the guest can retry on reload.


class BookingManageDepositView(View):
    """Security Deposit section of the Manage Booking hub - captures the guest's own bank account
    details for the cash-deposit refund by bank transfer, replicating the legacy
    klt-management-software 'Account details' popup's fields per Thomas's reference screenshot
    (2026-08-29). Reachable as soon as the deposit is paid (same is_paid() gate as Guest
    Registrations/Arrival & Departure) and only for a booking that actually has a deposit owed
    (Charge.security - see its own docstring, bookings/models.py) - _manage_nav_context()'s
    show_security_deposit hides the sidebar link too, this is the server-side backstop for
    someone hitting the URL directly. No edit cutoff - a guest can come back and correct these
    any time before departure, same reasoning as Arrival & Departure/Guest Registrations.

    2026-09-14 (Stage D of the multi-property hub merge - see project memory): each leg's deposit
    is a genuinely separate DepositBankDetails row (could even be a different bank account per
    apartment), so a multi-property stay renders one full, independently-submittable form per
    apartment that actually owes a deposit (`legs`, via `_manage_deposit_leg.html`) rather than one
    shared form. Each form POSTs back to the STAY's own reference (not the individual leg's), with
    a hidden `leg_reference` field telling this view which apartment's details to save - so both
    success and any future validation error land back on the merged page, never bouncing the guest
    out to a single-leg view. A leg whose Charge.security is falsy (deposits waived/not owed for
    that specific apartment) simply gets no card - only `applicable` legs are shown, and a group
    where NEITHER leg owes a deposit redirects away entirely, same as the single-booking gate did."""
    template_name = 'bookings/manage_deposit.html'

    def _context(self, booking, details, all_bookings):
        context = _manage_nav_context(booking, 'deposit', all_bookings=all_bookings)
        context.update({
            'booking': booking, 'details': details,
            'security_deposit_amount': booking.charges.security,
        })
        return context

    def _details_for(self, booking):
        details, _ = DepositBankDetails.objects.get_or_create(booking=booking)
        return details

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        applicable = [booking for booking in bookings if getattr(booking, 'charges', None) and booking.charges.security]
        if not applicable:
            # Single-leg: exactly the original gate's own redirect target, unchanged. Multi-leg:
            # every leg here is already necessarily paid (the unpaid gate above already returned),
            # so `details` would just bounce right back out via redirect_to_next_step_after_payment
            # - the merged hub is the more sensible landing spot for "nothing to do here".
            return redirect('bookings:details' if len(bookings) == 1 else 'bookings:manage_hub', reference=reference)

        context = self._context(applicable[0], self._details_for(applicable[0]), bookings)
        if len(applicable) > 1:
            context['legs'] = [self._context(booking, self._details_for(booking), bookings) for booking in applicable]
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        applicable = [booking for booking in bookings if getattr(booking, 'charges', None) and booking.charges.security]
        if not applicable:
            return redirect('bookings:details' if len(bookings) == 1 else 'bookings:manage_hub', reference=reference)

        leg_reference = request.POST.get('leg_reference') or applicable[0].reference
        target = next((booking for booking in applicable if booking.reference == leg_reference), applicable[0])

        details = self._details_for(target)
        post = request.POST
        details.bank_name = post.get('bank_name', '').strip()
        details.account_name = post.get('account_name', '').strip()
        details.account_number = post.get('account_number', '').strip()
        details.sort_code = post.get('sort_code', '').strip()
        details.iban = post.get('iban', '').strip()
        details.swift_code = post.get('swift_code', '').strip()
        details.bank_address = post.get('bank_address', '').strip()
        details.save()

        url = reverse('bookings:manage_deposit', kwargs={'reference': reference})
        return redirect(f"{url}?saved=1")


def apply_sibling_cancellation_credit(cancelled, remaining):
    """Carry the deposits of just-cancelled apartments over to whatever the guest still owes on
    the apartments they're keeping (2026-09-15, per Thomas).

    A cancelled booking's deposit is otherwise simply forfeit - this codebase has no refund path
    anywhere (see BookingCancelView) - so when the same party is still staying with us, that money
    reduces their remaining balance instead of being kept.

    Only ever applied to a leg whose balance is genuinely still outstanding: a leg already paid in
    full has nothing to credit against, and Thomas's rule is explicitly "if the balance has not
    been paid at time of cancellation". Cancelling EVERY leg therefore credits nothing, which is
    the intended no-op - there's nothing left to put it towards, so today's forfeit rule stands.

    Credit lands on Charge.sibling_cancellation_credit, never on due_at_balance, so the remaining
    apartment's owner is still paid in full on the stay they're actually providing - see that
    field's own docstring. Spread in leg order, filling each leg's outstanding balance before
    moving to the next; any excess is simply retained (per Thomas: no refund, matching the
    existing no-refund-on-cancellation rule). Returns the total actually credited."""
    available = sum(
        (leg.charges.due_at_booking or Decimal('0'))
        for leg in cancelled
        if getattr(leg, 'charges', None) and is_paid(leg)
    )
    if available <= 0:
        return Decimal('0')

    credited = Decimal('0')
    for leg in remaining:
        if available <= 0:
            break
        charge = getattr(leg, 'charges', None)
        if charge is None or charge.due_at_balance is None or is_balance_paid(leg):
            continue
        outstanding = charge.balance_payable()
        if outstanding <= 0:
            continue
        applied = min(outstanding, available)
        charge.sibling_cancellation_credit = (
            charge.sibling_cancellation_credit or Decimal('0')
        ) + applied
        charge.save(update_fields=['sibling_cancellation_credit'])
        # A stale checkout URL would otherwise have the guest paying the pre-credit amount - same
        # reason the guest-list and date-change flows clear it when they move the balance.
        balance_payment = getattr(leg, 'balance_payment', None)
        if balance_payment is not None and balance_payment.revolut_checkout_url:
            balance_payment.revolut_order_id = None
            balance_payment.revolut_checkout_url = None
            balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])
        available -= applied
        credited += applied
    return credited


class BookingCancelView(View):
    """Self-service cancellation of an already-paid booking - genuinely different from
    cancel_booking_hold() (bookings/utils.py), which only ever acts on a not-yet-paid hold and is
    explicitly a no-op on anything already paid. No refund logic here or anywhere else in this
    codebase - Thomas confirmed cancelling here never refunds anything already paid, and the
    confirm page states that explicitly rather than leaving it implicit. Hidden (and re-checked
    server-side) for platform-sourced bookings, an already-cancelled booking, and a booking whose
    stay has already started - see _manage_nav_context()'s show_cancel_booking, the single source
    of truth this view's own gate reuses. Type-to-confirm (the guest must retype their own
    reference) rather than a single click, given how consequential and irreversible this is.

    2026-09-15 (Stage D8 of the multi-property hub merge - see project memory): a multi-property
    stay can cancel ALL or SOME of its apartments, chosen by checkbox, rather than being an
    all-or-nothing action on one leg. Cancelling some of them carries the cancelled apartments'
    deposits over to the balance still owed on the ones being kept - see
    apply_sibling_cancellation_credit(). Still no refund anywhere: a credit only ever reduces
    something genuinely still owed, and any excess is retained."""
    template_name = 'bookings/manage_cancel.html'

    def _gate(self, request, reference):
        """(bookings, cancellable_legs, redirect_or_None). `cancellable` excludes any leg that's
        already cancelled or otherwise not cancellable, reusing show_cancel_booking per leg rather
        than inventing a second rule."""
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return bookings, [], merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return bookings, [], redirect('bookings:details', reference=unpaid.reference)
        cancellable = [
            leg for leg in bookings
            if _manage_nav_context(leg, 'cancel')['show_cancel_booking']
        ]
        if not cancellable:
            return bookings, [], redirect('bookings:manage_hub', reference=reference)
        return bookings, cancellable, None

    def _context(self, bookings, cancellable, reference, **extra):
        primary = bookings[0]
        context = {'booking': primary, 'reference_error': None, 'selection_error': None}
        context.update(_manage_nav_context(primary, 'cancel', all_bookings=bookings))
        if len(bookings) > 1:
            context['legs'] = [
                {'booking': leg, 'charge': getattr(leg, 'charges', None),
                 'balance_outstanding': (
                     not is_balance_paid(leg) and getattr(leg, 'charges', None) is not None
                     and (leg.charges.balance_payable() or Decimal('0')) > 0
                 )}
                for leg in cancellable
            ]
        context.update(extra)
        return context

    def get(self, request, reference, *args, **kwargs):
        bookings, cancellable, redirect_response = self._gate(request, reference)
        if redirect_response is not None:
            return redirect_response
        return render(request, self.template_name, self._context(bookings, cancellable, reference))

    def post(self, request, reference, *args, **kwargs):
        bookings, cancellable, redirect_response = self._gate(request, reference)
        if redirect_response is not None:
            return redirect_response

        # The guest confirms against whichever reference they're actually looking at - the shared
        # party reference on a merged stay, their own booking reference otherwise.
        typed_reference = request.POST.get('reference_confirm', '').strip()
        if typed_reference.upper() != reference.upper():
            label = "party reference" if len(bookings) > 1 else "booking reference"
            return render(request, self.template_name, self._context(
                bookings, cancellable, reference,
                reference_error=f"That doesn't match your {label} - please try again.",
            ))

        if len(bookings) > 1:
            selected_references = set(request.POST.getlist('cancel_leg'))
            selected = [leg for leg in cancellable if leg.reference in selected_references]
            if not selected:
                return render(request, self.template_name, self._context(
                    bookings, cancellable, reference,
                    selection_error="Please choose at least one apartment to cancel.",
                ))
        else:
            selected = cancellable

        selected_pks = {leg.pk for leg in selected}
        remaining = [leg for leg in bookings if leg.pk not in selected_pks and not is_cancelled(leg)]

        with transaction.atomic():
            for leg in selected:
                leg.enquiry_status = 'Cancelled by guest'
                leg.save(update_fields=['enquiry_status'])
            apply_sibling_cancellation_credit(selected, remaining)

        url = reverse('bookings:manage_hub', args=[reference])
        return redirect(f"{url}?cancelled=1")


def _amenities_context(booking):
    amenities = getattr(booking.property, 'amenities', None)
    cleaning_company = booking.property.cleaning_company
    return {
        'booking': booking,
        'amenities': amenities,
        'towel_items': amenities.towel_line_items(booking.total_guests()) if amenities else [],
        'linen_provided': bool(cleaning_company and cleaning_company.linen_provided),
        'washing_materials': cleaning_company.washing_materials.all() if cleaning_company else [],
    }


class BookingManageAmenitiesView(View):
    """Holiday Info section of the Manage Booking hub - a read-only, guest-facing answer to
    exactly the question staff used to field by hand-typed email (the "what will I find in the
    apartment" reply this whole feature started from), sourced from the same Amenity row the
    property page's own feature grid uses. Read-only: no get_or_create() side effect on a bare GET
    of a public bearer link - a property with no Amenity row yet just shows an empty list rather
    than silently creating one, unlike the staff detail page's own lazy-create (that page is
    staff-authenticated and editing-oriented; this one is neither).

    linen_provided/washing_materials (2026-08-27) come from Property.cleaning_company instead -
    per-property towel counts live on Amenity (how many/which types this specific property has),
    but whether beds get dressed in linen at all and what's stocked for the guest are standard
    practice for whichever company actually cleans the property, not a per-property fact.

    2026-09-14: genuinely per-property content (unlike Local Rules/FAQ/Local Guide below, which a
    multi-property stay's two legs always share by construction - see MultiPropertyReserveView),
    so a multi-property stay's Amenities page shows one _amenities_context() per leg. Per
    subsection, not the whole page as a unit (2026-09-14, per Thomas - two apartments in the same
    building are often furnished identically even when e.g. towel counts differ): each of "In the
    apartment"/"Towels and Linen"/"Also included" independently renders once (no property label)
    when every leg's own content genuinely matches, or once per leg labeled "Header - Property"
    when it doesn't - see manage_amenities.html and the *_shared context flags below."""
    template_name = 'bookings/manage_amenities.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        primary = bookings[0]
        context = _manage_nav_context(primary, 'amenities', all_bookings=bookings)
        context.update(_amenities_context(primary))
        if len(bookings) > 1:
            legs = [_amenities_context(booking) for booking in bookings]
            context['legs'] = legs
            # Per-subsection, not all-or-nothing (2026-09-14, per Thomas) - two apartments in the
            # same building are often furnished identically even when their towel counts or
            # washing-materials differ, so each subsection decides independently whether it needs
            # a per-apartment label at all.
            context['amenities_shared'] = _all_equal(
                tuple(leg['amenities'].full_feature_list()) if leg['amenities'] else None for leg in legs
            )
            context['towels_shared'] = _all_equal(
                (tuple(leg['towel_items']), leg['linen_provided']) for leg in legs
            )
            context['washing_shared'] = _all_equal(
                tuple(material.pk for material in leg['washing_materials']) for leg in legs
            )
        return render(request, self.template_name, context)


class BookingManageLocationView(View):
    """Holiday Info section of the Manage Booking hub - address, directions, nearby amenities and
    house rules (quiet/pool hours) for the guest's own booked property, previously only visible on
    the separate public Location page (which a guest may never have seen, since search results
    land straight on the property page). Read-only, same no-side-effect GET as
    BookingManageAmenitiesView.

    Self check-in instructions (2026-09-05, per Thomas): Property.self_check_in_instructions is
    only ever surfaced here, and only when this specific booking's own Arrival.self_check_in is
    True - this now follows the property's booking_company check-in policy automatically where one
    is set (see bookings/utils.py::compute_effective_self_check_in), falling back to a fully
    manual staff-set flag otherwise. A booking with no Arrival row yet (get_or_create'd lazily
    elsewhere - see bookings/views.py::_save_arrival) has never been marked self check-in, same as
    an explicit False.

    The access code(s) themselves (properties.models.PropertyAccessCode) are withheld from the
    response until BookingSettings.self_check_in_code_reveal_days before arrival - deliberately
    separate from the surrounding self_check_in_instructions prose (always shown once self check-in
    applies at all), so a guest can read the general "how this works" text well ahead of arrival
    while the actual code value stays hidden until closer to the date.

    Shared-postbox fork (2026-09-05, Quinta da Barracuda): when the property's Location has one
    configured (Location.self_check_in_preferred_code non-blank), bookings/utils.py::
    resolve_shared_postbox_path decides whether this booking also gets the Location-level preferred
    or fallback instructions+code shown ahead of the property's own self_check_in_instructions - the
    two are layered, not exclusive (2026-09-05 fix: an earlier version replaced one with the other,
    losing the property-specific building-navigation prose - e.g. "the door to your apartment is on
    the 2nd floor, turn right out of the lift" - the moment a fork applied). The property's own
    access codes still show on the fallback path (the guest still needs their own front-door code
    after the gate fob) but not on the preferred path (the postbox key makes the front-door code
    irrelevant).

    In-person liaison contact/check-in-out times/late fee (2026-09-06, per Thomas): sourced from
    Property.cleaning_company's liaison_name/liaison_phone, standard_checkin_time/
    standard_checkout_time, and late_check_in_after/late_check_in_fee respectively - all live
    ManagementCompany data rather than baked into Property.in_person_check_in_instructions'
    freeform text, so none of it can go stale independently of that record. Read off
    cleaning_company specifically, not booking_company - same precedent as
    ManagementCompany.standard_checkin_time elsewhere (the cleaning company is who actually
    performs the meet & greet, per Thomas 2026-09-02). in_person_checkin_checkout is just the
    company itself (its two time fields always have a real value, unlike the other two) - None
    only when there's no cleaning_company at all to read from.

    Airport transfer meeting info (2026-09-08, per Thomas): replaces the Directions section
    whenever this booking has an inbound AirportTransfer - the driver coordinates with the team
    directly, so the guest doesn't need to call ahead the way an in-person check-in normally asks
    (see _self_check_in.html's own inbound_transfer check). has_outbound_transfer lets the payment
    paragraph state definitively whether a return transfer is also booked, rather than the legacy
    email's "if applicable" hedge - the hub can just check. transfer_fallback_contact is only set
    (from ExtrasSettings) when both the name and phone are configured; template treats it as
    "don't show this contact at all" otherwise, not a broken/partial credit.

    Emergency/in-stay contact (2026-09-08, per Thomas): the same cleaning_company liaison used for
    in-person check-in coordination above, but shown unconditionally - self-check-in guests need
    someone to call for an in-stay problem just as much as meet-and-greet guests do, so unlike
    in_person_liaison this isn't gated on in_person at all. None when there's no cleaning_company
    or it has no liaison_phone set, same "don't show a broken/partial credit" treatment as
    transfer_fallback_contact above.

    2026-09-14 (Stage B2 of the multi-property hub merge - see project memory, built with extra
    care given the access-code stakes): the location-level facts (address, map, directions, house
    rules) are identical for both legs of a multi-property stay by construction
    (MultiPropertyReserveView only offers same-Location combos) and render once, from the primary
    leg. Self check-in / in-person / access codes / the emergency contact are genuinely
    per-PROPERTY though - each apartment has its own door code - so those render once per leg
    (`_manage_location_leg.html`, looped), never merged or shared across apartments."""
    template_name = 'bookings/manage_location.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        primary = bookings[0]
        context = _manage_nav_context(primary, 'location', all_bookings=bookings)
        context.update(_location_context(primary))
        if len(bookings) > 1:
            context['legs'] = [_location_context(booking) for booking in bookings]
        return render(request, self.template_name, context)


def _location_context(booking):
    location = booking.property.location
    arrival = Arrival.objects.filter(booking=booking).first()
    self_check_in = bool(arrival and arrival.self_check_in)
    # Across the whole stay, not just this leg - see _stay_transfers().
    stay_transfers = _stay_transfers(booking)
    inbound_transfer = stay_transfers.filter(direction=AirportTransferDirection.INBOUND).first()
    has_outbound_transfer = stay_transfers.filter(direction=AirportTransferDirection.OUTBOUND).exists()
    # A known meet-and-greet, distinct from "we don't know yet" (arrival is None) - only ever true
    # once an Arrival row exists and explicitly says self_check_in=False, never shown prematurely
    # before the guest's own arrival details (or a company policy) have actually settled which
    # path applies.
    in_person = arrival is not None and arrival.self_check_in is False

    access_codes = []
    codes_revealed = False
    reveal_days = None
    postbox_path = None
    self_check_in_late_arrival = False
    if self_check_in:
        reveal_days = BookingSettings.load().self_check_in_code_reveal_days
        days_until_arrival = (booking.arrival_date - timezone.now().date()).days
        codes_revealed = days_until_arrival <= reveal_days
        if location is not None and location.self_check_in_preferred_code:
            postbox_path = resolve_shared_postbox_path(booking)
        if postbox_path in (None, 'fallback'):
            access_codes = list(booking.property.access_codes.all())
        # Only true when self check-in is a MIXED company's late-arrival cutoff kicking in, not a
        # property that's always self check-in - a guest arriving well within normal hours
        # shouldn't be told they're "arriving very late" just because their property happens to
        # have no in-person option at all (2026-09-08, per Thomas). Judged on the computed ETA,
        # not the raw given time, for exactly the same reason compute_effective_self_check_in() is
        # - otherwise a 21:00 Faro landing would silently get self check-in (ETA 22:30, past the
        # cutoff) with no explanation of why.
        from properties.models import ManagementCompany
        booking_company = booking.property.booking_company
        arrival_eta = (
            compute_eta_from_given_time(arrival.method, arrival.time, arrival.time_unknown)
            if arrival is not None else None
        )
        self_check_in_late_arrival = bool(
            booking_company is not None
            and booking_company.check_in_method == ManagementCompany.CheckInMethod.MIXED
            and booking_company.self_check_in_after is not None
            and arrival_eta is not None
            and arrival_eta >= booking_company.self_check_in_after
        )

    in_person_cleaning_company = booking.property.cleaning_company if in_person else None
    in_person_liaison = (
        in_person_cleaning_company
        if in_person_cleaning_company is not None and in_person_cleaning_company.liaison_phone
        else None
    )
    in_person_late_fee = (
        in_person_cleaning_company
        if in_person_cleaning_company is not None
        and in_person_cleaning_company.late_check_in_after is not None
        and in_person_cleaning_company.late_check_in_fee is not None
        else None
    )

    transfer_fallback_contact = None
    if inbound_transfer is not None:
        extras_settings = ExtrasSettings.load()
        if (
            extras_settings.airport_transfer_fallback_contact_name
            and extras_settings.airport_transfer_fallback_contact_phone
        ):
            transfer_fallback_contact = extras_settings

    cleaning_company = booking.property.cleaning_company
    emergency_contact = (
        cleaning_company if cleaning_company is not None and cleaning_company.liaison_phone else None
    )

    return {
        'booking': booking, 'location': location,
        'self_check_in': self_check_in,
        'self_check_in_instructions': booking.property.self_check_in_instructions if self_check_in else '',
        'self_check_in_late_arrival': self_check_in_late_arrival,
        'in_person': in_person,
        'in_person_check_in_instructions': booking.property.in_person_check_in_instructions if in_person else '',
        'in_person_liaison': in_person_liaison,
        'in_person_checkin_checkout': in_person_cleaning_company,
        'in_person_late_fee': in_person_late_fee,
        'access_codes': access_codes,
        'codes_revealed': codes_revealed,
        'code_reveal_days': reveal_days,
        'postbox_path': postbox_path,
        'postbox_location': location if postbox_path else None,
        'inbound_transfer': inbound_transfer,
        'has_outbound_transfer': has_outbound_transfer,
        'transfer_fallback_contact': transfer_fallback_contact,
        'emergency_contact': emergency_contact,
    }


class BookingManageLocalRulesView(View):
    """Holiday Info section of the Manage Booking hub - Albufeira Municipal Council's public
    conduct rules and their fines (2026-09-08, per Thomas - "Albufeira_Tourist_Code_of_Conduct.pdf"
    council flyer). Same content for every property and every guest - not sourced from any
    per-property model, unlike Amenities/Location above, since the municipality sets it, not us.
    Hardcoded into the template rather than an admin-editable model for that reason: nothing here
    is ever going to vary by property, and it isn't ours to edit anyway. Read-only, same
    no-side-effect GET as BookingManageAmenitiesView/BookingManageLocationView.

    2026-09-14: content is identical for every property, so a multi-property stay's two legs (both
    at the same location by construction - see MultiPropertyReserveView) never need this rendered
    twice - the primary leg alone is enough, no `legs` context/template loop needed here at all."""
    template_name = 'bookings/manage_local_rules.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        context = {'booking': booking}
        context.update(_manage_nav_context(booking, 'local_rules', all_bookings=bookings))
        return render(request, self.template_name, context)


def _last_days_context(booking):
    location = booking.property.location
    late_checkout_grant = getattr(booking, 'late_checkout_grant', None)
    late_checkout = late_checkout_grant is not None
    late_checkout_unlimited = late_checkout and late_checkout_grant.time is None
    cleaning_company = booking.property.cleaning_company
    checkout_time = (
        late_checkout_grant.time if late_checkout and not late_checkout_unlimited
        else cleaning_company.standard_checkout_time if cleaning_company
        else None
    )
    # Across the whole stay, not just this leg - see _stay_transfers().
    outbound_transfer = _stay_transfers(booking).filter(direction=AirportTransferDirection.OUTBOUND).first()
    outbound_pickup_time = None
    if outbound_transfer is not None:
        pickup = (
            datetime.combine(date.today(), outbound_transfer.time) - timedelta(hours=2, minutes=45)
        ).time()
        outbound_pickup_time = pickup

    # Empty outright for an unlimited late checkout (2026-09-09, per Thomas) - the whole point of
    # this section is bridging the gap between a fixed checkout time and being ready to actually
    # leave, which doesn't exist when there's no fixed time to bridge from.
    after_checkout_paragraphs = []
    if location and not late_checkout_unlimited:
        after_checkout_paragraphs = [
            p for p in location.after_checkout_access_instructions.split('\n\n') if p.strip()
        ]

    return {
        'booking': booking,
        'checkout_time': checkout_time,
        'late_checkout': late_checkout,
        'late_checkout_unlimited': late_checkout_unlimited,
        'outbound_transfer': outbound_transfer,
        'outbound_pickup_time': outbound_pickup_time,
        'has_bbq': bool(getattr(booking.property, 'amenities', None) and booking.property.amenities.barbecue),
        'nearest_bins': location.nearest_bins if location else '',
        # Split into paragraphs here rather than relying on {% linebreaks %} in the template - that
        # filter always re-escapes its input even when already marked safe, which would mangle the
        # <a> tags linkify (bookings_extras.py) has already built. A blank line in the stored text
        # is a deliberate paragraph break (see the QdB/Monaco backfill, properties/migrations/
        # 0057_...) - collapsed into one run-on block by HTML whitespace rules if rendered as a
        # single <p>.
        'after_checkout_paragraphs': after_checkout_paragraphs,
    }


class BookingManageLastDaysView(View):
    """Holiday Info section of the Manage Booking hub - final-day checkout procedure and, where
    applicable, departure-day airport transfer timing and after-checkout facility access. Ported
    2026-09-08 (per Thomas) from the klt-management-software departure emails (final_days,
    after_check_out), made dynamic where the old emails had to hedge: the outbound-transfer
    pickup-time paragraph only ever shows when this booking actually has one (AirportTransferView's
    inbound-side equivalent is BookingManageLocationView), rather than the legacy "if applicable"
    wording. Read-only, same no-side-effect GET as BookingManageAmenitiesView.

    checkout_time/late_checkout/late_checkout_unlimited: read from booking.late_checkout_grant
    directly (staff/models.py::LateCheckoutGrant), not Extra.late_checkout/late_checkout_time -
    2026-09-09, per Thomas: Extra's fields mirror the grant for a fixed 11:00/12:00 slot, but for
    an unlimited grant (time=None - "check out whenever") Extra.late_checkout_time is also None, so
    the old `bool(extra.late_checkout and extra.late_checkout_time)` check silently read that case
    as "no late checkout granted at all" and fell back to showing the standard checkout time -
    wrong for exactly the guest who was told there wasn't a fixed time. Reading the grant directly
    has no such blind spot. Falls back to ManagementCompany.standard_checkout_time off
    cleaning_company when there's no grant - same source BookingManageLocationView's own
    in_person_checkin_checkout already reads, so the two tabs never disagree about what "standard
    checkout" means for this property. has_bbq reads Property.amenities.barbecue rather than any
    hardcoded property name - MON T's flag was backfilled (bookings/migrations/0055_...) as part of
    this change, since the legacy system knew about its BBQ but nothing had ever set the structured
    flag.

    2026-09-14: genuinely per-property content (checkout time, BBQ, after-checkout access), so a
    multi-property stay's Last Days page shows one _last_days_context() per leg. Per subsection,
    not the whole page as a unit - same *_shared-flag pattern as Amenities above, so e.g. an
    identical checkout time across both apartments renders once while a differing BBQ note still
    gets its own "Before you go - Property" label."""
    template_name = 'bookings/manage_last_days.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        primary = bookings[0]
        context = _manage_nav_context(primary, 'last_days', all_bookings=bookings)
        context.update(_last_days_context(primary))
        if len(bookings) > 1:
            legs = [_last_days_context(booking) for booking in bookings]
            context['legs'] = legs
            context['checkout_shared'] = _all_equal(
                (leg['checkout_time'], leg['late_checkout'], leg['late_checkout_unlimited']) for leg in legs
            )
            context['transfer_shared'] = _all_equal(
                (bool(leg['outbound_transfer']), leg['outbound_pickup_time']) for leg in legs
            )
            context['before_you_go_shared'] = _all_equal(
                (leg['has_bbq'], leg['nearest_bins']) for leg in legs
            )
            context['after_checkout_shared'] = _all_equal(
                tuple(leg['after_checkout_paragraphs']) for leg in legs
            )
        return render(request, self.template_name, context)


class BookingManageFAQView(View):
    """Holiday Info section of the Manage Booking hub - guest-facing FAQ, sourced from the FAQ
    model staff maintain in Settings > Bookings (same order-editable, plain-text pattern as
    BookingCondition, just split into a question and an answer). location=None on a FAQ row means
    "show on every location's page"; otherwise it only shows for a booking whose property sits at
    that exact location (e.g. a parking answer that's only true for one building) - a property
    with no location set at all only ever sees the location=None rows. Read-only, same
    no-side-effect GET as BookingManageAmenitiesView/BookingManageLocationView.

    2026-09-14: keyed off Location, not Property - a multi-property stay's two legs are always at
    the same Location by construction (see MultiPropertyReserveView), so the primary leg's own
    query already covers both apartments and this never needs rendering (or querying) twice."""
    template_name = 'bookings/manage_faq.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        faqs = FAQ.objects.filter(Q(location__isnull=True) | Q(location=booking.property.location_id))

        context = _manage_nav_context(booking, 'faq', all_bookings=bookings)
        context.update({'booking': booking, 'faqs': faqs})
        return render(request, self.template_name, context)


class BookingManageLocalGuideView(View):
    """Holiday Info section of the Manage Booking hub - a guest-facing local area guide (things to
    do, beaches, day trips, where to eat, shopping, local facilities), sourced from the
    LocalGuideEntry model staff maintain in Settings > Bookings, same order-editable,
    location-optional pattern as FAQ. Read-only, same no-side-effect GET as the other Holiday Info
    views.

    Grouped in Python rather than via {% regroup %}: the model's Meta.ordering sorts by (category,
    order), but "category" orders alphabetically by its stored value (beaches, day_trips, dining,
    facilities, shopping, things_to_do) - not the Things To Do-first/Facilities-last sequence
    LocalGuideEntry.Category.choices itself declares and this page wants to display in. {% regroup
    %} only ever groups already-adjacent rows, so it can't fix that ordering by itself.

    2026-09-14: same Location-keyed reasoning as BookingManageFAQView - a multi-property stay's two
    legs share a Location by construction, so no per-leg duplication is needed here either."""
    template_name = 'bookings/manage_local_guide.html'

    def get(self, request, reference, *args, **kwargs):
        bookings, merged = resolve_stay(request, reference)
        if merged is not None:
            return merged
        unpaid = _first_unpaid_leg(bookings)
        if unpaid is not None:
            return redirect('bookings:details', reference=unpaid.reference)

        booking = bookings[0]
        entries = LocalGuideEntry.objects.filter(
            Q(location__isnull=True) | Q(location=booking.property.location_id)
        )
        entries_by_category = defaultdict(list)
        for entry in entries:
            entries_by_category[entry.category].append(entry)
        grouped_entries = [
            (label, entries_by_category[value])
            for value, label in LocalGuideEntry.Category.choices
            if entries_by_category[value]
        ]

        context = _manage_nav_context(booking, 'local_guide', all_bookings=bookings)
        context.update({'booking': booking, 'grouped_entries': grouped_entries})
        return render(request, self.template_name, context)
