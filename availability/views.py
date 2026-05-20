from django.shortcuts import render
from django.views import View, generic
from properties.models import Property
from bookings.models import Booking
from env_settings import VALID_BOOKING_STATUSES
# Create your views here.

class IndexView(generic.TemplateView):
    template_name = 'availability/index.html'


class SearchView(View):
    template_name = 'availability/search.html'

    def get(self, request, *args, **kwargs):
        context = {}
        for key, value in request.GET.items():
            if 'start' in key:
                context['start_date'] = self.date_string_to_date(value)
            elif 'end' in key:                
                context['end_date'] = self.date_string_to_date(value)
            elif 'guests' in key:
                context['guests'] = self.guests_string_to_dict(value)
        context['available_properties'] = self.get_available_properties(
            context['start_date'], context['end_date'], context['guests']
        )
        return render(request, self.template_name, context)

    def date_string_to_date(self, date_string):
        from datetime import datetime
        return datetime.strptime(date_string, '%d/%m/%Y').date()

    def guests_string_to_dict(self, guests_string):
        guests = {}
        for guest in guests_string.split(','):
            value, key = guest.split()
            guests[key] = int(value)
        return guests
    
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