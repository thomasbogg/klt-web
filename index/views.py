from django.http import HttpResponse
from django.views import generic
from properties.models import Location
from libraries.dates import dates

# Create your views here.

class IndexView(generic.ListView):
    template_name = 'index/index.html'
    context_object_name = 'locations_list'
    
    def get_queryset(self):
        return Location.objects.order_by("title")
    
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