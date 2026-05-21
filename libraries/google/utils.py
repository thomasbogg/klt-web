from libraries.google.account import GoogleAccount
from libraries.google.connect import GoogleAPIService

# =============================================================================
# Google API Connection Functions
# =============================================================================

def get_google_api_connection(account: GoogleAccount | None = None, **kwargs) -> GoogleAPIService:
    """
    Creates and returns a connection to Google API using the provided account.
    
    Args:
        account: GoogleAccount instance containing authentication details
        **kwargs: Additional arguments to pass to GoogleAPIService
        
    Returns:
        GoogleAPIService: Connected Google API service instance
        
    Raises:
        Exception: If account is not provided or not a GoogleAccount instance
    """
    if not account:
        raise Exception('No GoogleAccount instance provided: cannot get Google API connection.')
    if not isinstance(account, GoogleAccount):
        raise Exception('Account is not a GoogleAccount instance: cannot get Google API connection.')
    return GoogleAPIService(username=account.emailAddress, credentials=account.credentials, LOCAL=account.local, **kwargs).connect()


def sort_connection(
        account: GoogleAccount | None = None,
        connection: GoogleAPIService | None = None,
        connectionCall: callable = None
) -> GoogleAPIService:
    """
    Ensures a valid Google API connection is available, creating one if needed.
    
    Args:
        account: GoogleAccount instance to use for creating a connection
        connection: Existing GoogleAPIService connection
        connection_call: Function to call to create a connection if needed
        
    Returns:
        GoogleAPIService: Valid Google API connection
        
    Raises:
        Exception: If neither account nor connection is provided, or if
                  connection creation fails
    """
    if not account and not connection:
        raise Exception('No account or connection provided: cannot get Google API Service.')
    
    if not connection:
        connection = connectionCall(account)
        if not connection:
            raise Exception('Failed to get Google API connection.')

    return connection