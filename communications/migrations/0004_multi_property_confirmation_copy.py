from django.db import migrations

# Adds the multi-property paragraph to guest_booking_confirmation's seeded body (see
# communications/registry.py::_guest_context's new is_multi_property/other_property_names -
# 2026-09-14, multi-property booking Stage 4). Only touches the row if its body still exactly
# matches what 0002_seed_email_templates.py originally seeded - if Thomas has already edited this
# template via Settings > Emails, this migration leaves it alone rather than clobbering his own
# copy. owner_booking_confirmation is deliberately NOT touched here: telling one owner that their
# guest is also staying at a different (possibly unrelated) owner's property isn't this migration's
# call to make by default - the context variables are available if that's ever wanted.

ORIGINAL_GUEST_BODY = (
    'Your stay at {{ property_name }} from {{ arrival_date }} to {{ departure_date }} is confirmed.\n\n'
    '{% if amount_due_now %}Amount due now: {{ amount_due_now }} {{ amount_due_now_currency }}.{% endif %}\n'
    '{% if amount_due_balance %}A balance of {{ amount_due_balance }} {{ amount_due_balance_currency }} '
    'is due by {{ balance_due_date }}.{% endif %}\n\n'
    'You can view and manage your booking at any time here: {{ manage_hub_url }}\n\n'
    'Your booking reference is {{ reference }} - please keep this safe.'
)

NEW_GUEST_BODY = (
    'Your stay at {{ property_name }} from {{ arrival_date }} to {{ departure_date }} is confirmed.\n\n'
    '{% if is_multi_property %}This booking is linked to your other stay at {{ other_property_names }} - '
    'both apartments share this same reference.{% endif %}\n'
    '{% if amount_due_now %}Amount due now: {{ amount_due_now }} {{ amount_due_now_currency }}.{% endif %}\n'
    '{% if amount_due_balance %}A balance of {{ amount_due_balance }} {{ amount_due_balance_currency }} '
    'is due by {{ balance_due_date }}.{% endif %}\n\n'
    'You can view and manage your booking at any time here: {{ manage_hub_url }}\n\n'
    'Your booking reference is {{ reference }} - please keep this safe.'
)


def add_multi_property_copy(apps, schema_editor):
    EmailTemplate = apps.get_model('communications', 'EmailTemplate')
    EmailTemplate.objects.filter(
        slug='guest_booking_confirmation', body=ORIGINAL_GUEST_BODY,
    ).update(body=NEW_GUEST_BODY)


def remove_multi_property_copy(apps, schema_editor):
    EmailTemplate = apps.get_model('communications', 'EmailTemplate')
    EmailTemplate.objects.filter(
        slug='guest_booking_confirmation', body=NEW_GUEST_BODY,
    ).update(body=ORIGINAL_GUEST_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0003_seed_arrival_departure_reminder_template'),
    ]

    operations = [
        migrations.RunPython(add_multi_property_copy, remove_multi_property_copy),
    ]
