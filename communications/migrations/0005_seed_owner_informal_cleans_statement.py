from django.db import migrations

# Slug -> DEFAULT_OFFSET_DAYS in communications/registry.py must stay in sync with this list - see
# 0002_seed_email_templates.py's own comment for why the split exists. offset_days is meaningless
# here (this is an ad-hoc send, not a ScheduledEmail - see finance/services.py::
# send_owner_statement's own docstring) but the field is non-nullable, so 0 like every other
# event-triggered template.
TEMPLATES = [
    {
        'slug': 'owner_informal_cleans_statement',
        'name': 'Owner informal cleans/meet-greet statement',
        'audience': 'owner',
        'offset_days': 0,
        'subject': 'Your cleans/meet-greet statement',
        'body': (
            'Hi {{ owner_name }},\n\n'
            "Here's your current outstanding cleans/meet-greet balance:\n\n"
            '{{ line_items }}\n\n'
            'Total: €{{ total }}\n\n'
            '{% if wise_payment_link %}You can pay this here: {{ wise_payment_link }}{% endif %}'
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
        ('communications', '0004_multi_property_confirmation_copy'),
    ]

    operations = [
        migrations.RunPython(seed_email_templates, remove_seeded_email_templates),
    ]
