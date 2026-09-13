from django.shortcuts import render
from django.views import View, generic
from properties.models import Property, Location
from properties.utils import get_stay_total_price
from bookings.models import Booking, BookingSettings
from availability.utils import (
    date_string_to_date, even_split_guests, find_property_combo_suggestions, full_toolbar_context,
    guests_string_to_dict,
)

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
        context['combo_suggestions'] = []
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
            # Only offer a "book two apartments together" suggestion once no single property fits
            # at all - a party that already fits one property has no reason to be offered two.
            if not available_properties:
                context['combo_suggestions'] = find_property_combo_suggestions(start_date, end_date, guests)
                for suggestion in context['combo_suggestions']:
                    self._add_estimated_price(suggestion, start_date, end_date, guests, booking_settings)
        return render(request, self.template_name, context)

    def _add_estimated_price(self, suggestion, start_date, end_date, guests, booking_settings):
        """An even-split price estimate for a combo suggestion card (2026-09-13, per Thomas) -
        the guest hasn't chosen an actual split yet (that's MultiPropertyReserveView's job), so
        this is a "roughly this much" figure, not a quote. None if either leg can't be priced at
        all (see get_stay_total_price) - the template just omits the price rather than showing a
        wrong one."""
        splits = even_split_guests(suggestion['properties'], guests)
        estimated_total = 0
        for property, split_guests in zip(suggestion['properties'], splits):
            pricing = get_stay_total_price(
                property, start_date, end_date, split_guests,
                monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
            )
            if pricing is None:
                suggestion['estimated_total'] = None
                suggestion['estimated_total_gbp'] = None
                return
            rental_total = pricing['basic_total'] - pricing['discount_total'] + pricing['extra_guest_total']
            estimated_total += booking_settings.compute_costs(rental_total, arrival_date=start_date)['subtotal']
        suggestion['estimated_total'] = estimated_total
        suggestion['estimated_total_gbp'] = booking_settings.to_gbp(estimated_total)

    def get_available_properties(self, start_date, end_date, guests):

        properties = Property.objects.bookable_on_website().filter(
            #specs__bedrooms__gte=guests.get('adults', 0) - 1 + guests.get('children', 0) - 1, # Assuming 1 bedroom can accommodate 2 adults or 2 children
            specs__max_guests__gte=guests.get('adults', 0) + guests.get('children', 0) + guests.get('infants', 0),
            # max_adults is a separate, tighter cap than max_guests (see Booking.clean()'s own
            # comment, added alongside this 2026-09-13) - a property sleeping 3 total but only 2 of
            # those as adults must not appear here for a 3-adult search just because 3 <= max_guests.
            specs__max_adults__gte=guests.get('adults', 0),
        )

        for property in properties:
            if Booking.objects.overlapping(property, start_date, end_date).exists():
                properties = properties.exclude(id=property.id)
        return properties