from django.http import Http404
from django.shortcuts import render
from django.views import View

from bookings.forms import BookingLookupForm
from bookings.models import Booking
from bookings.utils import booking_confirmation_context


class BookingConfirmationView(View):
    """Landing page after a successful reservation - looked up by reference alone (a bearer link,
    like a checkout confirmation), not requiring the email too."""
    template_name = 'bookings/confirmation.html'

    def get(self, request, reference, *args, **kwargs):
        booking = Booking.objects.filter(reference=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        return render(request, self.template_name, booking_confirmation_context(booking))


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
            if booking is not None:
                context.update(booking_confirmation_context(booking))
            else:
                context['not_found'] = True
        return render(request, self.template_name, context)
