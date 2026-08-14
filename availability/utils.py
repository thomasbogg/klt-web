from properties.models import Location, Property

def full_toolbar_context():
    return {
        'toolbar_date_picker_start_name': 'start',
        'toolbar_date_picker_end_name': 'end',
        'toolbar_guests_picker_name': 'guests',
        'toolbar_guests_picker_groups': [
            ('adults', '2', '1', '10'),
            ('children', '0', '0', '10'),
            ('infants', '0', '0', '10'),
        ],
        'toolbar_location_picker_name': 'location',
        'toolbar_location_picker_list': Location.objects.order_by('title'),
        'toolbar_bedrooms_picker_name': 'bedrooms',
        'toolbar_bedrooms_picker_groups': [
            ('bedrooms', '1', '1', '3')
        ],
    }