from django.core.management.base import BaseCommand

import env_settings
from libraries.banking.revolut import Revolut

WEBHOOK_URL = 'https://klt-hooks.up.railway.app/revolut/booking-deposit-callback'
EVENTS = [
    'ORDER_PAYMENT_AUTHORISATION_STARTED',
    'ORDER_PAYMENT_AUTHENTICATION_CHALLENGED',
    'ORDER_PAYMENT_AUTHENTICATED',
    'ORDER_COMPLETED',
    'ORDER_PAYMENT_DECLINED',
    'ORDER_PAYMENT_FAILED',
    'ORDER_CANCELLED',
]


class Command(BaseCommand):
    """Run-once setup utility: registers a new Revolut Merchant API webhook subscription for the
    booking-deposit flow, separate from the existing tourist-tax subscription. Prints the returned
    signing secret - paste it into klt-hooks' REVOLUT_BOOKING_DEPOSIT_WEBHOOK_SIGNING_KEY env var.
    Not invoked by the request/response cycle; run manually, once, then discard the printed secret."""
    help = "Register the Revolut webhook subscription for booking deposit payments."

    def handle(self, *args, **options):
        webhook = Revolut(secretKey=env_settings.REVOLUT_API_SECRET_KEY).webhook
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
            "Add this to klt-hooks' .env / Railway variables as REVOLUT_BOOKING_DEPOSIT_WEBHOOK_SIGNING_KEY."
        )
