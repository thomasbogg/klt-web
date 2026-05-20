from django.shortcuts import render
from django.views import View, generic
# Create your views here.

class BookingsView(generic.TemplateView):
    template_name = 'bookings/bookings.html'