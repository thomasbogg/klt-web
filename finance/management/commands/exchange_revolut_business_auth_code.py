from django.core.management.base import BaseCommand

from libraries.banking.revolut_business import exchange_authorization_code_for_tokens


class Command(BaseCommand):
    """Run-once setup utility: exchanges the one-time authorization code (shown by klt-hooks'
    /revolut/business-oauth-callback after consenting to the application in the Revolut Business
    app) for the first access_token + refresh_token. Prints the refresh_token - paste it into
    REVOLUT_BUSINESS_API_REFRESH_TOKEN. Run this quickly after getting the code - it has a short
    validity window, same as any standard OAuth2 authorization code.

    Needs REVOLUT_BUSINESS_API_SIGNING_KEY/_CLIENT_ID already set (the client-assertion JWT this
    exchange requires is signed the same way as every ongoing token refresh - see
    libraries/banking/revolut_business.py::generate_client_assertion)."""
    help = "Exchange a Revolut Business API authorization code for the first refresh token."

    def add_arguments(self, parser):
        parser.add_argument('code', type=str, help="The authorization code from the OAuth consent redirect.")

    def handle(self, *args, **options):
        tokens = exchange_authorization_code_for_tokens(options['code'])
        if tokens is None:
            self.stderr.write(self.style.ERROR(
                "Token exchange failed - see the logged error above, or the code may have expired "
                "(go back through the consent step to get a fresh one)."
            ))
            return

        self.stdout.write(self.style.SUCCESS("Token exchange succeeded."))
        self.stdout.write(self.style.WARNING(
            f"refresh_token (does not expire per Revolut's docs - save this): {tokens.get('refresh_token')}"
        ))
        self.stdout.write(
            "Add it to REVOLUT_BUSINESS_API_REFRESH_TOKEN. The access_token in this response isn't "
            "needed - it's regenerated automatically on every call from the refresh_token."
        )
