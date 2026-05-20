from django.template import loader
from django.shortcuts import render#, get_list_or_404
from django.shortcuts import get_object_or_404
from django.http import HttpResponse, Http404
from django.urls import path
from .models import Property, Location, Price
from django.views import generic

# Create your views here.

def split_slug(slug):
    return slug.replace('-', ' ')


def get_object_with_slug_or_404(slug, Object, **kwargs):
    title = split_slug(slug)
    object = get_object_or_404(Object, title__iexact=title, **kwargs)
    return object


def location(request, title):
    location = get_object_with_slug_or_404(title, Location)
    properties = Property.objects.filter(location_id__exact=location.id)
    context = {'location': location, 'properties': properties}
    return render(request, 'properties/location/page.html', context)


def property(request, location, title):
    location = get_object_with_slug_or_404(location, Location)
    property = get_object_with_slug_or_404(title, Property, location__title__iexact=location.title)
    context = {'location': location, 'property': property}
    return render(request, 'properties/property/page.html', context)


class DetailView(generic.DetailView):
    model = Location
    template_name = 'properties/detail.html'