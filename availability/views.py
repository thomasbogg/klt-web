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
        # Not full_toolbar_context() here (2026-09-16, per Thomas) - the GET-params loop below
        # only needs a plain dict to write start_date/end_date/guests into before the one real
        # full_toolbar_context(start_date, end_date, guests) call further down; calling it twice
        # just to have every key immediately overwritten was one of the redundant BookingSettings
        # queries behind the search page's ~6s latency.
        context = {}
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
        booking_settings = BookingSettings.load()
        context.update(full_toolbar_context(start_date, end_date, guests, booking_settings=booking_settings))
        context['guests'] = guests
        context['has_search'] = bool(start_date and end_date)
        context['combo_suggestions'] = []
        context['booking_settings'] = booking_settings
        context['too_far_ahead'] = context['has_search'] and start_date > booking_settings.max_bookable_date()
        if context['has_search']:
            available_properties = list(self.get_available_properties(start_date, end_date, guests))
            for property in available_properties:
                # on_sale=False covers both gaps a guest can hit here: the whole search is beyond
                # max_advance_booking_months (every property in this loop shares that same verdict,
                # since it's a function of start_date alone), or this particular property just has
                # no Price rows covering the stay yet. Either way the property still belongs in the
                # results (it fits capacity-wise and isn't booked) - see tile.html for the
                # "not on sale yet, contact me" card treatment this drives instead of a price.
                property.on_sale = not context['too_far_ahead']
                property.stay_total_price = None
                property.stay_total_price_gbp = None
                if property.on_sale:
                    pricing = get_stay_total_price(
                        property, start_date, end_date, guests,
                        monthly_discount_min_nights=booking_settings.monthly_discount_min_nights,
                    )
                    if pricing is not None:
                        rental_total = pricing['basic_total'] - pricing['discount_total'] + pricing['extra_guest_total']
                        property.stay_total_price = booking_settings.compute_costs(rental_total, arrival_date=start_date)['subtotal']
                        property.stay_total_price_gbp = booking_settings.to_gbp(property.stay_total_price)
                    else:
                        property.on_sale = False
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

        # select_related('specs', 'location'): the pricing loop below reads property.specs.bedrooms
        # (extra-guest allowance, see properties/utils.py::free_guest_allowance), and tile.html
        # reads property.location.* for every card - either would otherwise be one extra query per
        # property. prefetch_related('images'): tile.html's card thumbnail (2026-09-16, per Thomas
        # - these three N+1s plus the per-property availability check below were what made the
        # search page take ~6s against the remote DB) - one query for all properties' images up
        # front instead of one round trip per card via property.images.first.
        properties = Property.objects.bookable_on_website().select_related('specs', 'location').prefetch_related('images').filter(
            #specs__bedrooms__gte=guests.get('adults', 0) - 1 + guests.get('children', 0) - 1, # Assuming 1 bedroom can accommodate 2 adults or 2 children
            specs__max_guests__gte=guests.get('adults', 0) + guests.get('children', 0) + guests.get('infants', 0),
            # max_adults is a separate, tighter cap than max_guests (see Booking.clean()'s own
            # comment, added alongside this 2026-09-13) - a property sleeping 3 total but only 2 of
            # those as adults must not appear here for a 3-adult search just because 3 <= max_guests.
            specs__max_adults__gte=guests.get('adults', 0),
        )

        # One query instead of one Booking.objects.overlapping().exists() round trip per
        # candidate property (2026-09-16, per Thomas - this was the search page's ~6s latency:
        # a remote-DB round trip per property adds up fast). property_id__in on the same
        # `properties` queryset lets Django fold this into one subquery rather than a second
        # round trip for the ids.
        unavailable_property_ids = Booking.objects.holding().filter(
            property_id__in=properties.values('id'),
            arrival_date__lt=end_date,
            departure_date__gt=start_date,
        ).values_list('property_id', flat=True)
        return properties.exclude(id__in=unavailable_property_ids)