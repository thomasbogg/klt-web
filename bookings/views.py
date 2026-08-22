import re
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

import env_settings
from bookings.forms import BookingLookupForm
from bookings.models import (
    AirportTransfer, AirportTransferDirection, AirportTransferPriceBand, Arrival, BalancePayment,
    Booking, BookingCondition, BookingGuest, BookingRequestedExtra, BookingSettings, Departure, Extra,
    ExtrasSettings, GuestListAdjustment, RequestType, TravelMethod, WelcomePackDrinksChoice,
    WelcomePackFoodChoice, WelcomePackItem,
)
from bookings.utils import (
    booking_confirmation_context, cancel_booking_hold, extras_summary, recalculate_balance_for_party,
    recalculate_costs_for_party, reservation_retry_url,
)
from libraries.banking.revolut import Revolut

MAX_GUEST_AGE = 120


def is_paid(booking):
    """A booking with no Payment row at all predates this feature or was platform-synced - never
    part of the deposit-payment flow, so treat it as paid (i.e. don't gate it)."""
    payment = getattr(booking, 'payment', None)
    return payment is None or payment.status == 'paid'


def is_balance_paid(booking):
    """A booking with no BalancePayment row at all is either collapsed (paid in full at deposit
    time - see BookingSettings.compute_costs()) or predates this feature - either way there's
    nothing left to collect, so treat it as paid the same way is_paid() does for a missing Payment."""
    balance_payment = getattr(booking, 'balance_payment', None)
    return balance_payment is None or balance_payment.status == 'paid'


def is_fully_paid(booking):
    """Deposit paid, and (no balance stage at all, or the balance is paid too) - the single switch
    the Manage Booking hub uses to decide whether a guest-list edit still goes through the existing
    pre-balance-paid flows (BookingDetailsView/BookingBalanceDetailsView, which can still reprice
    Charge) or the increases-only, cash-at-check-in GuestListAdjustment flow instead (Charge is
    frozen for good by this point). is_balance_paid() alone is NOT sufficient here - it returns
    True for a still-unpaid collapsed booking too (no BalancePayment row exists at all), so it must
    always be combined with is_paid() first."""
    return is_paid(booking) and is_balance_paid(booking)


def extras_edit_locked(booking):
    """Whether self-serve Extras editing via the Manage Booking hub has closed for this booking -
    a pure fulfilment-lead-time cutoff (BookingSettings.extras_edit_cutoff_days_before_arrival),
    unrelated to payment status: Extras are cash-at-check-in and were never priced into Charge (see
    extras_summary()'s docstring), so there's nothing here for payment state to gate."""
    days_until_arrival = (booking.arrival_date - timezone.now().date()).days
    return days_until_arrival <= BookingSettings.load().extras_edit_cutoff_days_before_arrival


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
        ])

        booking.requested_extras.all().delete()
        new_requests = []
        for request_type in RequestType.objects.filter(active=True):
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
            existing = {r.request_type_id: r for r in booking.requested_extras.all()}
            quantities = {t.id: str(existing[t.id].quantity) if t.id in existing else '0' for t in active_types}
            notes = {t.id: existing[t.id].note if t.id in existing else '' for t in active_types}

        settings = ExtrasSettings.load()
        nights = (booking.departure_date - booking.arrival_date).days
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
                'combo_discount': str(settings.cot_and_high_chair_combo_discount),
                'child_min_age': BookingSettings.load().child_min_age,
            },
            'late_checkout': late_checkout,
            'late_checkout_time': late_checkout_time,
            'late_checkout_price': settings.late_checkout_price,
            'request_rows': [
                {'request_type': t, 'quantity': quantities[t.id], 'note': notes[t.id]}
                for t in active_types
            ],
        }

    def _save_transfers(self, booking, rows):
        """Prices are always recomputed here from ExtrasSettings/AirportTransferPriceBand, never
        trusted from the client - the JS-side estimate in airport_transfers.js is display-only."""
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
        """Airport Transfer row context, plus the pricing config (bands + night-surcharge window)
        embedded for the client-side live price estimate in airport_transfers.js - purely a
        display convenience, the authoritative price is always recomputed server-side in
        _save_transfers(), never trusted from the client."""
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
                    {'max_guests': band.max_guests, 'price': str(band.price)}
                    for band in AirportTransferPriceBand.objects.all()
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

    def _parse_late_checkout(self, post_data):
        """A requested time is only required (and only an error) when the checkbox itself is
        ticked - matches the pattern of transfer rows requiring a time, but here it's a single
        optional field rather than a repeated row."""
        late_checkout = post_data.get('late_checkout') == 'on'
        time_raw = post_data.get('late_checkout_time', '').strip()
        if not late_checkout:
            return False, None, None

        if not time_raw:
            return True, None, "Enter your preferred checkout time."
        try:
            return True, datetime.strptime(time_raw, '%H:%M').time(), None
        except ValueError:
            return True, None, "Enter a valid checkout time."

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
        """Used only by BookingManageGuestAddView, once the booking is already fully paid -
        deliberately NOT _save_guest_list()'s delete-then-bulk_create pattern, since that would
        wipe the identity (and added_via_adjustment tagging) of every already-saved BookingGuest
        row. Only ever appends the new rows, tagged to the GuestListAdjustment that authorized them."""
        BookingGuest.objects.bulk_create([
            BookingGuest(
                booking=booking,
                first_name=row['first_name'],
                last_name=row['last_name'],
                age=int(row['age']),
                is_lead=False,
                added_via_adjustment=adjustment,
            )
            for row in rows
        ])
        booking.adults = new_guests['adults']
        booking.children = new_guests['children']
        booking.babies = new_guests['infants']
        booking.last_updated = timezone.now()
        booking.save(update_fields=['adults', 'children', 'babies', 'last_updated'])


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
            return redirect('bookings:confirmation', reference=reference)

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
            _, _, late_checkout_error = self._parse_late_checkout(request.POST)
        else:
            transfer_rows, transfer_non_field_error, late_checkout_error = [], None, None
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

        if non_field_error or any(row['errors'] for row in rows):
            return render(request, self.template_name, context)

        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            return render(request, self.template_name, context)

        if late_checkout_error:
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
            charge.admin = new_costs['admin_fee']
            charge.security = new_costs['security_deposit']
            charge.due_at_booking = new_costs['due_at_booking']
            charge.due_at_balance = new_costs['due_at_balance']
            charge.balance_due_date = new_costs['balance_due_date']
            charge.save(update_fields=[
                'basic_rental', 'admin', 'security', 'due_at_booking', 'due_at_balance', 'balance_due_date',
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
    """Guest-list and Extras step for a two-stage booking's balance stage (see BalancePayment's
    docstring). The guest list stays editable here up to the property's max_guests, same as
    BookingDetailsView - but unlike that view, the deposit (due_at_booking) is already paid and
    frozen by this point, so a change here can only move due_at_balance, never retroactively
    redefine the deposit - see bookings/utils.py::recalculate_balance_for_party() for why this
    can't just reuse recalculate_costs_for_party(). Reached via a link sent manually to the guest
    around BookingSettings.balance_reminder_days_before_arrival (no automated email yet), or
    self-serve from the confirmation/manage-booking pages once the deposit is paid - see
    bookings/utils.py::booking_confirmation_context(). Bearer-readable by reference alone, like
    every other post-deposit reference-based view here - unlike BookingDetailsView's POST, there's
    no same-session pending_booking_reference to check, since this is reached from an emailed link
    days later with no session continuity at all."""
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
        _, _, late_checkout_error = self._parse_late_checkout(request.POST)
        child_min_age = BookingSettings.load().child_min_age
        context = {
            'booking': booking,
            'rows': rows,
            'max_guests': max_guests,
            'non_field_error': non_field_error,
            'child_min_age': child_min_age,
            'show_cot_high_chair': self._any_infant_age(rows, child_min_age),
        }
        context.update(self._extras_context(booking, post_data=request.POST))
        context.update(self._transfer_context(booking, rows=transfer_rows, non_field_error=transfer_non_field_error))
        context['late_checkout_error'] = late_checkout_error

        if non_field_error or any(row['errors'] for row in rows):
            return render(request, self.template_name, context)

        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            return render(request, self.template_name, context)

        if late_checkout_error:
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
            charge.admin = new_costs['admin_fee']
            charge.security = new_costs['security_deposit']
            charge.due_at_balance = new_costs['due_at_balance']
            charge.save(update_fields=['basic_rental', 'admin', 'security', 'due_at_balance'])

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
            return redirect('bookings:confirmation', reference=reference)

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


class ManageBookingView(View):
    """Reference + email lookup for a guest returning later without their confirmation link. Once
    the lookup succeeds, hands off to BookingManageHubView (bearer-readable by reference alone,
    like every other post-deposit view here) rather than rendering the booking in place - so a
    guest who bookmarks/reloads the hub URL doesn't need to re-prove their email every time."""
    template_name = 'bookings/manage.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {'form': BookingLookupForm()})

    def post(self, request, *args, **kwargs):
        form = BookingLookupForm(request.POST)
        context = {'form': form}
        if form.is_valid():
            booking = Booking.objects.filter(
                reference=form.cleaned_data['reference'],
                guest__email__iexact=form.cleaned_data['email'],
            ).first()
            if booking is not None and not is_paid(booking):
                return redirect('bookings:pay', reference=booking.reference)
            elif booking is not None:
                return redirect('bookings:manage_hub', reference=booking.reference)
            else:
                context['not_found'] = True
        return render(request, self.template_name, context)


def _manage_nav_context(booking, active_section):
    """Sidebar context shared by every Manage Booking hub section view, so the nav renders
    identically (and highlights the right item) everywhere. show_pay_balance mirrors
    BookingBalanceDetailsView's own gating - Pay Balance only makes sense to show while there's a
    two-stage balance still outstanding; that view's own internal redirects handle every other case
    (unpaid deposit, already paid, payment in progress), so this doesn't need to re-derive those.
    show_cancel_booking is the single source of truth BookingCancelView's own gate also uses (never
    show/allow it once already cancelled, for a platform-sourced booking Thomas doesn't control
    cancellation for from this system, or once the stay has already started)."""
    cancelled = is_cancelled(booking)
    return {
        'active_section': active_section,
        'show_pay_balance': hasattr(booking, 'balance_payment') and not is_balance_paid(booking) and not cancelled,
        'show_cancel_booking': (
            not cancelled
            and booking.enquiry_source not in env_settings.PLATFORMS
            and booking.arrival_date > timezone.now().date()
        ),
        'cancelled': cancelled,
        'stage': 'fully_paid' if is_fully_paid(booking) else 'pre_balance',
    }


def _manage_hub_context(booking):
    """Context for the hub's landing ("Booking") section - just the booking summary plus nav."""
    context = booking_confirmation_context(booking)
    context.update(_manage_nav_context(booking, 'booking'))
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
    review-before-pay click-through."""
    template_name = 'bookings/manage_hub.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return redirect('bookings:details', reference=reference)
        return render(request, self.template_name, _manage_hub_context(booking))


class BookingManageGuestsView(BookingFormMixin, View):
    """Guest List section of the Manage Booking hub - reachable as soon as the deposit is paid, no
    balance-paid gate. Behavior branches on stage (see is_fully_paid()):

    pre_balance: a full editable guest-list form, same underlying save mechanics
    BookingBalanceDetailsView already uses (_parse_rows/recalculate_balance_for_party/the
    price-change interstitial/_save_guest_list) - a second, independent entry point to the same
    edit, not a replacement (see the plan this was built from). Unlike that view, saving here
    redirects back to this same section rather than auto-advancing to payment - Pay Balance is now
    its own deliberate sidebar action.

    fully_paid: read-only party list + an add-guest mini-form, POSTing to the separate
    BookingManageGuestAddView (increases-only, GuestListAdjustment-tracked) - this view's own POST
    only exists for the pre_balance case."""
    template_name = 'bookings/manage_guests.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return redirect('bookings:details', reference=reference)

        context = _manage_nav_context(booking, 'guests')
        if context['stage'] == 'fully_paid':
            context.update({
                'booking': booking,
                'party': booking.party.all(),
                'max_guests': booking.property.specs.max_guests,
            })
            return render(request, self.template_name, context)

        rows = self._seed_or_prefill_rows(booking)
        context.update({
            'booking': booking,
            'rows': rows,
            'max_guests': booking.property.specs.max_guests,
        })
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return redirect('bookings:details', reference=reference)

        nav = _manage_nav_context(booking, 'guests')
        if nav['stage'] == 'fully_paid':
            return redirect('bookings:manage_guests', reference=reference)

        max_guests = booking.property.specs.max_guests
        rows, non_field_error = self._parse_rows(request.POST)
        context = {'booking': booking, 'rows': rows, 'max_guests': max_guests, 'non_field_error': non_field_error}
        context.update(nav)

        if non_field_error or any(row['errors'] for row in rows):
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
            charge.admin = new_costs['admin_fee']
            charge.security = new_costs['security_deposit']
            charge.due_at_balance = new_costs['due_at_balance']
            charge.save(update_fields=['basic_rental', 'admin', 'security', 'due_at_balance'])

            # Same reason BookingBalanceDetailsView clears this - a stale checkout URL would
            # otherwise leave the guest paying an amount that no longer matches Charge.
            balance_payment = booking.balance_payment
            if changed and balance_payment.revolut_checkout_url:
                balance_payment.revolut_order_id = None
                balance_payment.revolut_checkout_url = None
                balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])

        return redirect(f"{reverse('bookings:manage_guests', args=[booking.reference])}?guests_saved=1")


FLIGHT_NUMBER_RE = re.compile(r'^(?=.*[A-Za-z])[A-Za-z0-9]{1,3}[ -]?\d{3,5}$')


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
    (1-3 letters/digits, at least one letter, then 3-5 digits, e.g. TP1234 or U21234 - the
    alphanumeric prefix accommodates real IATA codes like easyJet's "U2" that mix a digit in)
    when a flight method is chosen and the field is non-blank - the one hard validation on this
    page, added because guests kept pasting a (meaningless here) booking reference instead; on
    failure the page re-renders with the guest's other entries preserved via _context(), same
    pattern as BookingManageGuestAddView's row errors. self_check_in/meet_greet on Arrival and
    clean/manual_date on Departure are all
    staff/ops-only - only ever supplied as creation defaults (via get_or_create), never touched on
    a later guest save, so a staff edit made in admin afterward is never clobbered."""
    template_name = 'bookings/manage_arrival_departure.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return redirect('bookings:details', reference=reference)

        arrival = getattr(booking, 'arrival', None)
        departure = getattr(booking, 'departure', None)
        context = self._context(booking, {
            'method': arrival.method if arrival else TravelMethod.FLIGHT_FARO,
            'flight_number': arrival.flight_number if arrival else '',
            'travelling_from': arrival.travelling_from if arrival else '',
            'hiring_car': arrival.hiring_car if arrival else False,
            'time': arrival.time.strftime('%H:%M') if arrival and arrival.time else '',
            'details': arrival.details if arrival else '',
        }, {
            'method': departure.method if departure else TravelMethod.FLIGHT_FARO,
            'flight_number': departure.flight_number if departure else '',
            'travelling_from': departure.travelling_from if departure else '',
            'time': departure.time.strftime('%H:%M') if departure and departure.time else '',
            'details': departure.details if departure else '',
        })
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return redirect('bookings:details', reference=reference)

        def parsed_time(raw):
            raw = (raw or '').strip()
            if not raw:
                return None
            try:
                return datetime.strptime(raw, '%H:%M').time()
            except ValueError:
                return None

        def parsed_method(raw):
            return raw if raw in TravelMethod.values else TravelMethod.FLIGHT_FARO

        arrival_data = {
            'method': parsed_method(request.POST.get('arrival_method')),
            'flight_number': request.POST.get('arrival_flight_number', '').strip(),
            'travelling_from': request.POST.get('arrival_travelling_from', '').strip(),
            'hiring_car': request.POST.get('arrival_hiring_car') == 'yes',
            'time': request.POST.get('arrival_time', '').strip(),
            'details': request.POST.get('arrival_details', '').strip()[:140],
        }
        departure_data = {
            'method': parsed_method(request.POST.get('departure_method')),
            'flight_number': request.POST.get('departure_flight_number', '').strip(),
            'travelling_from': request.POST.get('departure_travelling_from', '').strip(),
            'time': request.POST.get('departure_time', '').strip(),
            'details': request.POST.get('departure_details', '').strip()[:140],
        }

        errors = {}
        flight_number_hint = "That doesn't look like a flight number (e.g. TP1234) - please double-check it."
        if not self._valid_flight_number(arrival_data['method'], arrival_data['flight_number']):
            errors['arrival_flight_number'] = flight_number_hint
        if not self._valid_flight_number(departure_data['method'], departure_data['flight_number']):
            errors['departure_flight_number'] = flight_number_hint

        if errors:
            context = self._context(booking, arrival_data, departure_data)
            context['errors'] = errors
            return render(request, self.template_name, context)

        arrival, _ = Arrival.objects.get_or_create(booking=booking, defaults={
            'self_check_in': True, 'meet_greet': False,
        })
        arrival.method = arrival_data['method']
        arrival.flight_number = arrival_data['flight_number']
        arrival.travelling_from = arrival_data['travelling_from']
        arrival.hiring_car = arrival_data['hiring_car']
        arrival.time = parsed_time(arrival_data['time'])
        arrival.details = arrival_data['details']
        arrival.save(update_fields=[
            'method', 'flight_number', 'travelling_from', 'hiring_car', 'time', 'details',
        ])

        departure, _ = Departure.objects.get_or_create(
            booking=booking, defaults={'clean': False, 'manual_date': False},
        )
        departure.method = departure_data['method']
        departure.flight_number = departure_data['flight_number']
        departure.travelling_from = departure_data['travelling_from']
        departure.time = parsed_time(departure_data['time'])
        departure.details = departure_data['details']
        departure.save(update_fields=['method', 'flight_number', 'travelling_from', 'time', 'details'])

        return redirect(f"{reverse('bookings:manage_arrival_departure', args=[booking.reference])}?saved=1")

    @staticmethod
    def _valid_flight_number(method, flight_number):
        if method not in (TravelMethod.FLIGHT_FARO, TravelMethod.FLIGHT_LISBON) or not flight_number:
            return True
        return bool(FLIGHT_NUMBER_RE.match(flight_number))

    @staticmethod
    def _context(booking, arrival_data, departure_data):
        context = {
            'booking': booking,
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
        context.update(_manage_nav_context(booking, 'arrival_departure'))
        return context


class BookingManageGuestAddView(BookingFormMixin, View):
    """Guest-list *increases* once a booking is already fully paid (see is_fully_paid()) - the
    hub's only guest-list write path at that point, since Charge is frozen for good by then. Never
    edits or deletes an existing BookingGuest row - only ever appends new ones, each tagged
    added_via_adjustment so the cash owed for them is traceable back to its GuestListAdjustment
    audit row. GET has nothing to show on its own, so it just bounces back to the Guest List
    section; POST is stateless (two-step confirm via a repeated `confirmed` field, not session
    state) for the same reason BookingBalanceDetailsView's POST is - this is reached from a
    bookmarked/emailed link days or weeks later, no session continuity to lean on."""
    template_name = 'bookings/manage_guests.html'

    def get(self, request, reference, *args, **kwargs):
        return redirect('bookings:manage_guests', reference=reference)

    def post(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_fully_paid(booking):
            return redirect('bookings:manage_guests', reference=reference)

        max_guests = booking.property.specs.max_guests
        new_rows, non_field_error = self._parse_rows(request.POST)
        context = {'booking': booking, 'party': booking.party.all(), 'max_guests': max_guests}
        context.update(_manage_nav_context(booking, 'guests'))
        context['guest_add_rows'] = new_rows
        context['guest_add_error'] = non_field_error

        if non_field_error or any(row['errors'] for row in new_rows):
            return render(request, self.template_name, context)

        existing_party = list(booking.party.all())
        if len(existing_party) + len(new_rows) > max_guests:
            context['guest_add_error'] = f"This property allows a maximum of {max_guests} guests."
            return render(request, self.template_name, context)

        ages = [guest.age for guest in existing_party] + [int(row['age']) for row in new_rows]
        new_guests, new_costs, _ = recalculate_costs_for_party(booking, ages)
        if new_guests is None:
            context['guest_add_error'] = (
                "This stay can no longer be priced automatically - please contact us to add a guest."
            )
            return render(request, self.template_name, context)

        charge = booking.charges
        additional_charge = max(new_costs['subtotal'] - (charge.basic_rental + charge.admin), Decimal('0'))

        if request.POST.get('confirmed') != '1':
            context['pending_guest_addition'] = {'rows': new_rows, 'additional_charge': additional_charge}
            return render(request, self.template_name, context)

        with transaction.atomic():
            adjustment = GuestListAdjustment.objects.create(
                booking=booking,
                previous_party_size=len(existing_party),
                new_party_size=len(existing_party) + len(new_rows),
                additional_charge=additional_charge,
            )
            self._append_guest_rows(booking, new_rows, adjustment, new_guests)

        return redirect(f"{reverse('bookings:manage_guests', args=[booking.reference])}?guest_added=1")


class BookingManageExtrasView(BookingFormMixin, View):
    """Extras section of the Manage Booking hub - reachable as soon as the deposit is paid, same as
    Guest List and Arrival & Departure now (no balance-paid gate). Gated only by
    extras_edit_locked()'s fulfilment-lead-time cutoff, not by payment status, since Extras are
    cash-at-check-in and were never priced into Charge (see extras_summary()'s docstring). No
    price-change interstitial needed here at all for the same reason."""
    template_name = 'bookings/manage_extras.html'

    def _get_gated_booking(self, reference):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return booking, redirect('bookings:details', reference=reference)
        return booking, None

    def _show_cot_high_chair(self, booking):
        """Same infant-age check _any_infant_age() does for a freshly-typed guest-list form, but
        against the booking's actual saved party - this page no longer has a guest-list form on it
        to check ages from directly, since Guest List is now its own separate section."""
        child_min_age = BookingSettings.load().child_min_age
        rows = [{'age': guest.age} for guest in booking.party.all()]
        return self._any_infant_age(rows, child_min_age)

    def get(self, request, reference, *args, **kwargs):
        booking, redirect_response = self._get_gated_booking(reference)
        if redirect_response is not None:
            return redirect_response

        context = {'booking': booking, 'extras_locked': extras_edit_locked(booking),
                   'show_cot_high_chair': self._show_cot_high_chair(booking)}
        context.update(_manage_nav_context(booking, 'extras'))
        context.update(self._extras_context(booking))
        context.update(self._transfer_context(booking))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        booking, redirect_response = self._get_gated_booking(reference)
        if redirect_response is not None:
            return redirect_response

        if extras_edit_locked(booking):
            return redirect('bookings:manage_extras', reference=reference)

        transfer_rows, transfer_non_field_error = self._parse_transfer_rows(request.POST)
        _, _, late_checkout_error = self._parse_late_checkout(request.POST)
        context = {'booking': booking, 'extras_locked': False,
                   'show_cot_high_chair': self._show_cot_high_chair(booking)}
        context.update(_manage_nav_context(booking, 'extras'))
        context.update(self._extras_context(booking, post_data=request.POST))
        context.update(self._transfer_context(booking, rows=transfer_rows, non_field_error=transfer_non_field_error))
        context['late_checkout_error'] = late_checkout_error

        if transfer_non_field_error or any(row['errors'] for row in transfer_rows):
            return render(request, self.template_name, context)
        if late_checkout_error:
            return render(request, self.template_name, context)

        with transaction.atomic():
            self._save_extras(booking, request.POST)
            self._save_transfers(booking, transfer_rows)

        return redirect(f"{reverse('bookings:manage_extras', args=[booking.reference])}?extras_saved=1")


class BookingCancelView(View):
    """Self-service cancellation of an already-paid booking - genuinely different from
    cancel_booking_hold() (bookings/utils.py), which only ever acts on a not-yet-paid hold and is
    explicitly a no-op on anything already paid. No refund logic here or anywhere else in this
    codebase - Thomas confirmed cancelling here never refunds anything already paid, and the
    confirm page states that explicitly rather than leaving it implicit. Hidden (and re-checked
    server-side) for platform-sourced bookings, an already-cancelled booking, and a booking whose
    stay has already started - see _manage_nav_context()'s show_cancel_booking, the single source
    of truth this view's own gate reuses. Type-to-confirm (the guest must retype their own
    reference) rather than a single click, given how consequential and irreversible this is."""
    template_name = 'bookings/manage_cancel.html'

    def _get_gated_booking(self, reference):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if not is_paid(booking):
            return booking, redirect('bookings:details', reference=reference)
        nav = _manage_nav_context(booking, 'cancel')
        if not nav['show_cancel_booking']:
            return booking, redirect('bookings:manage_hub', reference=reference)
        return booking, None

    def get(self, request, reference, *args, **kwargs):
        booking, redirect_response = self._get_gated_booking(reference)
        if redirect_response is not None:
            return redirect_response

        context = {'booking': booking, 'reference_error': None}
        context.update(_manage_nav_context(booking, 'cancel'))
        return render(request, self.template_name, context)

    def post(self, request, reference, *args, **kwargs):
        booking, redirect_response = self._get_gated_booking(reference)
        if redirect_response is not None:
            return redirect_response

        typed_reference = request.POST.get('reference_confirm', '').strip()
        if typed_reference.upper() != booking.reference.upper():
            context = {
                'booking': booking,
                'reference_error': "That doesn't match your booking reference - please try again.",
            }
            context.update(_manage_nav_context(booking, 'cancel'))
            return render(request, self.template_name, context)

        booking.enquiry_status = 'Cancelled by guest'
        booking.save(update_fields=['enquiry_status'])
        return redirect(f"{reverse('bookings:manage_hub', args=[booking.reference])}?cancelled=1")
