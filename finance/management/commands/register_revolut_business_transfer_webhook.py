from django.core.management.base import BaseCommand

from libraries.banking.revolut_business import get_revolut_business_connection

WEBHOOK_URL = 'https://klt-hooks.up.railway.app/revolut/business-transfer-callback'
EVENTS = ['TransferStateChanged']  # confirm exact event name against Revolut Business API docs


class Command(BaseCommand):
    """Run-once setup utility: registers a Revolut Business API webhook subscription for owner
    payout transfer-state changes (finance/payouts_revolut.py), separate from the Merchant API
    subscriptions registered by bookings/management/commands/register_revolut_deposit_webhook.py.
    Prints the returned signing secret - paste it into klt-hooks'
    REVOLUT_BUSINESS_TRANSFER_WEBHOOK_SIGNING_KEY env var. Not invoked by the request/response
    cycle; run manually, once per environment (sandbox and production are separate Revolut
    environments with separate subscriptions - re-run this again when cutting over to production),
    then discard the printed secret.

    Unlike register_revolut_deposit_webhook.py (a static Merchant API secret key), this goes
    through get_revolut_business_connection() (OAuth2 refresh-token + JWT client-assertion) and
    must handle its None case explicitly - that's a real, expected failure mode
    (env_settings.REVOLUT_BUSINESS_API_* unset or expired) this product's auth flow has that the
    Merchant one doesn't."""
    help = "Register the Revolut Business API webhook subscription for owner payout transfers."

    def handle(self, *args, **options):
        connection = get_revolut_business_connection()
        if connection is None:
            self.stderr.write(self.style.ERROR(
                "Could not connect to Revolut Business API - check REVOLUT_BUSINESS_API_* env vars."
            ))
            return

        webhook = connection.webhook
        webhook.url = WEBHOOK_URL
        webhook.events = EVENTS
        webhook.create()

        if not webhook.id:
            self.stderr.write(self.style.ERROR(
                "Failed to create webhook - see the logged error above for details."
            ))
            return

        self.stdout.write(self.style.SUCCESS(f"Webhook created: id={webhook.id}, url={webhook.url}"))
        self.stdout.write(self.style.WARNING(
            f"Signing secret (shown once, save it now): {webhook.signingSecret}"
        ))
        self.stdout.write(
            "Add this to klt-hooks' .env / Railway variables as REVOLUT_BUSINESS_TRANSFER_WEBHOOK_SIGNING_KEY."
        )
