import requests
from libraries.utils import Object, logerror, generate_request_headers
from typing import List

API_VERSION = '2026-04-20'
BASE_URL = "https://merchant.revolut.com/api"


class Revolut(Object):

    class Webhook(Object):
        
        _url = f"{BASE_URL}/webhooks"

        def __init__(self, secretKey: str | None = None, apiVersion: str = API_VERSION, **kwargs):
            super().__init__({'apiVersion': apiVersion, 'secretKey': secretKey, **kwargs})

        def get(self):
            """
            Get the details of the webhook from Revolut.
            
            Returns:
                A dictionary containing the webhook details, or None if the request fails.
            """
            if not self._get('id'):
                logerror("Webhook ID is not set. Cannot retrieve webhook details.")
                return None
            
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            response = requests.get(f"{self._url}/{self._get('id')}", headers=headers)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to retrieve webhook: {response.status_code} - {response.text}")

        def list(self):
            """
            List all webhooks from Revolut.
            
            Returns:
                A generator of Revolut.Webhook objects, or None if the request fails.
            """
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            response = requests.get(self._url, headers=headers)
            if response.status_code == 200:
                for webhook_data in response.json().get('webhooks', []):
                    webhook = Revolut.Webhook(secretKey=self._get('secretKey'), apiVersion=self._get('apiVersion'), **webhook_data)
                    yield webhook
            else:
                logerror(f"Failed to list webhooks: {response.status_code} - {response.text}")
                return None

        def create(self):
            """
            Create a new webhook in Revolut with the current properties.
            
            Returns:
                A dictionary containing the created webhook details, or None if the request fails.
            """
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            payload = {
                'url': self._get('url'),
                'events': self._get('events'),
            }
            response = requests.post(self._url, headers=headers, json=payload)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to create webhook: {response.status_code} - {response.text}")

        def delete(self):
            """
            Delete the webhook from Revolut.
            
            Returns:
                True if the webhook was successfully deleted, False otherwise.
            """
            if not self._get('id'):
                logerror("Webhook ID is not set. Cannot delete webhook.")
                return False
            
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            headers.pop('Content-Type', None)  # Remove Content-Type header for DELETE request
            headers.pop('Accept', None)  # Remove Accept header for DELETE request
            response = requests.request('DELETE', f"{self._url}/{self._get('id')}", headers=headers, data={})
            if response.status_code == 204:
                return True
            else:
                logerror(f"Failed to delete webhook: {response.status_code} - {response.text}")
                return False
            
        def update(self):
            """
            Update the webhook in Revolut with the current properties.
            
            Returns:
                A dictionary containing the updated webhook details, or None if the request fails.
            """
            if not self._get('id'):
                logerror("Webhook ID is not set. Cannot update webhook.")
                return None
            
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            payload = {
                'url': self._get('url'),
                'events': self._get('events'),
            }
            response = requests.patch(f"{self._url}/{self._get('id')}", headers=headers, json=payload)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to update webhook: {response.status_code} - {response.text}")

        @property
        def url(self) -> str | None:
            """
            Get the URL for the webhook.
            
            Returns:
                The URL that the webhook will send events to, or None if not set.
            """
            return self._get('url')

        @url.setter
        def url(self, value: str) -> None:
            """
            Set the URL for the webhook.
            
            Args:
                value: The URL that the webhook will send events to.
            """
            self._set('url', value)

        @property
        def events(self) -> List[str]:
            """
            Get the list of events that trigger the webhook.
            
            Returns:
                A list of event types that trigger the webhook, or an empty list if not set.
            """
            return self._get('events') or []
        
        @events.setter
        def events(self, value: List[str] | str) -> None:
            """
            Set the list of events that trigger the webhook.
            
            Args:
                value: A list of event types or a single event type to set for the webhook.
            """
            val = self._values.get('events', [])
            if isinstance(value, list):
                val = list(set(val) | set(value))
            elif isinstance(value, str):
                if value not in val:
                    val.append(value)
            self._set('events', val)

        @property
        def signingSecret(self) -> str | None:
            """
            Get the signing secret for the webhook.
            
            Returns:
                The signing secret used to verify webhook events, or None if not set.
            """
            return self._get('signing_secret')


    class Payment(Object):

        _url = f"{BASE_URL}/orders"

        def __init__(self, currency: str = 'EUR', secretKey: str | None = None, apiVersion: str = API_VERSION, **kwargs):
            super().__init__({'apiVersion': apiVersion, 'secretKey': secretKey, 'currency': currency, **kwargs})

        def get(self):
            """
            Get the details of the payment order from Revolut.
            
            Returns:
                A dictionary containing the payment order details, or None if the request fails.
            """
            if not self._get('id'):
                logerror("Payment order ID is not set. Cannot retrieve payment order details.")
                return None
           
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            response = requests.get(f"{self._url}/{self._get('id')}", headers=headers)
            if response.status_code == 200:
                self._values = response.json()
            else:
                logerror(f"Failed to retrieve payment order: {response.status_code} - {response.text}")

        def create(self):
            """
            Create a new payment order in Revolut with the current properties.
            
            Returns:
                A dictionary containing the created payment order details, or None if the request fails.
            """
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            payload = {
                'amount': self._get('amount'),
                'currency': self._get('currency'),
                'description': self._get('description'),
                'customer': {
                    'email': self.customerEmail,
                    'phone': self.customerPhone,
                    'full_name': self.customerName,
                },
            }
            response = requests.post(self._url, headers=headers, json=payload)
            if response.status_code == 201:
                self._values.update(response.json())
            else:
                logerror(f"Failed to create payment order: {response.status_code} - {response.text}")
            return self

        def cancel(self):
            """
            Cancel the payment order in Revolut.
            
            Returns:
                A dictionary containing the updated payment order details after cancellation, or None if the request fails.
            """
            if not self._get('id'):
                logerror("Payment order ID is not set. Cannot cancel payment order.")
                return None
            
            headers = _get_headers(self._get('secretKey'), self._get('apiVersion'))
            response = requests.post(f"{self._url}/{self._get('id')}/cancel", headers=headers)
            if response.status_code == 200:
                self._values.update(response.json())
            else:
                logerror(f"Failed to cancel payment order: {response.status_code} - {response.text}")
            return self

        @property
        def amount(self) -> int:
            """
            Get the amount for the payment order.
            
            Returns:
                The amount for the payment order in the smallest currency unit (e.g., cents for EUR).
            """
            return self._get('amount')
        
        @amount.setter
        def amount(self, value: int) -> None:
            """
            Set the amount for the payment order.
            
            Args:
                value: The amount to set for the payment order in the smallest currency unit (e.g., cents for EUR).
            """
            self._set('amount', value)

        @property
        def currency(self) -> str:
            """
            Get the currency for the payment order.
            
            Returns:
                The currency code (e.g., 'EUR') for the payment order.
            """
            return self._get('currency')
        
        @currency.setter
        def currency(self, value: str) -> None:
            """
            Set the currency for the payment order.
            
            Args:
                value: The currency code (e.g., 'EUR') to set for the payment order.
            """
            self._set('currency', value)

        @property
        def customerName(self) -> str | None:
            """
            Get the full name of the customer for the payment order.
            
            Returns:
                The full name of the customer associated with the payment order, or None if not set.

            Returns:
                The full name of the customer associated with the payment order, or None if not set.
            """
            return self._get_customer('full_name')
        
        @customerName.setter
        def customerName(self, name: str) -> None:
            """
            Set the full name of the customer for the payment order.
            
            Args:
                name: The full name of the customer to set for the payment order.
            """
            self._set_customer('full_name', name)
        
        @property
        def customerEmail(self) -> str | None:
            """
            Get the email of the customer for the payment order.
            
            Returns:
                The email of the customer associated with the payment order, or None if not set.
            """
            return self._get_customer('email')
        
        @customerEmail.setter
        def customerEmail(self, email: str) -> None:
            """
            Set the email of the customer for the payment order.
            
            Args:
                email: The email of the customer to set for the payment order.
            """
            self._set_customer('email', email)

        @property
        def customerPhone(self) -> str | None:
            """
            Get the phone number of the customer for the payment order.
            
            Returns:
                The phone number of the customer associated with the payment order, or None if not set.
            """
            return self._get_customer('phone')
        
        @customerPhone.setter
        def customerPhone(self, phone: str) -> None:
            """
            Set the phone number of the customer for the payment order.
            
            Args:
                phone: The phone number of the customer to set for the payment order.
            """
            self._set_customer('phone', phone)

        @property
        def customerId(self) -> str | None:
            """
            Get the customer ID for the payment order.
            
            Returns:
                The customer ID associated with the payment order, or None if not set.
            """
            return self._get_customer('id')
        
        @customerId.setter
        def customerId(self, customer_id: str) -> None:
            """
            Set the customer ID for the payment order.
            
            Args:
                customer_id: The customer ID to set for the payment order.
            """
            self._set_customer('id', customer_id)

        @property
        def description(self) -> str | None:
            """
            Get the description for the payment order.
            
            Returns:
                The description of the payment order, or None if not set.
            """
            return self._get('description')
        
        @description.setter
        def description(self, value: str) -> None:
            """
            Set the description for the payment order.
            
            Args:
                value: The description to set for the payment order.
            """
            self._set('description', value)

        @property
        def token(self) -> str | None:
            """
            Get the payment token for the payment order.
            
            Returns:
                The payment token associated with the payment order, or None if not set.
            """
            return self._get('token')

        @property
        def outstandingAmount(self) -> int | None:
            """
            Get the outstanding amount for the payment order.
            
            Returns:
                The outstanding amount for the payment order in the smallest currency unit (e.g., cents for EUR), or None if not available.
            """
            return self._get('outstanding_amount')
        
        @property
        def state(self) -> str | None:
            """
            Get the state of the payment order.
            
            Returns:
                The state of the payment order (e.g., 'pending', 'paid', 'cancelled'), or None if not available.
            """
            return self._get('state')
        
        @property
        def checkoutUrl(self) -> str | None:
            """
            Get the checkout URL for the payment order.
            
            Returns:
                The URL that the customer can use to complete the payment, or None if not available.
            """
            return self._get('checkout_url')
        
        def _get_customer(self, field: str) -> str | None:
            """
            Helper method to get customer information from the payment order.
            
            Args:
                field: The specific customer field to get (e.g., 'email', 'phone').

            Returns:
                The value of the specified customer field, or None if not set.
            """
            customer = self._get('customer') or {}
            return customer.get(field)
        
        def _set_customer(self, field: str, value: str) -> None:
            """
            Helper method to set customer information in the payment order.
            
            Args:
                field: The specific customer field to set (e.g., 'email', 'phone').
                value: The value to set for the specified customer field.
            """
            if 'customer' not in self._values:
                self._values['customer'] = {}
            customer = self._values['customer']
            customer[field] = value
            self._set('customer', customer)


    def __init__(self, secretKey: str | None = None, apiVersion: str = API_VERSION, **kwargs):
        super().__init__({'secretKey': secretKey, 'apiVersion': apiVersion, **kwargs})

    @property
    def secretKey(self) -> str:
        """
        Get the Revolut API secret key.
        
        Returns:
            The API secret key for authentication
        """
        return self._get('secretKey')
    
    @secretKey.setter
    def secretKey(self, value: str) -> None:
        """
        Set the Revolut API secret key.
        
        Args:
            value: The API secret key to set for authentication
        """
        self._set('secretKey', value)

    @property
    def apiVersion(self) -> str:
        """
        Get the Revolut API version.
        
        Returns:
            The version of the Revolut API to use
        """
        return self._get('apiVersion')
    
    @apiVersion.setter
    def apiVersion(self, value: str) -> None:
        """
        Set the Revolut API version.
        
        Args:
            value: The version of the Revolut API to use
        """
        self._set('apiVersion', value)

    @property
    def webhook(self) -> Webhook:
        """
        Get an instance of the Webhook class for managing Revolut webhooks.
        
        Returns:
            An instance of the Webhook class initialized with the current API credentials
        """        
        return self.Webhook(secretKey=self.secretKey, apiVersion=self.apiVersion)
    
    @property
    def payment(self) -> Payment:
        """
        Get an instance of the Payment class for managing Revolut payment orders.
        
        Returns:
            An instance of the Payment class initialized with the current API credentials
        """        
        return self.Payment(secretKey=self.secretKey, apiVersion=self.apiVersion)


def _get_headers(secretKey: str, apiVersion: str) -> dict:
    """
    Generate the headers for API requests to Revolut, including authentication and API version.

    Args:
        secretKey: The API secret key for authentication
        apiVersion: The version of the Revolut API to use
    
    Returns:
        A dictionary containing the headers for API requests.
    """
    return generate_request_headers(secretKey=secretKey, **{'Revolut-Api-Version': apiVersion})