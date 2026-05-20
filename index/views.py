from django.http import HttpResponse
from django.views import generic
from properties.models import Location
from dates import dates

# Create your views here.

class IndexView(generic.ListView):
    template_name = 'index/index.html'
    context_object_name = 'locations_list'
    
    def get_queryset(self):
        return Location.objects.order_by("title")
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        ## TOOLBAR CONFIGURATION ##
        toolbar_name = 'availability-toolbar-'
        
        # Date Picker Settings
        context['toolbar_date_picker_start_name'] = toolbar_name + 'start'
        context['toolbar_date_picker_end_name'] = toolbar_name + 'end'
   
        # Group Picker Settings
        context['toolbar_group_picker_name'] = toolbar_name + 'guests'
        context['toolbar_group_picker_groups'] = [
            ('adults', '2', '1', '10'), # Default 2 adults, min 1, max 10
            ('children', '0', '0', '10'), # Default 0 children, min 0, max 10
            ('infants', '0', '0', '10'), # Default 0 infants, min 0, max 10
        ]
        return context