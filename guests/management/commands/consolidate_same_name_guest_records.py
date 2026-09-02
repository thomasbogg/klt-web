from django.core.management.base import BaseCommand
from django.db import transaction

from bookings.models import Booking
from guests.models import Guest

# One-off, per Thomas 2026-09-02: found while auditing "same first+last name repeated more than
# twice" (23 groups). Unlike consolidate_repeat_guest_records.py (keyed on matching email +
# last_name, so it only ever catches a group that already agrees on email), every group here has a
# *different* email (or none) per row - that's exactly why it slipped past that tool - so each one
# was reviewed by hand against phone/email/property/date instead. Only a group with a real
# corroborating signal (a phone or email that matches across rows once formatting/case is ignored,
# or an unmistakable same-property-same-calendar-date year-after-year pattern) is merged here.
#
# Extended 2026-09-02 per Thomas's explicit call, after seeing the reasoning above: also merge
# Anne Neeson, James Martin, Somendra Khosla, Mieke Reece, Stephen Obrien, Ronny Bukasa, and Kaksha
# Babla - each of these groups' own per-row detail is exactly as described above (still true, still
# the reason no *automatic* signal caught them); Thomas has his own knowledge of who these actually
# are and asked for them merged anyway. Mieke Reece gets 'backfill': False since its two duplicate
# rows' emails visibly belong to two different unrelated people (Mike Sibson, a cogeco.ca address) -
# safe to fold their (zero) bookings in, but wrong to let either email become "Mieke Reece"'s
# contact info. Anne Neeson's duplicates are deliberately ordered so the same-surname 's_neeson'
# row is checked for backfill before the differently-named 'natashaneeson.com' one.
#
# Left completely untouched, still - each for its own reason:
#   David Lynch    - two rows carry two different phone numbers; a common name, no other signal.
#   Margarida Casquinha - property differs between rows, and no contact info on any of them.
#   Kate-lynn Vaughan - same name, same day, but a different property per row and zero contact info
#                    to confirm one identity either way (Thomas chose not to merge this one).
MERGE_GROUPS = [
    {
        'name': 'Jayne Toye', 'canonical': 589, 'duplicates': [557, 565, 587],
        'reason': "clustered dates (6-15 Sep 2023), same property; only #589 carries real contact info",
    },
    {
        'name': 'Catia Rodrigues', 'canonical': 4197, 'duplicates': [3245, 3519],
        'reason': "same phone number on all three rows (formatting differs)",
    },
    {
        'name': 'Juliette Maytham', 'canonical': 2069, 'duplicates': [500, 3256],
        'reason': "same phone number, and matching email (one row has a typo'd domain)",
    },
    {
        'name': 'Stephen Palmer', 'canonical': 2833, 'duplicates': [1149, 3610],
        'reason': "email matches #1149 (case difference only); phone matches #3610 (formatting differs)",
    },
    {
        'name': 'Debbi Adams', 'canonical': 3932, 'duplicates': [322, 2785],
        'reason': "same property, same calendar date (Jan 31) three years running",
    },
    {
        'name': 'Robert Kane', 'canonical': 1361, 'duplicates': [2700, 3692],
        'reason': "same property, consecutive annual January bookings; #3692 has no bookings but a plausible matching email",
    },
    {
        'name': 'Mark Obermaier', 'canonical': 764, 'duplicates': [2703, 3014],
        'reason': "phone matches #2703; #3014's .ca email matches the other rows' Canadian phone number",
    },
    {
        'name': 'Philip Murphy', 'canonical': 2981, 'duplicates': [160, 2978],
        'reason': "same phone number on all three rows",
    },
    {
        'name': 'Vera Bogalho', 'canonical': 2345, 'duplicates': [3629, 4816],
        'reason': "same property, same calendar date (Sep 5/6) three years running",
    },
    {
        'name': 'Colin Budge', 'canonical': 3900, 'duplicates': [1422, 2831],
        'reason': "same property, annual pattern; canonical is the most recent (2026) contact info",
    },
    {
        'name': 'Anne Neeson', 'canonical': 1085, 'duplicates': [4560, 4563, 4318],
        'reason': "merged per Thomas's explicit call; canonical (#1085, 12 bookings) already has contact info so nothing gets backfilled from the other, differently-contactable rows",
    },
    {
        'name': 'James Martin', 'canonical': 3945, 'duplicates': [3946, 3949],
        'reason': "merged per Thomas's explicit call; canonical (#3945, 4 bookings) already has contact info so the other rows' own emails are not attached to it",
    },
    {
        'name': 'Somendra Khosla', 'canonical': 856, 'duplicates': [3821, 4384],
        'reason': "merged per Thomas's explicit call; canonical (#856, 33 bookings) already has contact info",
    },
    {
        'name': 'Mieke Reece', 'canonical': 3779, 'duplicates': [4349, 4388], 'backfill': False,
        'reason': "merged per Thomas's explicit call; backfill disabled since both duplicates' emails visibly belong to different, unrelated people",
    },
    {
        'name': 'Stephen Obrien', 'canonical': 4040, 'duplicates': [4050, 4340],
        'reason': "merged per Thomas's explicit call; no contact info on any row to backfill",
    },
    {
        'name': 'Ronny Bukasa', 'canonical': 1660, 'duplicates': [1661, 1662, 1663],
        'reason': "merged per Thomas's explicit call; no contact info on any row to backfill",
    },
    {
        'name': 'Kaksha Babla', 'canonical': 1550, 'duplicates': [1551, 1552],
        'reason': "merged per Thomas's explicit call; no contact info on any row to backfill",
    },
]

BACKFILL_FIELDS = ('email', 'phone', 'country', 'preferred_language')


class Command(BaseCommand):
    """Consolidates the hand-reviewed MERGE_GROUPS above onto their chosen canonical Guest row -
    reassigns every duplicate's bookings, backfills any blank canonical field from a duplicate in
    the listed order (never overwrites a value the canonical already has, and skipped entirely for
    a group with 'backfill': False), then deletes the now-empty duplicates."""
    help = "One-off: consolidate hand-reviewed same-name Guest duplicate groups onto one canonical row each."

    def handle(self, *args, **options):
        with transaction.atomic():
            total_reassigned = 0
            total_deleted = 0
            for group in MERGE_GROUPS:
                reassigned, deleted = self.consolidate_group(group)
                total_reassigned += reassigned
                total_deleted += deleted
                self.stdout.write(self.style.SUCCESS(
                    f"{group['name']}: kept guest #{group['canonical']}, reassigned {reassigned} booking(s), "
                    f"deleted {deleted} duplicate guest row(s)."
                ))
            self.stdout.write(f"TOTAL: reassigned {total_reassigned} bookings, deleted {total_deleted} guest rows")

    def consolidate_group(self, group):
        canonical = Guest.objects.get(pk=group['canonical'])
        guests_by_id = Guest.objects.in_bulk(group['duplicates'])
        duplicates = [guests_by_id[pk] for pk in group['duplicates']]  # preserve listed order

        needs_save = False
        if group.get('backfill', True):
            for field in BACKFILL_FIELDS:
                if getattr(canonical, field):
                    continue
                for dup in duplicates:
                    value = getattr(dup, field)
                    if value:
                        setattr(canonical, field, value)
                        needs_save = True
                        break
        if needs_save:
            canonical.save(update_fields=list(BACKFILL_FIELDS))

        # Every duplicate's entire booking set (not a filtered subset) is being moved, so each one
        # is guaranteed to have zero bookings left - safe to delete unconditionally, unlike
        # consolidate_owner_guest_records.py/consolidate_block_guest_records.py which only
        # reassign a filtered subset and so must check what's left before deleting.
        reassigned = Booking.objects.filter(guest_id__in=group['duplicates']).update(guest=canonical)
        deleted_count, _ = Guest.objects.filter(pk__in=group['duplicates']).delete()

        return reassigned, deleted_count
