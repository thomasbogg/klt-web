from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from bookings.models import Booking
from bookings.utils import guest_for_owner
from guests.models import Guest
from properties.models import Owner, Property

OWNER_FAMILY_PLACEHOLDER = 'owner/family'


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-01: for each Owner, finds every is_owner=True Booking at that
    owner's own properties whose Guest's name either matches the owner's own name (first+last, or
    last_name alone - matches how guest_for_owner() stores a whole company-style name in
    last_name) OR is the generic 'Owner/Family' placeholder (109 rows portfolio-wide, a legacy
    PIMS label used across every property/owner - never one identifiable person, so it's matched
    per-property here rather than being resolvable to a single canonical guest the way an exact
    name match is), and repoints those bookings onto one canonical Guest row per owner - the same
    one guest_for_owner() already uses for the Owner Suite's own self-service booking flow - with
    that Guest's email/phone set to the owner's own contact details, regardless of what contact
    info the original guest row happened to carry (per Thomas: intentional - these are all the
    same owner-stay identity for record-keeping purposes even where a family member/friend's own
    contact details got typed in for a particular visit).

    Deliberately excludes any 'Owner/Family'-named booking that ISN'T is_owner=True (~23 of the
    109 - looks like a real, possibly-paying guest that inherited a leftover placeholder name, not
    an owner stay at all) - those are left untouched for separate review.

    Deliberately scoped to ONLY the matched is_owner bookings themselves - a matched guest row's
    OTHER, non-owner bookings (if any) are left exactly as they are, still attributed to that
    original guest row. A guest row is only deleted once every one of its bookings has been
    reassigned away (i.e. it had no bookings beyond the owner-stay ones just consolidated) -
    Booking.guest is PROTECT, so a guest row that still has an unrelated booking attached is never
    included in the bulk delete.

    Batched deliberately (one bulk_update for every reassigned booking, one grouped count query,
    one bulk delete) rather than looping with a per-booking/per-guest .save()/.get()/.delete() -
    the original per-owner, per-row version worked but took 45+ minutes against this project's
    remote Railway Postgres, almost entirely round-trip latency rather than actual work; this
    version does the same thing in a handful of queries total. bulk_update() bypasses Django
    signals, which is fine here - staff/signals.py's post_save receiver on Booking never reads
    guest at all (confirmed against its own source), so nothing downstream depends on this field
    changing via .save()."""
    help = "One-off: consolidate is_owner bookings whose guest name matches the property owner's name (or the generic 'Owner/Family' placeholder) onto one canonical Guest per owner."

    def handle(self, *args, **options):
        with transaction.atomic():
            reassigned, deleted = self.consolidate_all()
            self.stdout.write(f"TOTAL: reassigned {reassigned} bookings, deleted {deleted} guest rows")

    def consolidate_all(self):
        properties = list(Property.objects.filter(owner__isnull=False).select_related('owner'))
        owner_by_property_id = {p.pk: p.owner for p in properties}

        candidate_bookings = list(
            Booking.objects.filter(is_owner=True, property_id__in=owner_by_property_id.keys())
            .select_related('guest')
        )

        canonical_by_owner_id = {}
        bookings_to_update = []
        touched_guest_ids = set()

        for booking in candidate_bookings:
            owner = owner_by_property_id.get(booking.property_id)
            if owner is None or not self._guest_matches_owner(booking.guest, owner):
                continue

            canonical = canonical_by_owner_id.get(owner.pk)
            if canonical is None:
                canonical = self._canonical_guest_for_owner(owner)
                canonical_by_owner_id[owner.pk] = canonical

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

        return len(bookings_to_update), deleted_count

    def _guest_matches_owner(self, guest, owner):
        name_norm = owner.name.strip().lower()
        full = f"{(guest.first_name or '').strip()} {guest.last_name.strip()}".strip().lower()
        return (
            full == name_norm
            or guest.last_name.strip().lower() == name_norm
            or (guest.first_name or '').strip().lower() == OWNER_FAMILY_PLACEHOLDER
        )

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
