from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

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
