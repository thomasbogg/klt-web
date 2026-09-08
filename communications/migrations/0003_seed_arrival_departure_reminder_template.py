from django.db import migrations

# Slug -> DEFAULT_OFFSET_DAYS in communications/registry.py must stay in sync with this list - see
# 0002_seed_email_templates.py's own comment for why the split exists.
TEMPLATES = [
    {
        'slug': 'arrival_departure_reminder',
        'name': 'Arrival & departure details reminder',
        'audience': 'guest',
        'offset_days': -14,
        'subject': 'Your travel plans for {{ property_name }} - {{ reference }}',
        'body': (
            "We still don't have your travel plans for your upcoming stay at {{ property_name }}, "
            'arriving {{ arrival_date }}.\n\n'
            "Please let us know how you're getting to and from the property here: "
            '{{ manage_arrival_departure_url }}\n\n'
            "This helps us plan your check-in properly, so please try to let us know soon."
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
        ('communications', '0002_seed_email_templates'),
    ]

    operations = [
        migrations.RunPython(seed_email_templates, remove_seeded_email_templates),
    ]
