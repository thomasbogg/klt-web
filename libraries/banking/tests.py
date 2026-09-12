"""Regression tests for the 5 bugs found and fixed in revolut_business.py (2026-09-12) - this
module has no dedicated app of its own (not in INSTALLED_APPS), same as its sibling revolut.py,
whose own call sites are tested inline in bookings/tests.py instead. revolut_business.py has no
call site there yet (see finance/payouts_revolut.py for the real caller, tested via mocking at
get_revolut_business_connection in finance/tests.py) - these tests cover the class's own object
behavior directly instead, run via `manage.py test libraries.banking.tests`."""
from unittest.mock import patch

from django.test import SimpleTestCase

from libraries.banking.revolut_business import RevolutBusiness


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
