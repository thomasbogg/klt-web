from django.test import TestCase

from properties.models import Location, ManagementCompany, Property


class IndexViewLocationTilesTests(TestCase):
    """The homepage only gives a tile to a Location with at least one bookable-on-website
    property (2026-09-08, per Thomas - a location whose only properties are inactive, or booked
    through a company that's opted out of website sales, has nothing a guest could actually book
    there, so it shouldn't funnel guests toward it). Reuses Property.objects.bookable_on_website(),
    the same gate availability/views.py's search results already apply - see
    properties.tests.PropertyBookableOnWebsiteQuerySetTests for that queryset method's own
    standalone coverage."""

    def setUp(self):
        self.management_company = ManagementCompany.objects.create(name='Index Test Co')

    def _make_location(self, title):
        return Location.objects.create(
            title=title, street='Test St', zip_code='0000',
            city='Test City', coordinates='37.0,-8.0', map_link='https://example.com',
        )

    def test_location_with_a_bookable_property_gets_a_tile(self):
        location = self._make_location('Bookable Location')
        Property.objects.create(
            title=f'{location} - A1', short_title='INDEXBOOKABLE',
            location=location, booking_company=self.management_company,
        )
        response = self.client.get('/')
        self.assertIn(location, response.context['locations_list'])

    def test_location_with_no_properties_at_all_gets_no_tile(self):
        location = self._make_location('Empty Location')
        response = self.client.get('/')
        self.assertNotIn(location, response.context['locations_list'])

    def test_location_whose_only_property_has_no_booking_company_gets_no_tile(self):
        location = self._make_location('Untracked Location')
        Property.objects.create(title=f'{location} - A1', short_title='INDEXUNTRACKED', location=location)
        response = self.client.get('/')
        self.assertNotIn(location, response.context['locations_list'])

    def test_location_whose_company_opted_out_of_website_sales_gets_no_tile(self):
        location = self._make_location('Opted Out Location')
        other_company = ManagementCompany.objects.create(name='Index Test External Co', bookable_on_website=False)
        Property.objects.create(
            title=f'{location} - A1', short_title='INDEXOPTOUT', location=location, booking_company=other_company,
        )
        response = self.client.get('/')
        self.assertNotIn(location, response.context['locations_list'])

    def test_location_whose_only_property_is_inactive_gets_no_tile(self):
        location = self._make_location('Inactive Only Location')
        Property.objects.create(
            title=f'{location} - A1', short_title='INDEXINACTIVE',
            location=location, booking_company=self.management_company, active=False,
        )
        response = self.client.get('/')
        self.assertNotIn(location, response.context['locations_list'])
