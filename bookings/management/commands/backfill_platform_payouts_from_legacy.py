from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

import env_settings
from bookings.models import Booking, PlatformPayout

# Below this, Charge.basic_rental is legacy junk rather than a real rental figure - confirmed
# against klt_main.db: exactly 2 legacy charges rows (Vrbo bookings, PIMSId 3690/5438) carry
# basicRental=1.0 with a platformFee in the 95-97 range, which is what skewed a naive
# platformFee/basicRental average for Vrbo up to ~83% even though the real median across the other
# 251 Vrbo rows is ~6.3% (in line with Vrbo's actual host-fee rate) - every other platform has zero
# rows below this floor. Booking's below it are skipped and reported rather than backfilled with an
# obviously-wrong payout_amount=1.00.
MIN_PLAUSIBLE_BASIC_RENTAL = Decimal('10.00')


class Command(BaseCommand):
    """One-off, per Thomas 2026-09-01: PlatformPayout (gross/commission/payout) was built as a
    manual-entry model going forward, but nothing ever backfilled it from the equivalent legacy
    data that already came across the klt_main.db migration onto Charge.basic_rental/platform_fee
    (see bookings/models.py::Charge - those two fields are legacy-only now that PlatformPayout
    replaced them). Confirmed via klt_main.db's own charges table: basicRental is literally what
    was recorded as the platform payout figure (used for both platform and non-platform bookings'
    pre-admin-fee rental price), platformFee is populated only for platform bookings and is exactly
    what the platform kept as commission - so payout_amount=basic_rental and
    platform_commission=platform_fee is a direct, not inferred, mapping. gross_amount is then
    derived as basic_rental + platform_fee (the model's own gross-commission=payout relationship)
    wherever platform_fee is actually present; where a booking never had a platform_fee entered in
    legacy (176 of 1,944), platform_commission/gross_amount are left null rather than implying a
    known $0 commission - only payout_amount is backfilled for those.

    Only touches bookings with enquiry_source in env_settings.PLATFORMS that don't already have a
    PlatformPayout row (idempotent/safe to rerun - a booking someone's already entered payout
    figures for by hand is never touched) and whose Charge.basic_rental is at least
    MIN_PLAUSIBLE_BASIC_RENTAL; anything below that floor is skipped and reported by reference
    instead of writing an obviously-wrong figure.

    payout_date is never set - legacy has no equivalent field, so it's left for manual entry same
    as any newly-created booking today.

    Bulk-created in one query rather than a per-booking loop, per this project's established
    pattern for one-off data scripts against the remote Postgres (see e.g.
    consolidate_owner_guest_records.py's docstring for why)."""
    help = "One-off: backfill PlatformPayout rows from legacy Charge.basic_rental/platform_fee for platform bookings that don't have one yet."

    def handle(self, *args, **options):
        with transaction.atomic():
            created, skipped = self.backfill_all()
            self.stdout.write(f"TOTAL: created {created} PlatformPayout rows, skipped {len(skipped)} implausible rows")
            for ref, basic_rental in skipped:
                self.stdout.write(f"  SKIPPED (basic_rental={basic_rental}): {ref}")

    def backfill_all(self):
        candidates = (
            Booking.objects.filter(enquiry_source__in=env_settings.PLATFORMS, platform_payout__isnull=True)
            .select_related('charges')
        )

        payouts_to_create = []
        skipped = []

        for booking in candidates:
            charge = getattr(booking, 'charges', None)
            if charge is None or not charge.basic_rental:
                continue

            if charge.basic_rental < MIN_PLAUSIBLE_BASIC_RENTAL:
                skipped.append((booking.reference, charge.basic_rental))
                continue

            payout_amount = charge.basic_rental
            platform_commission = None
            gross_amount = None
            if charge.platform_fee:
                platform_commission = charge.platform_fee
                gross_amount = charge.basic_rental + charge.platform_fee

            payouts_to_create.append(PlatformPayout(
                booking=booking,
                gross_amount=gross_amount,
                platform_commission=platform_commission,
                payout_amount=payout_amount,
                payout_currency=charge.currency or None,
            ))

        PlatformPayout.objects.bulk_create(payouts_to_create, batch_size=500)
        return len(payouts_to_create), skipped
