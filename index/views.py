from django.http import HttpResponse
from django.views import generic
from properties.models import Location, Property
from libraries.dates import dates

# Create your views here.

class IndexView(generic.ListView):
    template_name = 'index/index.html'
    context_object_name = 'locations_list'

    def get_queryset(self):
        # Only a Location with at least one bookable-on-website property earns a homepage tile
        # (2026-09-08, per Thomas) - the same gate availability/views.py::get_available_properties
        # uses for search results (Property.objects.bookable_on_website()), so a location whose
        # only properties are inactive or booked through a company that doesn't sell via this site
        # doesn't show up with nothing a guest can actually book there.
        bookable_location_ids = Property.objects.bookable_on_website().values_list('location_id', flat=True)
        return Location.objects.filter(pk__in=bookable_location_ids).order_by("title")
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Date Picker Settings
        context['datepicker_start_name'] = 'start'
        context['datepicker_end_name'] = 'end'
   
        # Group Picker Settings
        context['grouppicker_name'] = 'guests'
        context['grouppicker_groups'] = [
            ('adults', '2', '1', '10', 'Adults', 'Ages 13 or above'), # Default 2 adults, min 1, max 10
            ('children', '0', '0', '10', 'Children', 'Ages 2 – 12'), # Default 0 children, min 0, max 10
            ('infants', '0', '0', '10', 'Infants (Cots)', 'Under 2'), # Default 0 infants, min 0, max 10
        ]
        return context