"""Revolut Business API client (accounts/counterparties/transfers - real money-out payouts),
ported from klt-management-software's libraries/revolut/business.py (2026-09-10). Kept as its own
module, separate from revolut.py's Revolut/Payment (the merchant checkout-link client already live
for guest/owner collections) - different Revolut product, different base host, different OAuth2
(refresh-token + JWT client-assertion) auth entirely.

Real call site: finance/payouts_revolut.py::send_owner_payout_via_revolut(), wired into the staff
Payouts tab's "Send payment" button (2026-09-12) - only reached for an owner with bank details on
file, and only actually connects once env_settings.REVOLUT_BUSINESS_API_* are configured (fails
closed to None otherwise, see get_access_token_for_revolut_business_api()). Keep
REVOLUT_BUSINESS_SANDBOX=True until the sandbox flow has been exercised end-to-end - see that
setting's own comment in env_settings.py for why the default the moment credentials exist is
PRODUCTION, not sandbox.

Known gap carried over from the source, not fixed here since fixing it is real feature work, not
plumbing: Account has no create/get/delete of its own (unlike Webhook/Counterparty/Transfer), so
Account.get() only returns Object's own base dict rather than calling ACCOUNTS_URL - fetching a
real account's details needs `next(revolut.accounts)`-style listing instead, or a get() override
added when this is actually switched on.
"""
import requests
from libraries.utils import Object, logerror, generate_request_headers, logwarning
from typing import Generator, List

import env_settings

PROD_BASE_URL = "https://b2b.revolut.com/api"
SANDBOX_BASE_URL = "https://sandbox-b2b.revolut.com/api"
BASE_URL = SANDBOX_BASE_URL if (env_settings.TEST or env_settings.REVOLUT_BUSINESS_SANDBOX) else PROD_BASE_URL
ACCOUNTS_URL = f"{BASE_URL}/1.0/accounts"
COUNTERPARTIES_URL = f"{BASE_URL}/1.0/counterparties"
TRANSFERS_URL = f"{BASE_URL}/1.0/pay"
WEBHOOKS_URL = f"{BASE_URL}/2.0/webhooks"


def _headers(access_token: str | None) -> dict:
    """generate_request_headers() (libraries/utils.py) is shared with revolut.py's unrelated
    Merchant API client, so the Business API's own Revolut-Api-Version header - required by
    Revolut, previously defined in env_settings but never actually sent (2026-09-12 bugfix) - is
    added here rather than in the shared helper."""
    kwargs = {'Revolut-Api-Version': env_settings.REVOLUT_BUSINESS_API_VERSION} if env_settings.REVOLUT_BUSINESS_API_VERSION else {}
    return generate_request_headers(access_token, **kwargs)


class RevolutBusiness(Object):

    class Webhook(Object):
        def __init__(self, accessToken: str | None = None, **kwargs):
            super().__init__({'accessToken': accessToken, **kwargs})

        def _get(self, key):
            return self._values.get(key)

        def get(self):
            if not self._get('id'):
                logerror("Webhook ID is not set. Cannot retrieve webhook details.")
                return None

            headers = _headers(self._get('accessToken'))
            response = requests.get(f"{WEBHOOKS_URL}/{self._get('id')}", headers=headers)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to retrieve webhook: {response.status_code} - {response.text}")

        def create(self):
            headers = _headers(self._get('accessToken'))
            payload = {
                'url': self._get('url'),
                'events': self._get('events'),
            }
            response = requests.post(WEBHOOKS_URL, headers=headers, json=payload)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to create webhook: {response.status_code} - {response.text}")

        def delete(self):
            if not self._get('id'):
                logerror("Webhook ID is not set. Cannot delete webhook.")
                return False

            headers = _headers(self._get('accessToken'))
            headers.pop('Content-Type', None)
            headers.pop('Accept', None)
            response = requests.request('DELETE', f"{WEBHOOKS_URL}/{self._get('id')}", headers=headers, data={})
            if response.status_code == 204:
                return True
            else:
                logerror(f"Failed to delete webhook: {response.status_code} - {response.text}")
                return False

        def update(self):
            if not self._get('id'):
                logerror("Webhook ID is not set. Cannot update webhook.")
                return None

            headers = _headers(self._get('accessToken'))
            payload = {
                'url': self._get('url'),
                'events': self._get('events'),
            }
            response = requests.patch(f"{WEBHOOKS_URL}/{self._get('id')}", headers=headers, json=payload)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to update webhook: {response.status_code} - {response.text}")

        @property
        def url(self) -> str | None:
            return self._get('url')

        @url.setter
        def url(self, value: str) -> None:
            self._set('url', value)

        @property
        def events(self) -> List[str]:
            return self._get('events') or []

        @events.setter
        def events(self, value: List[str] | str) -> None:
            val = self._values.get('events', [])
            if isinstance(value, list):
                val = list(set(val) | set(value))
            elif isinstance(value, str):
                if value not in val:
                    val.append(value)
            self._set('events', val)

        @property
        def signingSecret(self) -> str | None:
            return self._get('signing_secret')

    class Transfer(Object):
        def __init__(self, accessToken: str | None = None, **kwargs):
            super().__init__({'accessToken': accessToken, **kwargs})

        def _get(self, key):
            return self._values.get(key)

        def create(self):
            headers = _headers(self._get('accessToken'))
            payload = {
                'request_id': self._get('request_id'),
                'account_id': self.account.id,
                'receiver': {
                    'counterparty_id': self.counterparty.id,
                    'account_id': self.counterparty.account.id,
                },
                'amount': self._get('amount'),
                'charge_bearer': self._values.get('charge_bearer', 'shared'),
                'currency': self._get('currency'),
                'reference': self._get('reference'),
            }
            response = requests.post(TRANSFERS_URL, headers=headers, json=payload)
            if response.status_code in (200, 201):
                self._values.update(response.json())
            else:
                logerror(f"Failed to create payment order: {response.status_code} - {response.text}")
            return self

        @property
        def account(self) -> 'RevolutBusiness.Account':
            if not self._values.get('account'):
                self._values['account'] = RevolutBusiness.Account()
            return self._get('account')

        @account.setter
        def account(self, value: 'RevolutBusiness.Account') -> None:
            if not isinstance(value, RevolutBusiness.Account):
                logerror("Account must be an instance of RevolutBusiness.Account.")
                return
            self._set('account', value)

        @property
        def counterparty(self) -> 'RevolutBusiness.Counterparty':
            if not self._values.get('counterparty'):
                self._values['counterparty'] = RevolutBusiness.Counterparty(accessToken=self._get('accessToken'))
            return self._get('counterparty')

        @counterparty.setter
        def counterparty(self, value: 'RevolutBusiness.Counterparty') -> None:
            if not isinstance(value, RevolutBusiness.Counterparty):
                logerror("Counterparty must be an instance of RevolutBusiness.Counterparty.")
                return
            self._set('counterparty', value)

        @property
        def requestId(self) -> str | None:
            return self._get('request_id')

        @requestId.setter
        def requestId(self, value: str) -> None:
            self._set('request_id', value)

        @property
        def amount(self) -> int:
            return self._get('amount')

        @amount.setter
        def amount(self, value: int) -> None:
            self._set('amount', value)

        @property
        def currency(self) -> str:
            return self._get('currency')

        @currency.setter
        def currency(self, value: str) -> None:
            self._set('currency', value)

        @property
        def reference(self) -> str | None:
            return self._get('reference')

        @reference.setter
        def reference(self, value: str) -> None:
            self._set('reference', value)

        @property
        def chargeBearer(self) -> str:
            return self._get('charge_bearer')

        @chargeBearer.setter
        def chargeBearer(self, value: str) -> None:
            if value not in ['sender', 'receiver', 'shared']:
                logerror("Charge bearer must be one of 'sender', 'receiver', or 'shared'.")
                return
            self._set('charge_bearer', value)

        @property
        def state(self) -> str:
            return self._get('state')

        @property
        def createdAt(self) -> str:
            return self._get('created_at')

    class Counterparty(Object):
        class Address(Object):
            def __init__(self, *args, **kwargs):
                super().__init__(kwargs)

            @property
            def streetLine1(self) -> str | None:
                return self._get('street_line1')

            @streetLine1.setter
            def streetLine1(self, value: str) -> None:
                self._set('street_line1', value)

            @property
            def streetLine2(self) -> str | None:
                return self._get('street_line2')

            @streetLine2.setter
            def streetLine2(self, value: str) -> None:
                self._set('street_line2', value)

            @property
            def region(self) -> str | None:
                return self._get('region')

            @region.setter
            def region(self, value: str) -> None:
                self._set('region', value)

            @property
            def city(self) -> str | None:
                return self._get('city')

            @city.setter
            def city(self, value: str) -> None:
                self._set('city', value)

            @property
            def country(self) -> str | None:
                return self._get('country')

            @country.setter
            def country(self, value: str) -> None:
                self._set('country', value)

            @property
            def postcode(self) -> str | None:
                return self._get('postcode')

            @postcode.setter
            def postcode(self, value: str) -> None:
                self._set('postcode', value)

            def _get(self, key):
                return self._values.get(key)

        def __init__(self, accessToken: str | None = None, **kwargs):
            super().__init__({'accessToken': accessToken, **kwargs})

        def _get(self, key):
            return self._values.get(key)

        def create(self):
            headers = _headers(self._get('accessToken'))
            payload = {
                'revtag': self._values.get('revtag', None),
                'name': self._values.get('name', None),
                'company_name': self._values.get('company', None),
                # Was self._values.get('company', None) - a copy-paste of company_name above that
                # ignored the individualName setter's own dict entirely (2026-09-12 bugfix).
                'individual_name': self._get('individual_name'),
                'bank_country': self.account.country,
                'currency': self.account.currency,
                'account_no': self.account.accountNo,
                'iban': self.account.iban,
                'sort_code': self.account.sortCode,
                'routing_number': self.account.routingNumber,
                'bic': self.account.bic,
                'clabe': self.account.clabe,
                'ifsc': self.account.ifsc,
                'aub': self.account.aub,
                # self.address (the lazy property), not self._get('address') - a fresh
                # Counterparty() that never had .address accessed would otherwise KeyError here
                # even post-bugfix, since 'address' would never have been written into _values at
                # all (2026-09-12 bugfix).
                'address': self.address.get(),
            }
            response = requests.post(COUNTERPARTIES_URL, headers=headers, json=payload)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to create counterparty: {response.status_code} - {response.text}")

        def get(self):
            if not self._get('id'):
                logerror("Counterparty ID is not set. Cannot retrieve counterparty details.")
                return None

            headers = _headers(self._get('accessToken'))
            response = requests.get(f"{COUNTERPARTIES_URL}/{self._get('id')}", headers=headers)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to retrieve counterparty: {response.status_code} - {response.text}")

        def delete(self):
            if not self._get('id'):
                logerror("Counterparty ID is not set. Cannot delete counterparty.")
                return False

            headers = _headers(self._get('accessToken'))
            headers.pop('Content-Type', None)
            headers.pop('Accept', None)
            response = requests.request(
                'DELETE', f"{COUNTERPARTIES_URL}/{self._get('id')}", headers=headers, data={},
            )
            if response.status_code == 204:
                return True
            else:
                logerror(f"Failed to delete counterparty: {response.status_code} - {response.text}")
                return False

        @property
        def revtag(self) -> str | None:
            return self._get('revtag')

        @revtag.setter
        def revtag(self, value: str) -> None:
            self._set('revtag', value)

        @property
        def name(self) -> str | None:
            return self._get('name')

        @name.setter
        def name(self, value: str) -> None:
            self._set('name', value)

        @property
        def individualName(self) -> str | None:
            return self._get('individual_name')

        @individualName.setter
        def individualName(self, value: str) -> None:
            if len(value.split()) != 2:
                logwarning("Individual name must consist of first and last name only. Using those.")
            first, last = value.split()[0], value.split()[-1]
            self._set('individual_name', {'first_name': first, 'last_name': last})

        @property
        def companyName(self) -> str | None:
            return self._get('company_name')

        @companyName.setter
        def companyName(self, value: str) -> None:
            self._set('company_name', value)

        @property
        def profileType(self) -> str | None:
            return self._get('profile_type')

        @property
        def address(self) -> 'RevolutBusiness.Counterparty.Address':
            if not self._values.get('address'):
                self._values['address'] = RevolutBusiness.Counterparty.Address()
            return self._values['address']

        @property
        def accounts(self) -> Generator['RevolutBusiness.Account', None, None]:
            if not self._values.get('accounts'):
                return []
            for account_data in self._values['accounts']:
                yield RevolutBusiness.Account().set(account_data)

        @property
        def account(self) -> 'RevolutBusiness.Account':
            if not self._values.get('account'):
                if self._values.get('accounts'):
                    self._values['account'] = list(self.accounts)[0]
                else:
                    self._values['account'] = RevolutBusiness.Account()
            return self._values['account']

        @account.setter
        def account(self, value: 'RevolutBusiness.Account') -> None:
            if not isinstance(value, RevolutBusiness.Account):
                logerror("Account must be an instance of RevolutBusiness.Account.")
                return
            self._set('account', value)

    class Account(Object):
        def __init__(self, *args, **kwargs):
            super().__init__(kwargs)

        @property
        def balance(self) -> int:
            return self._get('balance')

        @property
        def currency(self) -> str | None:
            return self._get('currency')

        @currency.setter
        def currency(self, value: str) -> None:
            self._set('currency', value)

        @property
        def country(self) -> str | None:
            return self._get('bank_country')

        @country.setter
        def country(self, value: str) -> None:
            self._set('bank_country', value)

        @property
        def accountNo(self) -> str | None:
            return self._get('account_no')

        @accountNo.setter
        def accountNo(self, value: str) -> None:
            self._set('account_no', value)

        @property
        def iban(self) -> str | None:
            return self._get('iban')

        @iban.setter
        def iban(self, value: str) -> None:
            self._set('iban', value)

        @property
        def sortCode(self) -> str | None:
            return self._get('sort_code')

        @sortCode.setter
        def sortCode(self, value: str) -> None:
            self._set('sort_code', value)

        @property
        def routingNumber(self) -> str | None:
            return self._get('routing_number')

        @routingNumber.setter
        def routingNumber(self, value: str) -> None:
            self._set('routing_number', value)

        @property
        def bic(self) -> str | None:
            return self._get('bic')

        @bic.setter
        def bic(self, value: str) -> None:
            self._set('bic', value)

        @property
        def clabe(self) -> str | None:
            return self._get('clabe')

        @clabe.setter
        def clabe(self, value: str) -> None:
            self._set('clabe', value)

        @property
        def ifsc(self) -> str | None:
            return self._get('ifsc')

        @ifsc.setter
        def ifsc(self, value: str) -> None:
            self._set('ifsc', value)

        @property
        def aub(self) -> str | None:
            return self._get('aub')

        @aub.setter
        def aub(self, value: str) -> None:
            self._set('aub', value)

        @property
        def type(self) -> str | None:
            return self._get('type')

        @property
        def state(self) -> str:
            return self._get('state')

        @property
        def public(self) -> bool:
            return self._get('public')

        @property
        def createdAt(self) -> str:
            return self._get('created_at')

        @property
        def updatedAt(self) -> str:
            return self._get('updated_at')

        def _get(self, key):
            return self._values.get(key)

    def __init__(self, accessToken: str | None = None, **kwargs):
        super().__init__({'accessToken': accessToken, **kwargs})

    @property
    def accessToken(self) -> str:
        return self._get('accessToken')

    @accessToken.setter
    def accessToken(self, value: str) -> None:
        self._set('accessToken', value)

    @property
    def account(self) -> Account:
        return self.Account(accessToken=self.accessToken)

    @property
    def transfer(self) -> Transfer:
        return self.Transfer(accessToken=self.accessToken)

    @property
    def counterparty(self) -> Counterparty:
        return self.Counterparty(accessToken=self.accessToken)

    @property
    def webhook(self) -> Webhook:
        return self.Webhook(accessToken=self.accessToken)

    @property
    def accounts(self) -> Generator[Account, None, None]:
        yield from self._list(ACCOUNTS_URL, self.Account)

    @property
    def counterparties(self) -> Generator[Counterparty, None, None]:
        yield from self._list(COUNTERPARTIES_URL, self.Counterparty)

    @property
    def webhooks(self) -> Generator[Webhook, None, None]:
        yield from self._list(WEBHOOKS_URL, self.Webhook)

    def _list(self, url, item_class) -> List:
        headers = _headers(self._get('accessToken'))
        response = requests.get(url, headers=headers)
        items = []
        if response.status_code == 200:
            for item_data in response.json():
                item = item_class(self._get('accessToken'), **item_data)
                items.append(item)
        else:
            logerror(f"Failed to list {item_class.__name__} items: {response.status_code} - {response.text}")
        return items


def get_access_token_for_revolut_business_api(refresh_token: str, client_assertion: str) -> str | None:
    """Exchanges a long-lived refresh token + signed JWT client assertion for a short-lived access
    token (Revolut Business API's OAuth2 flow - not the simple secret-key auth the Merchant API
    uses). Returns None (rather than raising) whenever either credential is unset, which is the
    normal state until the Business account upgrade + app registration actually happens."""
    if not refresh_token or not client_assertion:
        return None

    url = f"{BASE_URL}/1.0/auth/token"
    headers = generate_request_headers()
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': client_assertion,
    }
    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        return response.json().get('access_token')
    except requests.RequestException as e:
        logerror(f"Failed to obtain access token for Revolut Business API: {e}")
        return None


def get_revolut_business_connection() -> RevolutBusiness | None:
    """Entry point for future callers - returns None while the Business API credentials aren't
    configured yet (env_settings.REVOLUT_BUSINESS_API_REFRESH_TOKEN/_CLIENT_ASSERTION), rather than
    a connection that would fail on first real call. No call sites yet (2026-09-10) - this is
    infrastructure staged ahead of the Revolut Business account upgrade, not a live feature."""
    access_token = get_access_token_for_revolut_business_api(
        refresh_token=env_settings.REVOLUT_BUSINESS_API_REFRESH_TOKEN,
        client_assertion=env_settings.REVOLUT_BUSINESS_API_CLIENT_ASSERTION,
    )
    if access_token is None:
        return None
    return RevolutBusiness(accessToken=access_token)
