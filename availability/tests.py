from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from availability.utils import find_property_combo_suggestions
from bookings.models import Booking
from guests.models import Guest
from properties.models import Location, ManagementCompany, Price, Property, PropertySpec


class SearchViewFilteringTests(TestCase):
    """Property.booking_company is the direct replacement for the old we_book boolean (see
    properties/models.py) - SearchView.get_available_properties() must only ever return properties
    that have one set, exactly as it used to only return we_book=True properties."""

    def setUp(self):
        self.location = Location.objects.create(
            title='Search Test Location', street='Test St', zip_code='0000',
            city='Test City', coordinates='37.0,-8.0', map_link='https://example.com',
        )
        self.management_company = ManagementCompany.objects.create(name='Search Test Management Co')
        self.start = date.today() + timedelta(days=330)
        self.end = self.start + timedelta(days=5)
        self.url = reverse('availability:search')
        self.query = {
            'start': self.start.strftime('%d/%m/%Y'),
            'end': self.end.strftime('%d/%m/%Y'),
            'guests': '2 adults,0 children,0 infants',
        }

    def _make_property(self, short_title, **kwargs):
        property = Property.objects.create(
            title=f'{short_title} Property', short_title=short_title,
            location=self.location, **kwargs,
        )
        PropertySpec.objects.create(property=property, max_guests=4, bedrooms=1, bathrooms=1, minimum_nights=1)
        Price.objects.create(
            property=property,
            start_date=date.today(), end_date=self.end + timedelta(days=30), rate=100,
        )
        return property

    def test_property_with_booking_company_appears_in_results(self):
        property = self._make_property('BOOKABLE', booking_company=self.management_company)
        response = self.client.get(self.url, self.query)
        self.assertIn(property, response.context['available_properties'])

    def test_property_without_booking_company_does_not_appear(self):
        property = self._make_property('CLEANONLY', cleaning_company=self.management_company)
        response = self.client.get(self.url, self.query)
        self.assertNotIn(property, response.context['available_properties'])

    def test_property_of_a_non_bookable_on_website_company_does_not_appear(self):
        other_company = ManagementCompany.objects.create(name='External Agency', bookable_on_website=False)
        property = self._make_property('EXTERNAL', booking_company=other_company)
        response = self.client.get(self.url, self.query)
        self.assertNotIn(property, response.context['available_properties'])

    def test_inactive_property_does_not_appear(self):
        property = self._make_property('INACTIVE', booking_company=self.management_company, active=False)
        response = self.client.get(self.url, self.query)
        self.assertNotIn(property, response.context['available_properties'])


class FindPropertyComboSuggestionsTests(TestCase):
    """A party too big for any single property (see MultiPropertyReserveView, ReservationGroup)
    should still be offered a pair of properties at the same location that together fit -
    find_property_combo_suggestions() is what SearchView calls to work that out."""

    def setUp(self):
        self.location = Location.objects.create(
            title='Combo Test Location', street='Test St', zip_code='0000',
            city='Test City', coordinates='37.0,-8.0', map_link='https://example.com',
        )
        self.other_location = Location.objects.create(
            title='Other Combo Location', street='Test St', zip_code='0000',
            city='Test City', coordinates='37.0,-8.0', map_link='https://example.com',
        )
        self.management_company = ManagementCompany.objects.create(name='Combo Test Management Co')
        self.start = date.today() + timedelta(days=330)
        self.end = self.start + timedelta(days=5)
        self.guests = {'adults': 6, 'children': 0, 'infants': 0}

    def _make_property(self, short_title, max_guests, location=None, booking_company=None):
        property = Property.objects.create(
            title=f'{location or self.location} - {short_title}', short_title=short_title,
            location=location or self.location,
            booking_company=booking_company if booking_company is not None else self.management_company,
        )
        PropertySpec.objects.create(property=property, max_guests=max_guests, bedrooms=1, bathrooms=1, minimum_nights=1)
        return property

    def _book(self, property):
        guest = Guest.objects.create(last_name='Blocker')
        Booking.objects.create(
            property=property, guest=guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_suggests_the_tightest_fitting_pair(self):
        self._make_property('SMALL1', 4)
        self._make_property('SMALL2', 4)
        self._make_property('BIG1', 6)
        self._make_property('BIG2', 6)
        suggestions = find_property_combo_suggestions(self.start, self.end, self.guests)
        [suggestion] = [s for s in suggestions if s['location'] == self.location]
        self.assertEqual(suggestion['combined_max_guests'], 8)
        self.assertCountEqual([p.short_title for p in suggestion['properties']], ['SMALL1', 'SMALL2'])

    def test_no_suggestion_when_no_pair_fits(self):
        self._make_property('TOOSMALL1', 2)
        self._make_property('TOOSMALL2', 2)
        suggestions = find_property_combo_suggestions(self.start, self.end, self.guests)
        self.assertFalse([s for s in suggestions if s['location'] == self.location])

    def test_excludes_a_property_already_booked_for_these_dates(self):
        self._make_property('AVAILABLE', 4)
        booked = self._make_property('BOOKED', 4)
        self._book(booked)
        suggestions = find_property_combo_suggestions(self.start, self.end, self.guests)
        self.assertFalse([s for s in suggestions if s['location'] == self.location])

    def test_excludes_a_property_not_bookable_on_website(self):
        self._make_property('OKAY', 4)
        external_company = ManagementCompany.objects.create(name='External Combo Co', bookable_on_website=False)
        self._make_property('EXTERNAL', 4, booking_company=external_company)
        suggestions = find_property_combo_suggestions(self.start, self.end, self.guests)
        self.assertFalse([s for s in suggestions if s['location'] == self.location])

    def test_only_pairs_properties_at_the_same_location(self):
        self._make_property('HERE', 4)
        self._make_property('THERE', 4, location=self.other_location)
        suggestions = find_property_combo_suggestions(self.start, self.end, self.guests)
        self.assertFalse(suggestions)


class SearchViewComboSuggestionsTests(TestCase):
    """SearchView should only ever surface find_property_combo_suggestions() once its own normal
    single-property search comes back empty - a party that already fits one property has no
    reason to be offered two apartments instead."""

    def setUp(self):
        self.location = Location.objects.create(
            title='Search Combo Location', street='Test St', zip_code='0000',
            city='Test City', coordinates='37.0,-8.0', map_link='https://example.com',
        )
        self.management_company = ManagementCompany.objects.create(name='Search Combo Management Co')
        self.start = date.today() + timedelta(days=330)
        self.end = self.start + timedelta(days=5)
        self.url = reverse('availability:search')

    def _make_property(self, short_title, max_guests):
        property = Property.objects.create(
            title=f'{self.location} - {short_title}', short_title=short_title,
            location=self.location, booking_company=self.management_company,
        )
        PropertySpec.objects.create(property=property, max_guests=max_guests, bedrooms=1, bathrooms=1, minimum_nights=1)
        Price.objects.create(
            property=property, start_date=date.today(), end_date=self.end + timedelta(days=30), rate=100,
        )
        return property

    def test_no_combo_suggestions_when_a_single_property_fits(self):
        self._make_property('FITS', 6)
        response = self.client.get(self.url, {
            'start': self.start.strftime('%d/%m/%Y'), 'end': self.end.strftime('%d/%m/%Y'),
            'guests': '6 adults,0 children,0 infants',
        })
        self.assertEqual(response.context['combo_suggestions'], [])

    def test_combo_suggestions_offered_when_nothing_fits_alone(self):
        self._make_property('TOOSMALL1', 4)
        self._make_property('TOOSMALL2', 4)
        response = self.client.get(self.url, {
            'start': self.start.strftime('%d/%m/%Y'), 'end': self.end.strftime('%d/%m/%Y'),
            'guests': '6 adults,0 children,0 infants',
        })
        self.assertEqual(len(response.context['combo_suggestions']), 1)
        self.assertContains(response, 'Book two together')
