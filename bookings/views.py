from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

import env_settings
from bookings.forms import BookingLookupForm
from bookings.models import (
    Booking, BookingCondition, BookingGuest, BookingRequestedExtra, Extra, RequestType, WelcomePackDrinksChoice,
    WelcomePackFoodChoice, WelcomePackItem,
)
from bookings.utils import (
    booking_confirmation_context, cancel_booking_hold, recalculate_costs_for_party, reservation_retry_url,
)
from libraries.banking.revolut import Revolut

MAX_GUEST_AGE = 120


def is_paid(booking):
    """A booking with no Payment row at all predates this feature or was platform-synced - never
    part of the deposit-payment flow, so treat it as paid (i.e. don't gate it)."""
    payment = getattr(booking, 'payment', None)
    return payment is None or payment.status == 'paid'


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


class BookingDetailsView(View):
    """Booking-details step shown right after a reservation is created, before payment - the
    guest-list section (first/last name + age per party member) plus a Welcome Pack & Requests
    section (RequestType catalog items, cash-at-checkin, no interaction with the online Charge/
    Payment total), with room for future sections (arrival/departure coordination) on the same
    page - see the plan this was built from for context. GET is bearer-readable like every other
    reference-based view; POST is a write (it can change Booking.adults/children/babies and the
    locked Charge, plus the guest's extras) and is gated the same way BookingPaymentCancelView
    gates cancellation - see that class's docstring."""
    template_name = 'bookings/details.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        if is_paid(booking):
            return redirect('bookings:confirmation', reference=reference)

        payment = booking.payment
        context = {
            'booking': booking,
            'rows': self._seed_or_prefill_rows(booking),
            'max_guests': booking.property.specs.max_guests,
            'hold_expired': booking.hold_expires_at is not None and booking.hold_expires_at <= timezone.now(),
            'payment_in_progress': payment.status == 'in_progress',
        }
        context.update(self._extras_context(booking))
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

        max_guests = booking.property.specs.max_guests
        rows, non_field_error = self._parse_rows(request.POST)
        context = {
            'booking': booking,
            'rows': rows,
            'max_guests': max_guests,
            'hold_expired': False,
            'payment_in_progress': False,
            'non_field_error': non_field_error,
        }
        context.update(self._extras_context(booking, post_data=request.POST))

        if non_field_error or any(row['errors'] for row in rows):
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

            self._save_extras(booking, request.POST)

        return redirect('bookings:pay', reference=booking.reference)

    def _save_extras(self, booking, post_data):
        """Welcome Pack + RequestType selections are cash-at-checkin (see the plan this was built
        from) so, unlike the guest list, they never touch Charge/Payment - just persisted as-is.
        The pack's food/drinks choices are only meaningful (and only stored) when welcome_pack is
        actually wanted - a fixed pair of picks, not a freeform swap request (see the memory this
        was rebuilt from after the first version's freeform text field turned out to invite too
        much back-and-forth for a two-person operation)."""
        extra, _ = Extra.objects.get_or_create(booking=booking)
        extra.welcome_pack = post_data.get('welcome_pack') == 'on'
        if extra.welcome_pack:
            food = post_data.get('welcome_pack_food', '')
            extra.welcome_pack_food = food if food in WelcomePackFoodChoice.values else WelcomePackFoodChoice.STANDARD
            drinks = post_data.get('welcome_pack_drinks', '')
            extra.welcome_pack_drinks = (
                drinks if drinks in WelcomePackDrinksChoice.values else WelcomePackDrinksChoice.ALCOHOLIC
            )
            extra.welcome_pack_note = post_data.get('welcome_pack_note', '').strip()
        else:
            extra.welcome_pack_food = None
            extra.welcome_pack_drinks = None
            extra.welcome_pack_note = ''
        extra.save(update_fields=['welcome_pack', 'welcome_pack_food', 'welcome_pack_drinks', 'welcome_pack_note'])

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
            existing = {r.request_type_id: r for r in booking.requested_extras.all()}
            quantities = {t.id: str(existing[t.id].quantity) if t.id in existing else '0' for t in active_types}
            notes = {t.id: existing[t.id].note if t.id in existing else '' for t in active_types}

        return {
            'welcome_pack_items': WelcomePackItem.objects.filter(active=True),
            'welcome_pack': welcome_pack,
            'welcome_pack_food': welcome_pack_food,
            'welcome_pack_drinks': welcome_pack_drinks,
            'welcome_pack_note': welcome_pack_note,
            'request_rows': [
                {'request_type': t, 'quantity': quantities[t.id], 'note': notes[t.id]}
                for t in active_types
            ],
        }

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
    """Reference + email lookup for a guest returning later without their confirmation link."""
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
                context.update(booking_confirmation_context(booking))
            else:
                context['not_found'] = True
        return render(request, self.template_name, context)
