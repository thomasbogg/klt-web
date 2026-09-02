from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from bookings.models import Booking
from bookings.utils import guest_for_owner
from guests.models import Guest

# One-off, per Thomas 2026-09-02: a second, differently-shaped legacy placeholder pattern from
# consolidate_owner_guest_records.py's 'Owner/Family' (first_name, singular) - here first_name is
# the property's own title (e.g. 'Quinta Da Barracuda - B22', in both original and all-caps PIMS
# casing) and last_name is 'Owners/Family' (plural), one row per owner-stay booking instead of one
# shared row. Found while investigating same-name Guest duplicates (2026-09-02): 22 rows across 5
# properties (Cerro Mar - 305, Quinta Da Barracuda B04/B11/B22/C01/D03), all 24 of their bookings
# already confirmed is_owner=True - so unlike the generic 'Owner/Family' placeholder, no filtering
# by booking type is needed here, every row in this pattern is a genuine owner stay.


class Command(BaseCommand):
    """Consolidates every '<property title> Owners/Family'-named Guest row onto the same canonical
    per-owner Guest that guest_for_owner() / consolidate_owner_guest_records.py already use for
    that property's owner - reassigns each row's (already-confirmed is_owner=True) bookings, then
    deletes the now-empty placeholder row. A property with no owner set is skipped and reported,
    not guessed at."""
    help = "One-off: consolidate '<property> Owners/Family' placeholder Guest rows onto each property's own owner Guest."

    def handle(self, *args, **options):
        with transaction.atomic():
            reassigned, deleted, skipped = self.consolidate_all()
            self.stdout.write(f"TOTAL: reassigned {reassigned} bookings, deleted {deleted} guest rows")
            for guest in skipped:
                self.stdout.write(self.style.WARNING(f"  SKIPPED (no owner set): guest #{guest.pk} {guest.first_name!r}"))

    def consolidate_all(self):
        placeholders = list(Guest.objects.filter(last_name__iexact='Owners/Family'))

        canonical_by_owner_id = {}
        bookings_to_update = []
        touched_guest_ids = set()
        skipped = []

        for guest in placeholders:
            bookings = list(Booking.objects.filter(guest=guest, is_owner=True).select_related('property__owner'))
            if not bookings:
                continue
            owner = bookings[0].property.owner
            if owner is None:
                skipped.append(guest)
                continue

            canonical = canonical_by_owner_id.get(owner.pk)
            if canonical is None:
                canonical = self._canonical_guest_for_owner(owner)
                canonical_by_owner_id[owner.pk] = canonical

            for booking in bookings:
                if booking.guest_id != canonical.pk:
                    touched_guest_ids.add(booking.guest_id)
                    booking.guest = canonical
                    bookings_to_update.append(booking)

        Booking.objects.bulk_update(bookings_to_update, ['guest'], batch_size=500)

        remaining_counts = dict(
            Booking.objects.filter(guest_id__in=touched_guest_ids)
            .values('guest_id').annotate(n=Count('id')).values_list('guest_id', 'n')
        )
        guest_ids_to_delete = [gid for gid in touched_guest_ids if remaining_counts.get(gid, 0) == 0]
        deleted_count, _ = Guest.objects.filter(pk__in=guest_ids_to_delete).delete()

        return len(bookings_to_update), deleted_count, skipped

    def _canonical_guest_for_owner(self, owner):
        canonical = guest_for_owner(owner)
        changed = False
        if owner.phone and canonical.phone != owner.phone:
            canonical.phone = owner.phone
            changed = True
        normalized_email = owner.email.strip().lower()
        if canonical.email != normalized_email:
            canonical.email = normalized_email
            changed = True
        if changed:
            canonical.save(update_fields=['phone', 'email'])
        return canonical
