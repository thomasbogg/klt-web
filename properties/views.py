from django.shortcuts import get_object_or_404
from .models import Property, Location, Price
from django.views import generic
from availability.utils import (
    date_string_to_date,
    full_toolbar_context,
    get_property_calendar,
    guests_string_to_dict,
)

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['location'] = self.object.location
        try:
            context['start_date'] = date_string_to_date(self.request.GET.get('start', ''))
            context['end_date'] = date_string_to_date(self.request.GET.get('end', ''))
            context['guests'] = guests_string_to_dict(self.request.GET.get('guests', ''))
        except (ValueError, TypeError):
            context['start_date'] = context['end_date'] = None
            context['guests'] = {}
        return context


class DetailView(generic.DetailView):
    model = Location
    template_name = 'properties/detail.html'