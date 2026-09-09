"""Sage One (Portugal) API client - developers.sageone.com/docs/pt/v2, transcribed 2026-09-09.

UNVERIFIED against a real account (no dev-app credentials existed at write time - see
finance/services.py::dispatch_memo_to_sage's own docstring for the manual registration/OAuth
grant only Thomas can do). Built directly from Sage's own documented request-signing algorithm
and worked example, not from live testing - same "confirm before relying on it" caveat this
codebase already uses for libraries/banking/revolut.py's sandbox hostname. Signature construction
in particular deserves a real round trip against api.sageone.com/test/... before this ever points
at production.

Auth is two independent layers, both required on every call:
1. OAuth2 bearer token (get_access_token/refresh_access_token below) - who you are.
2. An OAuth 1.0a-style HMAC-SHA1 request signature (X-Signature/X-Nonce headers, built by
   _sign()) - proof the request wasn't tampered with in transit. The signing key mixes the
   registered app's signing_secret with the CURRENT access_token, so it has to be rebuilt on every
   call and after every token refresh - there is no long-lived signature to cache."""
import base64
import hashlib
import hmac
import os
import re
from urllib.parse import quote

import requests

from libraries.utils import Object, logerror

import env_settings

PROD_BASE_URL = 'https://api.sageone.com'
# Documented sandbox path - real requests, fake data, fixed signing secret ('TestSigningSecret')
# rather than a registered app's real one. Use this for every call until Thomas has actually
# completed the developer-portal registration and OAuth grant, then proven a real round trip here
# before ever pointing at production.
#
# Deliberately its own env_settings.SAGE_SANDBOX flag, NOT env_settings.TEST (2026-09-09) -
# env_settings.TEST is only ever true during an automated pytest run (see libraries/banking/
# revolut.py's own identical pattern), so reusing it here would give Thomas no way to point a real,
# interactive dev session at the sandbox while manually verifying this integration - exactly the
# "prove it in sandbox first" step this module's own docstring calls for.
SANDBOX_BASE_URL = 'https://api.sageone.com/test'
BASE_URL = SANDBOX_BASE_URL if (env_settings.TEST or env_settings.SAGE_SANDBOX) else PROD_BASE_URL

AUTH_URL = 'https://www.sageone.com/oauth2/auth'
TOKEN_URL = 'https://api.sageone.com/oauth2/token'


def _percent_encode(value) -> str:
    """RFC 3986 percent-encoding, per Sage's own spec: unreserved characters (letters, digits,
    '-_.~') are never encoded, everything else (including space, encoded to %20 not '+') is
    percent-encoded with upper-case hex digits. Python's quote() already matches this exactly once
    `safe=''` removes '/' from its default safe set - quote() itself never touches '_.-~' or
    alphanumerics regardless of `safe`."""
    return quote(str(value), safe='')


def _parameter_string(params: dict) -> str:
    """Sage's 'Parameter String': percent-encode every key/value, sort by encoded key, join as
    key=value pairs with '&' - see _build_base_string()'s docstring for how this then gets
    percent-encoded AGAIN as a single unit when it goes into the base string (confirmed against
    Sage's own worked example: 'My Customer' -> 'My%20Customer' -> 'My%2520Customer')."""
    encoded_pairs = sorted(
        (_percent_encode(key), _percent_encode(value)) for key, value in params.items()
    )
    return '&'.join(f'{key}={value}' for key, value in encoded_pairs)


def _build_base_string(method: str, url: str, params: dict, nonce: str) -> str:
    """method & percent_encode(url) & percent_encode(parameter_string) & percent_encode(nonce) -
    the exact 7-step recipe from Sage's docs, confirmed against their worked example."""
    parameter_string = _parameter_string(params)
    return '&'.join((
        method.upper(),
        _percent_encode(url),
        _percent_encode(parameter_string),
        _percent_encode(nonce),
    ))


def _signing_key(signing_secret: str, access_token: str) -> str:
    return f'{_percent_encode(signing_secret)}&{_percent_encode(access_token)}'


def generate_nonce() -> str:
    """Sage's recommended recipe: base64 32 random bytes, strip everything that isn't a word
    character (letters/digits/underscore) - 'any approach which produces a relatively random
    alphanumeric string is acceptable' per their own docs, this is just their suggestion."""
    raw = base64.b64encode(os.urandom(32)).decode('ascii')
    return re.sub(r'\W', '', raw)


def _sign(method: str, url: str, params: dict, nonce: str, signing_secret: str, access_token: str) -> str:
    base_string = _build_base_string(method, url, params, nonce)
    key = _signing_key(signing_secret, access_token)
    digest = hmac.new(key.encode('ascii'), base_string.encode('ascii'), hashlib.sha1).digest()
    return base64.b64encode(digest).decode('ascii')


def _headers(access_token: str, signing_secret: str, method: str, url: str, params: dict) -> dict:
    nonce = generate_nonce()
    signature = _sign(method, url, params, nonce, signing_secret, access_token)
    return {
        'Authorization': f'Bearer {access_token}',
        'X-Signature': signature,
        'X-Nonce': nonce,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    }


def get_access_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict | None:
    """Exchanges a one-time authorization code (from the OAuth2 grant only Thomas, as the Sage One
    account owner, can perform - see AUTH_URL) for the first access_token/refresh_token pair.
    Returns Sage's raw token response dict (access_token, refresh_token, expires_in, ...), or None
    on failure - caller is responsible for persisting it (finance.SageSettings)."""
    response = requests.post(TOKEN_URL, data={
        'client_id': client_id, 'client_secret': client_secret,
        'code': code, 'grant_type': 'authorization_code', 'redirect_uri': redirect_uri,
    })
    if response.status_code == 200:
        return response.json()
    logerror(f'Failed to exchange Sage authorization code: {response.status_code} - {response.text}')
    return None


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict | None:
    """refresh_token rotates on every use (per Sage's docs) - the caller must persist the NEW
    refresh_token from the response, not just the new access_token, or the next refresh will fail."""
    response = requests.post(TOKEN_URL, data={
        'client_id': client_id, 'client_secret': client_secret,
        'refresh_token': refresh_token, 'grant_type': 'refresh_token',
    })
    if response.status_code == 200:
        return response.json()
    logerror(f'Failed to refresh Sage access token: {response.status_code} - {response.text}')
    return None


class Sage(Object):
    """access_token/signing_secret are passed in explicitly (never read from env_settings
    directly) - same convention as libraries.banking.revolut.Revolut, so this module stays
    framework-agnostic and the live-token lookup/refresh/persistence (finance.SageSettings) stays
    a Django-aware concern of finance/services.py, not this client."""

    class Contact(Object):
        _url = f'{BASE_URL}/accounts/v2/contacts'

        def __init__(self, access_token: str, signing_secret: str, **kwargs):
            super().__init__({'access_token': access_token, 'signing_secret': signing_secret, **kwargs})

        def find_by_name(self, name: str) -> dict | None:
            """First contact whose name matches `name` (Sage's own ?search= param), or None if
            none exists yet - the find half of finance/services.py::dispatch_memo_to_sage's
            find-or-create."""
            params = {'search': name}
            headers = _headers(self._get('access_token'), self._get('signing_secret'), 'GET', self._url, params)
            response = requests.get(self._url, headers=headers, params=params)
            if response.status_code != 200:
                logerror(f'Failed to search Sage contacts: {response.status_code} - {response.text}')
                return None
            resources = response.json().get('$resources', [])
            return resources[0] if resources else None

        def create(self, name: str, email: str = None, tax_number: str = None) -> dict | None:
            """contact_type_id=1 is Sage's own code for a customer (not a supplier) - the only
            kind of contact an owner invoice ever needs. Bracket-notation form params, per Sage's
            documented request format (not a JSON body). `contact[tax_number]` as the field name
            for an owner's NIF is an UNCONFIRMED GUESS (the fetched docs summary only confirmed
            name/contact_type_id) - verify the real field name (via Sage's sample code or a
            sandbox GET on an existing contact) before relying on this to actually reach Sage."""
            params = {'contact[contact_type_id]': 1, 'contact[name]': name}
            if email:
                params['contact[email]'] = email
            if tax_number:
                params['contact[tax_number]'] = tax_number
            headers = _headers(self._get('access_token'), self._get('signing_secret'), 'POST', self._url, params)
            response = requests.post(self._url, headers=headers, data=params)
            if response.status_code == 201:
                return response.json()
            logerror(f'Failed to create Sage contact: {response.status_code} - {response.text}')
            return None

    class SalesInvoice(Object):
        _url = f'{BASE_URL}/accounts/v2/sales_invoices'

        def __init__(self, access_token: str, signing_secret: str, **kwargs):
            super().__init__({'access_token': access_token, 'signing_secret': signing_secret, **kwargs})

        def create(self, contact_id: str, date, description: str, net_amount, tax_rate_id: str) -> dict | None:
            """One line item per invoice - every use in finance/services.py::dispatch_memo_to_sage
            is a single lump-sum charge (a Memo's total()), not an itemised breakdown, so there's
            no line-item list here to keep this simple until a real need for one shows up.

            The `invoice_lines][]` repeated-array bracket syntax below is an UNCONFIRMED GUESS at
            how Sage expects an array of line-item hashes in a form-encoded body (a common Rails
            convention, and Sage's API otherwise reads as Rails-shaped, but this specific encoding
            was not present in the fetched docs summary) - verify against Sage's own sample code
            (Ruby/C#/Java/PHP, linked from developers.sageone.com/docs/pt/v2) before relying on
            this to actually create a real invoice."""
            params = {
                'sales_invoice[contact_id]': contact_id,
                'sales_invoice[date]': date.isoformat(),
                'sales_invoice[invoice_lines][][description]': description,
                'sales_invoice[invoice_lines][][net_amount]': str(net_amount),
                'sales_invoice[invoice_lines][][tax_rate_id]': tax_rate_id,
            }
            headers = _headers(self._get('access_token'), self._get('signing_secret'), 'POST', self._url, params)
            response = requests.post(self._url, headers=headers, data=params)
            if response.status_code == 201:
                return response.json()
            logerror(f'Failed to create Sage sales invoice: {response.status_code} - {response.text}')
            return None

    def __init__(self, access_token: str = None, signing_secret: str = None, **kwargs):
        super().__init__({'access_token': access_token, 'signing_secret': signing_secret, **kwargs})

    @property
    def contact(self) -> Contact:
        return self.Contact(access_token=self._get('access_token'), signing_secret=self._get('signing_secret'))

    @property
    def sales_invoice(self) -> SalesInvoice:
        return self.SalesInvoice(access_token=self._get('access_token'), signing_secret=self._get('signing_secret'))
