from django.shortcuts import render
from django.views import View, generic
from properties.models import Property, Location
from properties.utils import get_stay_total_price
from bookings.models import Booking
from env_settings import VALID_BOOKING_STATUSES
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
        context.update(full_toolbar_context(start_date, end_date, context.get('guests')))
        available_properties = list(self.get_available_properties(start_date, end_date, context['guests']))
        for property in available_properties:
            property.stay_total_price = get_stay_total_price(property, start_date, end_date)
        context['available_properties'] = available_properties
        context['nights'] = (end_date - start_date).days if start_date and end_date else None
        return render(request, self.template_name, context)

    def get_available_properties(self, start_date, end_date, guests):

        properties = Property.objects.filter(
            #specs__bedrooms__gte=guests.get('adults', 0) - 1 + guests.get('children', 0) - 1, # Assuming 1 bedroom can accommodate 2 adults or 2 children
            specs__max_guests__gte=guests.get('adults', 0) + guests.get('children', 0) + guests.get('infants', 0),
            we_book=True, # Exclude properties we don't book
        )

        for property in properties:
            overlapping_bookings = Booking.objects.filter(
                property=property,
                arrival_date__lt=end_date,
                departure_date__gt=start_date,
                enquiry_status__in=VALID_BOOKING_STATUSES
            )
            if overlapping_bookings.exists():
                properties = properties.exclude(id=property.id)
        return properties