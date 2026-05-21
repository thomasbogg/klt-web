from libraries.google.connect import GoogleAPIService
from datetime import date
from libraries.dates import dates
from typing import Any
from libraries.utils import (
    Object,
    toList
)


class GooglePerson(Object):
    """
    Represents a Google Contact with methods for managing contact information.
    
    This class provides functionality to create, update, delete, and manage
    Google Contact properties like name, email, phone, etc.
    
    Attributes:
        connection: Google API service connection
        id: Resource name (identifier) of the contact
        etag: Entity tag for versioning
        firstName: First name of the contact
        lastName: Last name of the contact
        phoneNumber: Phone number of the contact
        emailAddress: Email address of the contact
        birthday: Birthday of the contact
        address: Formatted address of the contact
        company: Company/organization name
        website: Website URL
        job: Job title or occupation
        userDefined: Custom fields for the contact
    """

    def __init__(
            self, 
            connection: GoogleAPIService | None = None, 
            load: dict | None = None, 
            id: str | None = None, 
            TEST: bool = False) -> None:
        """
        Initialize a Google Person (Contact) object.
        
        Args:
            connection: Connection to Google API service
            load: Dictionary containing contact data to load
            id: ID of the contact
            TEST: Flag for test mode
        """
        super().__init__(load=load, id=id, TEST=TEST)
        self._connection = connection
        self._service = connection.connection if connection else None

    # =============================================================================
    # Core API Methods
    # =============================================================================

    def create(self) -> dict:
        """
        Create a new Google Contact.
        
        Returns:
            dict: The created contact object
            
        Raises:
            ValueError: If the Google API service is not connected
        """
        if not self._service:
            raise ValueError('Google API service is not connected.')
        
        return self._service.people().createContact(body=self._values).execute()

    def update(self, updateFields: str = 'names,phoneNumbers,emailAddresses,userDefined') -> dict:
        """
        Update an existing Google Contact.
        
        Args:
            updateFields: Comma-separated list of fields to update
            
        Returns:
            dict: The updated contact object
        """
        updatedContact = None
        for field in updateFields.split(','):
            self._values = self._service.people().updateContact(
                                                    resourceName=self.id, 
                                                    body=self._values, 
                                                    updatePersonFields=field).execute()
        return updatedContact

    def delete(self) -> dict:
        """
        Delete this Google Contact.
        
        Returns:
            dict: API response for the deletion operation
        """
        return self._service.people().deleteContact(resourceName=self.id).execute()

    # =============================================================================
    # Helper Methods
    # =============================================================================
    
    def _get(self, key: str) -> Any:
        """
        Get a value from the contact data, handling nested structures.
        
        Args:
            key: Key to retrieve
            
        Returns:
            The value at the given key, with list handling
        """
        value = super()._get(key)
        if isinstance(value, list) and len(value) == 1:
            return value[0]
        return value

    def _set(self, key: str, value: Any) -> None:
        """
        Set a value in the contact data, handling nested structures.
        
        Args:
            key: Key to set
            value: Value to set
        """
        if key[-1] == 's':
            if key in self._values:
                self._values[key][0].update(value)
            else:
                self._values[key] = toList(value)
            return 
        return super()._set(key, value)
    
    # =============================================================================
    # Basic Properties
    # =============================================================================
    
    @property
    def connection(self) -> GoogleAPIService | None:
        """
        Get the Google API service connection.
        
        Returns:
            GoogleAPIService object or None if not connected
        """
        return self._connection
    
    @connection.setter
    def connection(self, value: GoogleAPIService) -> None:
        """
        Set the Google API service connection.
        
        Args:
            value: GoogleAPIService connection object
        """
        self._connection = value
        self._service = value.connection if value else None

    @property
    def hasConnection(self) -> bool:
        """
        Check if the contact has a valid API connection.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connection is not None

    @property
    def id(self) -> str:
        """
        Get the resource name (identifier) of the contact.
        
        Returns:
            Resource name string
        """
        return self._get('resourceName')
    
    @id.setter
    def id(self, value: str) -> 'GooglePerson':
        """
        Set the resource name (identifier) of the contact.
        
        Args:
            value: Resource name string
            
        Returns:
            Self reference for method chaining
        """
        self._set('resourceName', value)
        return self
    
    @property
    def etag(self) -> str:
        """
        Get the entity tag for versioning.
        
        Returns:
            Entity tag string
        """
        return self._get('etag')
    
    @etag.setter
    def etag(self, value: str) -> None:
        """
        Set the entity tag for versioning.
        
        Args:
            value: Entity tag string
        """
        self._set('etag', value)

    # =============================================================================
    # Contact Information Properties
    # =============================================================================

    @property
    def firstName(self) -> str | None:
        """
        Get the first name of the contact.
        
        Returns:
            First name string or None if not set
        """
        return self._get('names').get('givenName')
   
    @firstName.setter
    def firstName(self, value: str) -> None:
        """
        Set the first name of the contact.
        
        Args:
            value: First name string
        """
        self._set('names', {'givenName': value})
    
    @property
    def lastName(self) -> str | None:
        """
        Get the last name of the contact.
        
        Returns:
            Last name string or None if not set
        """
        return self._get('names').get('familyName')
   
    @lastName.setter
    def lastName(self, value: str) -> None:
        """
        Set the last name of the contact.
        
        Args:
            value: Last name string
        """
        self._set('names', {'familyName': value})
    
    @property
    def phoneNumber(self) -> str | None:
        """
        Get the phone number of the contact.
        
        Returns:
            Phone number string or None if not set
        """
        return self._get('phoneNumbers').get('value')
    
    @phoneNumber.setter
    def phoneNumber(self, value: str) -> None:
        """
        Set the phone number of the contact.
        
        Args:
            value: Phone number string
        """
        self._set('phoneNumbers', {'value': value})

    @property
    def emailAddress(self) -> str | None:
        """
        Get the email address of the contact.
        
        Returns:
            Email address string or None if not set
        """
        return self._get('emailAddresses').get('value')
    
    @emailAddress.setter
    def emailAddress(self, value: str) -> None:
        """
        Set the email address of the contact.
        
        Args:
            value: Email address string
        """
        self._set('emailAddresses', {'value': value})
    
    @property
    def birthday(self) -> date | None:
        """
        Get the birthday of the contact.
        
        Returns:
            Date object or None if not set
        """
        birthdays = self._get('birthdays')
        if not birthdays or 'date' not in birthdays:
            return None
        return dates.date(**birthdays.get('date'))
    
    @birthday.setter
    def birthday(self, value: date) -> None:
        """
        Set the birthday of the contact.
        
        Args:
            value: Date object
        """
        self._set('birthdays', {'date': {'year': value.year, 'month': value.month, 'day': value.day}})

    @property
    def address(self) -> str | None:
        """
        Get the formatted address of the contact.
        
        Returns:
            Address string or None if not set
        """
        return self._get('addresses').get('formattedValue')
    
    @address.setter
    def address(self, value: str) -> None:
        """
        Set the formatted address of the contact.
        
        Args:
            value: Address string
        """
        self._set('addresses', {'formattedValue': value})

    @property
    def company(self) -> str | None:
        """
        Get the company/organization name of the contact.
        
        Returns:
            Company name string or None if not set
        """
        return self._get('organizations').get('name')
    
    @company.setter
    def company(self, value: str) -> None:
        """
        Set the company/organization name of the contact.
        
        Args:
            value: Company name string
        """
        self._set('organizations', {'name': value})

    @property
    def website(self) -> str | None:
        """
        Get the website URL of the contact.
        
        Returns:
            Website URL string or None if not set
        """
        return self._get('urls').get('value')
    
    @website.setter
    def website(self, value: str) -> None:
        """
        Set the website URL of the contact.
        
        Args:
            value: Website URL string
        """
        self._set('urls', {'value': value})

    @property
    def job(self) -> str | None:
        """
        Get the job title or occupation of the contact.
        
        Returns:
            Job title string or None if not set
        """
        return self._get('occupations').get('value')
    
    @job.setter
    def job(self, value: str) -> None:
        """
        Set the job title or occupation of the contact.
        
        Args:
            value: Job title string
        """
        self._set('occupations', {'title': value})

    @property
    def userDefined(self) -> list | None:
        """
        Get custom fields for the contact.
        
        Returns:
            List of custom field dictionaries or None if not set
        """
        return self._get('userDefined')

    @userDefined.setter
    def userDefined(self, keyValueTuple: tuple[str, str]) -> None:
        """
        Add a custom field to the contact.
        
        Args:
            keyValueTuple: Tuple containing (key, value) for the custom field
            
        Raises:
            ValueError: If the tuple is invalid or contains empty values
        """
        if not isinstance(keyValueTuple, tuple) or len(keyValueTuple) != 2:
            raise ValueError('userDefined must be a tuple of (key, value)')
        if not keyValueTuple[0] or not keyValueTuple[1]:
            raise ValueError('userDefined key and value cannot be empty')
        if 'userDefined' not in self._values:
            self._values['userDefined'] = []
        self._values['userDefined'] += [
            {'key': keyValueTuple[0], 'value': keyValueTuple[1]}]