from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

import env_settings
from bookings.forms import BookingLookupForm
from bookings.models import Booking, BookingCondition
from bookings.utils import booking_confirmation_context, cancel_booking_hold, reservation_retry_url
from libraries.banking.revolut import Revolut


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
