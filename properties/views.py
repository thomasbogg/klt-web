from django.shortcuts import get_object_or_404
from .models import Property, Location, Price
from django.views import generic
from availability.utils import full_toolbar_context

# Create your views here.

def split_slug(slug):
    return slug.replace('-', ' ').upper()


def get_object_with_slug_or_404(slug, Object, **kwargs):
    title = split_slug(slug)
    object = get_object_or_404(Object, title__iexact=title, **kwargs)
    return object


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
        location_slug = self.kwargs.get('location')
        property_slug = self.kwargs.get('title')
        location = get_object_with_slug_or_404(location_slug, Location)
        property = get_object_or_404(Property, title__iexact=f'{location} - {property_slug}')
        return property

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(full_toolbar_context())
        context['location'] = self.object.location
        return context
    

class DetailView(generic.DetailView):
    model = Location
    template_name = 'properties/detail.html'