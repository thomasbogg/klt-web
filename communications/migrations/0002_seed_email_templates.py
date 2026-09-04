from django.db import migrations

# Slug -> DEFAULT_OFFSET_DAYS in communications/registry.py must stay in sync with this list - the
# registry is what actually gives each slug meaning (anchor/eligibility/context), this migration
# only seeds the staff-editable starting copy so the feature is usable without a code deploy for
# every future edit. Real prose, not placeholder lorem ipsum - staff can (and should) rewrite this
# to match their own voice via Settings > Emails once it ships.
TEMPLATES = [
    {
        'slug': 'guest_booking_confirmation',
        'name': 'Guest booking confirmation',
        'audience': 'guest',
        'offset_days': 0,
        'subject': 'Your booking at {{ property_name }} is confirmed - {{ reference }}',
        'body': (
            'Your stay at {{ property_name }} from {{ arrival_date }} to {{ departure_date }} is confirmed.\n\n'
            '{% if amount_due_now %}Amount due now: {{ amount_due_now }} {{ amount_due_now_currency }}.{% endif %}\n'
            '{% if amount_due_balance %}A balance of {{ amount_due_balance }} {{ amount_due_balance_currency }} '
            'is due by {{ balance_due_date }}.{% endif %}\n\n'
            'You can view and manage your booking at any time here: {{ manage_hub_url }}\n\n'
            'Your booking reference is {{ reference }} - please keep this safe.'
        ),
    },
    {
        'slug': 'owner_booking_confirmation',
        'name': 'Owner booking confirmation',
        'audience': 'owner',
        'offset_days': 0,
        'subject': 'New booking at {{ property_name }} - {{ arrival_date }} to {{ departure_date }}',
        'body': (
            'A new booking has been made at {{ property_name }} for {{ guest_full_name }}, arriving '
            '{{ arrival_date }} and departing {{ departure_date }}.\n\nBooking reference: {{ reference }}.'
        ),
    },
    {
        'slug': 'deposit_payment_received',
        'name': 'Deposit payment received',
        'audience': 'guest',
        'offset_days': 0,
        'subject': 'Payment received - your booking {{ reference }} is confirmed',
        'body': (
            "We've received your payment of {{ amount_due_now }} {{ amount_due_now_currency }} for your stay "
            'at {{ property_name }}. Your booking is now confirmed.\n\n'
            'You can view your booking details here: {{ manage_hub_url }}'
        ),
    },
    {
        'slug': 'balance_payment_received',
        'name': 'Balance payment received',
        'audience': 'guest',
        'offset_days': 0,
        'subject': 'Balance payment received - {{ reference }}',
        'body': (
            "Thank you - we've received your balance payment in full for your stay at {{ property_name }}. "
            "There's nothing more to pay before your arrival on {{ arrival_date }}.\n\n"
            'View your booking here: {{ manage_hub_url }}'
        ),
    },
    {
        'slug': 'hold_expiry_warning_wise',
        'name': 'Reservation hold expiring soon',
        'audience': 'guest',
        'offset_days': -1,
        'subject': 'Action needed - your reservation at {{ property_name }} is on hold',
        'body': (
            'Your reservation at {{ property_name }} ({{ reference }}) is being held for you, but your '
            'payment window is closing soon.\n\n'
            'To secure your dates, please complete payment as soon as possible: {{ pay_url }}\n\n'
            "If we don't receive your payment in time, the dates may become available to other guests."
        ),
    },
    {
        'slug': 'balance_payment_reminder',
        'name': 'Balance payment reminder',
        'audience': 'guest',
        'offset_days': -63,
        'subject': 'Balance payment due soon for your stay at {{ property_name }}',
        'body': (
            'Your balance of {{ amount_due_balance }} {{ amount_due_balance_currency }} for your upcoming '
            'stay at {{ property_name }} is due by {{ balance_due_date }}.\n\n'
            'Please complete your payment here: {{ balance_details_url }}'
        ),
    },
    {
        'slug': 'security_deposit_request',
        'name': 'Security deposit bank details request',
        'audience': 'guest',
        'offset_days': -14,
        'subject': 'Security deposit - bank details needed for your stay at {{ property_name }}',
        'body': (
            "Ahead of your stay at {{ property_name }}, we'll need your bank details so we can return your "
            'security deposit after check-out.\n\n'
            'Please provide them here: {{ manage_deposit_url }}'
        ),
    },
    {
        'slug': 'guest_registration_reminder',
        'name': 'Guest registration reminder',
        'audience': 'guest',
        'offset_days': -10,
        'subject': 'Guest registration needed for your stay at {{ property_name }}',
        'body': (
            'Portuguese law requires us to register each guest staying at {{ property_name }}.\n\n'
            'Please complete your guest registration details here: {{ manage_guest_registrations_url }}'
        ),
    },
]


def seed_email_templates(apps, schema_editor):
    EmailTemplate = apps.get_model('communications', 'EmailTemplate')
    for entry in TEMPLATES:
        EmailTemplate.objects.get_or_create(slug=entry['slug'], defaults=entry)


def remove_seeded_email_templates(apps, schema_editor):
    EmailTemplate = apps.get_model('communications', 'EmailTemplate')
    EmailTemplate.objects.filter(slug__in=[entry['slug'] for entry in TEMPLATES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_email_templates, remove_seeded_email_templates),
    ]
