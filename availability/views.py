from django.shortcuts import render
from django.views import View, generic
from properties.models import Property, Location
from properties.utils import get_stay_total_price
from bookings.models import Booking, BookingSettings
from availability.utils import date_string_to_date, full_toolbar_context, guests_string_to_dict

# Create your views here.

class IndexView(generic.TemplateView):
    template_name = 'availability/index.html'


class SearchView(View):
    template_name = 'availability/search.html'

    def get(self, request, *args, **kwargs):
        context = full_toolbar_context()
        for key, value in request.GET.items():
            if 'start' in key:
                context['start_date'] = date_string_to_date(value)
                context['start_query'] = value
            elif 'end' in key:
                context['end_date'] = date_string_to_date(value)
                context['end_query'] = value
            elif 'guests' in key:
                context['guests'] = guests_string_to_dict(value)
                context['guests_query'] = value
        start_date = context.get('start_date')
        end_date = context.get('end_date')
        guests = context.get('guests', {})
        context.update(full_toolbar_context(start_date, end_date, guests))
        context['guests'] = guests
        context['has_search'] = bool(start_date and end_date)
        if context['has_search']:
            available_properties = list(self.get_available_properties(start_date, end_date, guests))
            booking_settings = BookingSettings.load()
            for property in available_properties:
                pricing = get_stay_total_price(
                    property, start_date, end_date, guests,
                    monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
                )
                property.stay_total_price = None
                if pricing is not None:
                    rental_total = pricing['basic_total'] - pricing['discount_total'] + pricing['extra_guest_total']
                    property.stay_total_price = booking_settings.compute_costs(rental_total, arrival_date=start_date)['subtotal']
                property.stay_total_price_gbp = (
                    booking_settings.to_gbp(property.stay_total_price)
                    if property.stay_total_price is not None else None
                )
            context['available_properties'] = available_properties
            context['nights'] = (end_date - start_date).days
        return render(request, self.template_name, context)

    def get_available_properties(self, start_date, end_date, guests):

        properties = Property.objects.filter(
            #specs__bedrooms__gte=guests.get('adults', 0) - 1 + guests.get('children', 0) - 1, # Assuming 1 bedroom can accommodate 2 adults or 2 children
            specs__max_guests__gte=guests.get('adults', 0) + guests.get('children', 0) + guests.get('infants', 0),
            booking_company__isnull=False, # Exclude properties we don't book
        )

        for property in properties:
            if Booking.objects.overlapping(property, start_date, end_date).exists():
                properties = properties.exclude(id=property.id)
        return properties