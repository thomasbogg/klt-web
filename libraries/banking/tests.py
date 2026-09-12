"""Regression tests for the 5 bugs found and fixed in revolut_business.py (2026-09-12) - this
module has no dedicated app of its own (not in INSTALLED_APPS), same as its sibling revolut.py,
whose own call sites are tested inline in bookings/tests.py instead. revolut_business.py has no
call site there yet (see finance/payouts_revolut.py for the real caller, tested via mocking at
get_revolut_business_connection in finance/tests.py) - these tests cover the class's own object
behavior directly instead, run via `manage.py test libraries.banking.tests`."""
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import SimpleTestCase

from libraries.banking.revolut_business import (
    RevolutBusiness, exchange_authorization_code_for_tokens, generate_client_assertion,
    get_access_token_for_revolut_business_api,
)


class GetOverrideBugfixTests(SimpleTestCase):
    """Webhook/Transfer/Counterparty didn't override _get() and inherited Object._get()
    (libraries/utils.py), which raises KeyError on any unset key - contradicting every
    `-> str | None` type hint on these classes' properties."""

    def test_webhook_url_returns_none_not_keyerror_when_unset(self):
        webhook = RevolutBusiness.Webhook()
        self.assertIsNone(webhook.url)

    def test_transfer_state_returns_none_not_keyerror_when_unset(self):
        transfer = RevolutBusiness.Transfer()
        self.assertIsNone(transfer.state)

    def test_counterparty_revtag_returns_none_not_keyerror_when_unset(self):
        counterparty = RevolutBusiness.Counterparty()
        self.assertIsNone(counterparty.revtag)


class CounterpartyCreatePayloadBugfixTests(SimpleTestCase):
    """Counterparty.create()'s payload set both 'company_name' and 'individual_name' to
    self._values.get('company', None) - a copy-paste bug that ignored the individualName setter's
    own {first_name, last_name} dict entirely. Also called self._get('address').get() directly
    instead of self.address (the lazy property), which KeyErrors on a fresh instance that never
    had .address touched."""

    @patch('libraries.banking.revolut_business.requests.post')
    def test_individual_name_payload_uses_the_individual_name_setters_dict(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'id': 'cp-1'}

        counterparty = RevolutBusiness.Counterparty(accessToken='token')
        counterparty.individualName = 'Jane Doe'
        counterparty.create()

        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['individual_name'], {'first_name': 'Jane', 'last_name': 'Doe'})

    @patch('libraries.banking.revolut_business.requests.post')
    def test_create_does_not_raise_on_a_fresh_counterparty_with_no_address_touched(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'id': 'cp-2'}

        counterparty = RevolutBusiness.Counterparty(accessToken='token')
        counterparty.individualName = 'Jane Doe'
        counterparty.create()  # must not raise KeyError

        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['address'], {})


class ApiVersionHeaderBugfixTests(SimpleTestCase):
    """REVOLUT_BUSINESS_API_VERSION was defined in env_settings but never actually sent - every
    Business API call should carry a dated Revolut-Api-Version header."""

    @patch('libraries.banking.revolut_business.env_settings')
    @patch('libraries.banking.revolut_business.requests.post')
    def test_api_version_header_is_sent_when_configured(self, mock_post, mock_env_settings):
        mock_env_settings.REVOLUT_BUSINESS_API_VERSION = '2024-09-01'
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'id': 'cp-3'}

        counterparty = RevolutBusiness.Counterparty(accessToken='token')
        counterparty.individualName = 'Jane Doe'
        counterparty.create()

        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['headers']['Revolut-Api-Version'], '2024-09-01')


class ClientAssertionJwtTests(SimpleTestCase):
    """generate_client_assertion() (2026-09-12) - replaces the earlier design of a static,
    pre-baked env_settings.REVOLUT_BUSINESS_API_CLIENT_ASSERTION, which would have gone stale
    almost immediately in real use since a client-assertion JWT's own `exp` claim is a short-lived
    expiry, not a one-time secret (confirmed against Revolut's own docs). Must be freshly signed
    (RS256) on every call."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        cls.public_key = private_key.public_key()

    @patch('libraries.banking.revolut_business.env_settings')
    def test_returns_none_when_signing_key_unset(self, mock_env_settings):
        mock_env_settings.REVOLUT_BUSINESS_API_SIGNING_KEY = None
        mock_env_settings.REVOLUT_BUSINESS_API_CLIENT_ID = 'client-123'
        self.assertIsNone(generate_client_assertion())

    @patch('libraries.banking.revolut_business.env_settings')
    def test_returns_none_when_client_id_unset(self, mock_env_settings):
        mock_env_settings.REVOLUT_BUSINESS_API_SIGNING_KEY = self.private_key_pem
        mock_env_settings.REVOLUT_BUSINESS_API_CLIENT_ID = None
        self.assertIsNone(generate_client_assertion())

    @patch('libraries.banking.revolut_business.env_settings')
    def test_produces_a_correctly_signed_and_shaped_jwt(self, mock_env_settings):
        mock_env_settings.REVOLUT_BUSINESS_API_SIGNING_KEY = self.private_key_pem
        mock_env_settings.REVOLUT_BUSINESS_API_CLIENT_ID = 'client-123'

        token = generate_client_assertion()
        claims = jwt.decode(token, self.public_key, algorithms=['RS256'], audience='https://revolut.com')

        self.assertEqual(claims['iss'], 'klt-hooks.up.railway.app')
        self.assertEqual(claims['sub'], 'client-123')
        self.assertEqual(claims['aud'], 'https://revolut.com')
        self.assertIsInstance(claims['exp'], int)


class OAuthTokenRequestShapeTests(SimpleTestCase):
    """get_access_token_for_revolut_business_api (ongoing refresh) and
    exchange_authorization_code_for_tokens (one-time bootstrap) both post to the same /auth/token
    endpoint with a freshly-generated client_assertion, differing only in grant_type - confirms
    each sends the exact parameter shape Revolut's own docs specify."""

    @patch('libraries.banking.revolut_business.generate_client_assertion')
    @patch('libraries.banking.revolut_business.requests.post')
    def test_refresh_token_grant_sends_correct_params(self, mock_post, mock_generate_assertion):
        mock_generate_assertion.return_value = 'signed-jwt'
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'at-1'}
        mock_post.return_value.raise_for_status = lambda: None

        result = get_access_token_for_revolut_business_api('my-refresh-token')

        self.assertEqual(result, 'at-1')
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['data']['grant_type'], 'refresh_token')
        self.assertEqual(kwargs['data']['refresh_token'], 'my-refresh-token')
        self.assertEqual(kwargs['data']['client_assertion'], 'signed-jwt')
        self.assertEqual(kwargs['data']['client_assertion_type'], 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer')

    @patch('libraries.banking.revolut_business.generate_client_assertion')
    def test_refresh_token_grant_returns_none_when_assertion_unavailable(self, mock_generate_assertion):
        mock_generate_assertion.return_value = None
        self.assertIsNone(get_access_token_for_revolut_business_api('my-refresh-token'))

    @patch('libraries.banking.revolut_business.generate_client_assertion')
    @patch('libraries.banking.revolut_business.requests.post')
    def test_authorization_code_grant_sends_correct_params_no_redirect_uri(self, mock_post, mock_generate_assertion):
        mock_generate_assertion.return_value = 'signed-jwt'
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'at-1', 'refresh_token': 'rt-1'}
        mock_post.return_value.raise_for_status = lambda: None

        result = exchange_authorization_code_for_tokens('auth-code-abc')

        self.assertEqual(result, {'access_token': 'at-1', 'refresh_token': 'rt-1'})
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['data']['grant_type'], 'authorization_code')
        self.assertEqual(kwargs['data']['code'], 'auth-code-abc')
        self.assertEqual(kwargs['data']['client_assertion'], 'signed-jwt')
        self.assertNotIn('redirect_uri', kwargs['data'])
