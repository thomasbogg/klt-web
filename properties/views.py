from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views import generic

from .models import Property, Location, Price
from .utils import get_stay_total_price
from availability.utils import (
    date_string_to_date,
    full_toolbar_context,
    get_property_calendar,
    guests_string_to_dict,
)
from bookings.forms import ReservationForm
from bookings.models import Booking, BookingSettings
from bookings.utils import create_booking

# Create your views here.

def split_slug(slug):
    return slug.replace('-', ' ').upper()


def get_object_with_slug_or_404(slug, Object, **kwargs):
    title = split_slug(slug)
    object = get_object_or_404(Object, title__iexact=title, **kwargs)
    return object


def get_property_from_slugs(location_slug, property_slug):
    location = get_object_with_slug_or_404(location_slug, Location)
    return get_object_or_404(Property, title__iexact=f'{location} - {property_slug}')


class LocationView(generic.DetailView):
    template_name = 'properties/location/page.html'
    context_object_name = 'location'

    def get_object(self):
        title = self.kwargs.get('title')
        location = get_object_with_slug_or_404(title, Location)
        return location

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(full_toolbar_context())
        location = self.get_object()
        properties = Property.objects.filter(location_id__exact=location.id)
        context['properties'] = properties
        return context


class PropertyView(generic.DetailView):
    template_name = 'properties/property/page.html'
    context_object_name = 'property'

    def get_object(self):
        return get_property_from_slugs(self.kwargs.get('location'), self.kwargs.get('title'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        start_date = end_date = None
        guests = {}
        try:
            start_date = date_string_to_date(self.request.GET.get('start', ''))
            end_date = date_string_to_date(self.request.GET.get('end', ''))
            guests = guests_string_to_dict(self.request.GET.get('guests', ''))
        except (ValueError, TypeError):
            start_date = end_date = None
            guests = {}

        context.update(full_toolbar_context(start_date, end_date, guests))
        context['toolbar_compact'] = bool(start_date and end_date)
        context['location'] = self.object.location
        context['calendar_months'] = get_property_calendar(self.object)
        return context


class ReserveView(generic.DetailView):
    template_name = 'properties/property/reserve.html'
    context_object_name = 'property'

    def get_object(self):
        return get_property_from_slugs(self.kwargs.get('location'), self.kwargs.get('title'))

    def is_still_available(self, property, start_date, end_date):
        return not Booking.objects.overlapping(property, start_date, end_date).exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['location'] = self.object.location
        # POST redisplay (form errors) carries dates in the POST body, not the querystring.
        params = self.request.POST if self.request.method == 'POST' else self.request.GET
        try:
            start_date = date_string_to_date(params.get('start', ''))
            end_date = date_string_to_date(params.get('end', ''))
            guests = guests_string_to_dict(params.get('guests', ''))
        except (ValueError, TypeError):
            start_date = end_date = None
            guests = {}
        context['start_date'] = start_date
        context['end_date'] = end_date
        context['guests'] = guests
        context['start_query'] = params.get('start', '')
        context['end_query'] = params.get('end', '')
        context['guests_query'] = params.get('guests', '')

        if not start_date or not end_date:
            context['is_available'] = False
            return context

        context['nights'] = (end_date - start_date).days
        context['is_available'] = self.is_still_available(self.object, start_date, end_date)

        if context['is_available']:
            booking_settings = BookingSettings.load()
            rental_total = get_stay_total_price(
                self.object, start_date, end_date, guests,
                monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
            )
            if rental_total is None:
                context['is_available'] = False  # can't book a stay we can't price
            else:
                context['costs'] = booking_settings.compute_costs(rental_total, arrival_date=start_date)
                context['costs_gbp'] = booking_settings.costs_in_gbp(context['costs'])
                if 'form' not in context:
                    context['form'] = ReservationForm(initial={
                        'start': self.request.GET.get('start', ''),
                        'end': self.request.GET.get('end', ''),
                        'guests': self.request.GET.get('guests', ''),
                    })
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = ReservationForm(request.POST)
        if form.is_valid():
            try:
                booking = create_booking(
                    self.object,
                    {
                        'first_name': form.cleaned_data['first_name'],
                        'last_name': form.cleaned_data['last_name'],
                        'email': form.cleaned_data['email'],
                        'phone': form.cleaned_data['phone'],
                    },
                    form.cleaned_data['start'],
                    form.cleaned_data['end'],
                    form.cleaned_data['guests'],
                    currency=form.cleaned_data['currency'],
                )
                return redirect('bookings:pay', reference=booking.reference)
            except ValidationError as error:
                messages = dict.fromkeys(error.messages) if hasattr(error, 'messages') else [str(error)]
                form.add_error(None, '; '.join(messages))
        context = self.get_context_data(form=form)
        return self.render_to_response(context)


class DetailView(generic.DetailView):
    model = Location
    template_name = 'properties/detail.html'