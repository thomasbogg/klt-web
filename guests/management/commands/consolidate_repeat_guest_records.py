from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from bookings.models import Booking
from guests.models import Guest

# One-off, per Thomas 2026-09-01: groups that share an (email, last_name) pair but carry more than
# one distinct first_name on record, where merging onto a single canonical Guest is still correct -
# mapped to the first_name we want that canonical row to keep. Any OTHER (email, last_name) group
# with more than one distinct first_name is left alone by this command - a judgement call only
# Thomas can make per group, not something to guess at automatically. Two different reasons land a
# group in here:
#   - Same person, typo/data-entry glitch: confirmed by an identical phone number on every row in
#     the group (Kearins, Monagle) - mapped to the correctly-spelled form.
#   - Same family sharing one contact, per Thomas: he confirmed Abbott/Wager/Burnett are each one
#     family who should be tracked as one guest identity for record-keeping, the same treatment
#     already applied to is_owner bookings in consolidate_owner_guest_records.py - mapped to
#     whichever family member already has the most bookings (the same choice the canonical-row
#     selection below would make on its own; named explicitly here only to mark the group eligible).
SAME_PERSON_OVERRIDES = {
    ('valerie@vkearinssolicitor.ie', 'Kearins'): 'Valerie',
    ('brian_49@hotmail.com', 'Monagle'): 'Brian',
    ('abbottcliffkatie75@gmail.com', 'Abbott'): 'Katie',
    ('bwager@bmts.com', 'Wager'): 'Brian',
    ('annehols@yahoo.co.uk', 'Burnett'): 'Anne',
}


class Command(BaseCommand):
    """Consolidates Guest rows that are almost certainly the same returning person: same email +
    same last_name, shared across more than one Guest row because every booking creates its own
    Guest rather than reusing an existing one. Repoints all of that person's bookings onto one
    canonical Guest row and deletes the now-empty duplicates.

    Only acts on a group when it's confident it's one person: either every row in the group already
    has the exact same first_name (the common case - a genuine repeat guest booked several times),
    or the group is in SAME_PERSON_OVERRIDES above (a hand-confirmed typo/glitch, e.g. 'Valeire' vs
    'Valerie' with the identical phone number on both rows). A group with more than one distinct
    first_name that ISN'T in the override list is left completely untouched - that shape usually
    means two different real people sharing one contact (a couple, a family), not a duplicate
    identity, and merging them would wrongly erase which of them actually stayed on which booking.

    Canonical row = whichever of the group's Guest rows has the most bookings already attached
    (tie-broken by lowest id, which only matters when bookings are tied - name is identical in that
    case for every group except the two SAME_PERSON_OVERRIDES entries, where the canonical row's
    first_name is then corrected to the override spelling regardless of which physical row won the
    tie-break). phone/preferred_language/country are backfilled onto the canonical row from a
    duplicate only where the canonical's own value is blank - never overwritten if already set.

    Batched deliberately (one bulk_update for reassigned bookings, one bulk_update for canonical
    guest edits, one grouped count query, one bulk delete) rather than a per-row loop - see
    consolidate_owner_guest_records.py's docstring for why that matters against this project's
    remote Postgres. bulk_update() bypasses signals, same as that command - confirmed nothing
    downstream reads Guest fields via a save signal."""
    help = "One-off: consolidate Guest rows that share an email + last_name and are confidently the same repeat guest onto one canonical Guest row."

    def handle(self, *args, **options):
        with transaction.atomic():
            reassigned, renamed, deleted, skipped = self.consolidate_all()
            self.stdout.write(
                f"TOTAL: reassigned {reassigned} bookings, renamed {renamed} canonical guests, "
                f"deleted {deleted} guest rows, skipped {len(skipped)} ambiguous groups"
            )
            for email, last_name, first_names in skipped:
                self.stdout.write(f"  SKIPPED (ambiguous): {email} | {last_name} | {sorted(first_names)}")

    def consolidate_all(self):
        groups = (
            Guest.objects.exclude(email='').exclude(email__isnull=True)
            .values('email', 'last_name').annotate(c=Count('id')).filter(c__gt=1)
        )

        bookings_to_update = []
        guests_to_rename = []
        touched_guest_ids = set()
        skipped = []
        renamed_count = 0

        for group in groups:
            email, last_name = group['email'], group['last_name']
            guests = list(
                Guest.objects.filter(email=email, last_name=last_name)
                .annotate(booking_count=Count('booking'))
                .order_by('-booking_count', 'id')
            )
            first_names = {g.first_name.strip() for g in guests}

            override_key = (email, last_name)
            if len(first_names) == 1:
                canonical_first_name = next(iter(first_names))
            elif override_key in SAME_PERSON_OVERRIDES:
                canonical_first_name = SAME_PERSON_OVERRIDES[override_key]
            else:
                skipped.append((email, last_name, first_names))
                continue

            canonical = guests[0]
            duplicates = guests[1:]
            needs_save = False

            if canonical.first_name != canonical_first_name:
                canonical.first_name = canonical_first_name
                renamed_count += 1
                needs_save = True

            for dup in duplicates:
                if not canonical.phone and dup.phone:
                    canonical.phone = dup.phone
                    needs_save = True
                if not canonical.preferred_language and dup.preferred_language:
                    canonical.preferred_language = dup.preferred_language
                    needs_save = True
                if not canonical.country and dup.country:
                    canonical.country = dup.country
                    needs_save = True

            if needs_save:
                guests_to_rename.append(canonical)

            for dup in duplicates:
                touched_guest_ids.add(dup.pk)
                for booking in dup.booking_set.all():
                    booking.guest = canonical
                    bookings_to_update.append(booking)

        Booking.objects.bulk_update(bookings_to_update, ['guest'], batch_size=500)
        Guest.objects.bulk_update(
            guests_to_rename, ['first_name', 'phone', 'preferred_language', 'country'], batch_size=500,
        )

        remaining_counts = dict(
            Booking.objects.filter(guest_id__in=touched_guest_ids)
            .values('guest_id').annotate(n=Count('id')).values_list('guest_id', 'n')
        )
        guest_ids_to_delete = [gid for gid in touched_guest_ids if remaining_counts.get(gid, 0) == 0]
        deleted_count, _ = Guest.objects.filter(pk__in=guest_ids_to_delete).delete()

        return len(bookings_to_update), renamed_count, deleted_count, skipped
