from libraries.google.account import GoogleAccount
from libraries.google.connect import GoogleAPIService
from libraries.google.contacts.person import GooglePerson
from libraries.google.utils import (
    get_google_api_connection,
    sort_connection
)
from libraries.utils import logerror

# =============================================================================
# API Connection Functions
# =============================================================================

def get_google_contacts_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a connection to Google Contacts API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Contacts API service instance
    """
    apiKwargs = {
        'api': 'people',
        'version': 'v1',
        'scopes': ['https://www.googleapis.com/auth/contacts'],
    }
    return get_google_api_connection(account, **apiKwargs)


def get_refreshed_google_contacts_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a fresh connection to Google Contacts API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Contacts API service instance
    """
    return get_google_contacts_connection(account)


# =============================================================================
# Contact Management Functions
# =============================================================================

def get_google_contacts(
        account: GoogleAccount | None = None,
        connection: GoogleAPIService | None = None,
        number: int = 10,
        personFields: str = 'names,emailAddresses,phoneNumbers',
        name: str | None = None,
        TEST: bool = False) -> GooglePerson | list[GooglePerson]:
    """
    Retrieves Google contacts, either a specific contact by name or a list of contacts.
    
    Args:
        account: GoogleAccount instance for authentication
        connection: Existing GoogleAPIService connection to use
        number: Maximum number of contacts to retrieve when listing
        personFields: Comma-separated fields to include in the response
        name: Name to search for a specific contact, or None to list contacts
        TEST: Flag to enable test mode
        
    Returns:
        GooglePerson: Single contact if name is provided and found
        list[GooglePerson]: List of contacts if name is not provided
        
    Raises:
        Exception: If a named contact is not found
    """
    connection = sort_connection(account, connection, get_google_contacts_connection)

    if name:
        result = connection.connection.people().searchContacts(
                                                            query=name, 
                                                            readMask=personFields).execute().get('results', [])
        if not result:
            return None
        return GooglePerson(connection, load=result[0]['person'], TEST=TEST)

    results = connection.connection.people().connections().list(
                                                            resourceName="people/me", 
                                                            pageSize=number, 
                                                            personFields=personFields).execute().get("connections", [])

    return list(map(lambda x: GooglePerson(connection, load=x, TEST=TEST), results))


def new_google_contact(
        account: GoogleAccount | None = None,
        connection: GoogleAPIService | None = None,
        firstName: str | None = None,
        lastName: str | None = None,
        emailAddress: str | None = None,
        phoneNumber: str | None = None,
        TEST: bool = False) -> GooglePerson:
    """
    Creates a new Google contact with the specified details.
    
    Args:
        account: GoogleAccount instance for authentication
        connection: Existing GoogleAPIService connection to use
        firstName: First name of the contact
        lastName: Last name of the contact
        emailAddress: Email address of the contact
        phoneNumber: Phone number of the contact
        TEST: Flag to enable test mode
        
    Returns:
        GooglePerson: Newly created contact object (not yet saved to Google)
    """
    connection = sort_connection(account, connection, get_google_contacts_connection)
    person = GooglePerson(connection, TEST=TEST)
    person.firstName = firstName
    person.lastName = lastName
    person.emailAddress = emailAddress
    person.phoneNumber = phoneNumber
    return person