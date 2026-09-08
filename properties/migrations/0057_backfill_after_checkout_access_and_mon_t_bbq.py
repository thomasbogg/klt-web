from django.db import migrations

# Real building-level content, ported from klt-management-software's after_check_out email
# generator (correspondence/guest/departure/after_check_out/run.py) as part of the Last Days &
# Check-out guest hub tab (2026-09-08, per Thomas) - only these two locations actually offer
# after-checkout facility access today, matching that legacy code's own
# isQuintaDaBarracuda/isClubeDoMonaco gate. Staff can edit this going forward from the location
# detail page's "After check-out access" panel.
QUINTA_DA_BARRACUDA_INSTRUCTIONS = (
    'To get in and out of the gated area without your apartment keys, take the blue fob '
    '(pedestrian and beach gate magnet) from one of your key sets before you leave for the last '
    'time - please leave all other keys and fobs on the dining table.\n\n'
    'If you need luggage storage, you\'re welcome to use the private room on the left-hand side '
    'of the seating area in the main lobby. As you stand in the centre of the lobby on floor -1 '
    'facing the stairs behind the sliding glass doors that lead up to the pool, look to the left '
    '- you\'ll see the seating area, and just past it a door to a small room perfect for storing '
    'luggage for as long as you need.\n\n'
    'There are also communal showers in the main lobby area if you\'d like to freshen up.\n\n'
    'You\'re welcome to take the beach towels with you for any last pool or beach time - just '
    'drop them off in the luggage storage room when you\'re finished with them.\n\n'
    'As you leave Quinta da Barracuda for the last time, please deposit the blue fob in post-box '
    'C01, on the left-hand side as you exit the front gate.'
)

CLUBE_DO_MONACO_INSTRUCTIONS = (
    'To get in and out of the building without keys, use the keypad on the right-hand side of '
    'the front entrance - the code is 9999.\n\n'
    'You\'re welcome to store your cases in the space behind the stairs on the ground floor, in '
    'the gap between the back of the stairs and the sofas just before apartment 8.\n\n'
    'The communal showers are on the same floor as the pool - head straight on rather than '
    'turning right to the pool as you come off the stairs on level -1.\n\n'
    'Feel free to take the beach towels from the apartment if they\'ll be useful. Once you\'re '
    'finished, just drop them on the table in the private room next to the vending machine on '
    'the ground floor - there\'s a sign that says "Private. No entry.", but don\'t worry about '
    'it, just open the door and place the towels on the coffee table in front of the sofa.'
)


def backfill(apps, schema_editor):
    Location = apps.get_model('properties', 'Location')
    Location.objects.filter(title='QUINTA DA BARRACUDA').update(
        after_checkout_access_instructions=QUINTA_DA_BARRACUDA_INSTRUCTIONS
    )
    Location.objects.filter(title='CLUBE DO MONACO').update(
        after_checkout_access_instructions=CLUBE_DO_MONACO_INSTRUCTIONS
    )

    # MON T does have a BBQ (the legacy system special-cased a cleaning note for it) - the
    # structured Amenity.barbecue flag was just never set. Correcting it here also fixes that
    # property's public Amenities listing, not just the new checkout-day note.
    Amenity = apps.get_model('properties', 'Amenity')
    Amenity.objects.filter(property__short_title='MON T').update(barbecue=True)


def unbackfill(apps, schema_editor):
    Location = apps.get_model('properties', 'Location')
    Location.objects.filter(title__in=['QUINTA DA BARRACUDA', 'CLUBE DO MONACO']).update(
        after_checkout_access_instructions=''
    )
    Amenity = apps.get_model('properties', 'Amenity')
    Amenity.objects.filter(property__short_title='MON T').update(barbecue=False)


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0056_location_after_checkout_access_instructions'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
