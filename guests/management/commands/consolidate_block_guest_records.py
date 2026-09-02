from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from bookings.models import Booking
from guests.models import Guest

# One-off, per Thomas 2026-09-02: PIMS-migrated calendar-block bookings (an owner/admin marking a
# property unbookable, or holding a late check-out) each got their own Guest row rather than
# sharing one, cluttering the staff Guests view with dozens of identical placeholder rows. Two
# groups, matched case-insensitively since legacy data has an inconsistent 'Block - Unbookable'
# casing alongside the more common 'BLOCK - Unbookable' - both collapse onto the same canonical
# last_name. Left alone deliberately: 'Blocked', 'Block For Stephen Chase', 'Block For Bruce
# Wright', and 'Legacy Block' (paired with a numeric last_name, presumably an old booking/PIMS id)
# - none of those are the two categories Thomas named (unbookable-period / late check-out), and
# the owner-named ones look like they identify a specific block, not an interchangeable
# placeholder.
BLOCK_GROUPS = {
    'BLOCK - Unbookable': ['BLOCK - Unbookable', 'Block - Unbookable'],
    'BLOCK - Late Check-out': ['BLOCK - Late Check-out'],
}


class Command(BaseCommand):
    """Consolidates the 'BLOCK - Unbookable' and 'BLOCK - Late Check-out' placeholder Guest rows
    (one canonical row per category) - see BLOCK_GROUPS above. Canonical row = lowest id among the
    group (all rows in a group are identical placeholders, so tie-breaking is arbitrary and
    harmless); its last_name is normalized to the group's canonical spelling regardless of which
    row was picked. Repoints every Booking off a duplicate onto the canonical row, then deletes the
    now-unreferenced duplicates. Booking.guest is the only FK onto Guest in the codebase (confirmed
    2026-09-02), so nothing else needs repointing."""
    help = "One-off: consolidate 'BLOCK - Unbookable' and 'BLOCK - Late Check-out' placeholder Guest rows onto one canonical row each."

    def handle(self, *args, **options):
        with transaction.atomic():
            for canonical_name, variants in BLOCK_GROUPS.items():
                self.consolidate_group(canonical_name, variants)

    def consolidate_group(self, canonical_name, variants):
        guests = list(
            Guest.objects.filter(Q(last_name__in=variants))
            .annotate(booking_count=Count('booking')).order_by('id')
        )
        if len(guests) <= 1:
            self.stdout.write(f"{canonical_name}: nothing to do ({len(guests)} row(s)).")
            return

        canonical = guests[0]
        duplicates = guests[1:]

        if canonical.last_name != canonical_name or canonical.first_name is not None:
            canonical.last_name = canonical_name
            canonical.first_name = None
            canonical.save(update_fields=['last_name', 'first_name'])

        reassigned = Booking.objects.filter(guest_id__in=[d.pk for d in duplicates]).update(guest=canonical)
        deleted_count, _ = Guest.objects.filter(pk__in=[d.pk for d in duplicates]).delete()

        self.stdout.write(self.style.SUCCESS(
            f"{canonical_name}: kept guest #{canonical.pk}, reassigned {reassigned} booking(s), "
            f"deleted {deleted_count} duplicate guest row(s)."
        ))
