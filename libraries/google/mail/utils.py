from libraries.google.account import GoogleAccount
from libraries.google.connect import GoogleAPIService
from libraries.google.mail.messages import GoogleMailMessages
from libraries.google.utils import get_google_api_connection, sort_connection

# =============================================================================
# Gmail API Connection Functions
# =============================================================================

def get_google_mail_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates a connection to the Gmail API.
    
    Args:
        account: Google account to use for authentication
        
    Returns:
        GoogleAPIService: Connected Gmail API service
        
    Raises:
        Exception: If account is not provided or invalid
    """
    apiKwargs = {
        'api': 'gmail', 
        'version': 'v1', 
        'scopes': ['https://www.googleapis.com/auth/gmail.modify',
                   'https://mail.google.com/']
    }
    return get_google_api_connection(account, **apiKwargs)


def get_refreshed_google_mail_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates a fresh connection to the Gmail API.
    
    Args:
        account: Google account to use for authentication
        
    Returns:
        GoogleAPIService: Fresh Gmail API service connection
    """
    return get_google_mail_connection(account)

# =============================================================================
# Gmail User Functions
# =============================================================================

def get_google_mail_user(
        account: GoogleAccount | None = None, 
        connection: GoogleAPIService | None = None, 
        TEST: bool = False) -> GoogleMailMessages:
    """
    Gets a GoogleMailMessages instance for interacting with Gmail.
    
    Args:
        account: Google account to use for authentication
        connection: Existing GoogleAPIService connection to use
        TEST: Whether to operate in test mode
        
    Returns:
        GoogleMailMessages: Interface for working with Gmail messages
    """
    connection = sort_connection(account, connection, get_google_mail_connection)
    return GoogleMailMessages(connection, account, TEST)