from libraries.google.account import GoogleAccount
from libraries.google.connect import GoogleAPIService
from libraries.google.forms.form import GoogleForm
from libraries.google.utils import (
    get_google_api_connection,
    sort_connection
)
from libraries.utils import logwarning, toList

# =============================================================================
# API Connection Functions
# =============================================================================

def get_google_forms_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a connection to Google Forms API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Forms API service instance
    """
    apiKwargs = {
        'api': 'forms', 
        'version': 'v1', 
        'scopes': [
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/forms.body',
        ],
    }
    return get_google_api_connection(account, **apiKwargs)


def get_refreshed_google_forms_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a fresh connection to Google Forms API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Forms API service instance
    """
    return get_google_forms_connection(account)


# =============================================================================
# Forms Management Functions
# =============================================================================

def get_google_forms(
        id: str | list[str] = None,
        account: GoogleAccount | None = None,
        connection: GoogleAPIService | None = None,
        TEST: bool = False) -> list[GoogleForm]:
    """
    Retrieves one or more Google Forms by their IDs.
    
    Args:
        id: Single form ID or list of form IDs to retrieve
        account: GoogleAccount instance for authentication
        connection: Existing GoogleAPIService connection to use
        TEST: Flag to enable test mode
        
    Returns:
        list[GoogleForm]: List of retrieved Google Forms
        
    Raises:
        Exception: If no form ID is provided
    """
    connection = sort_connection(account, connection, connectionCall=get_google_forms_connection)
    ids = toList(id)
    if not ids:
        raise Exception('No form ID(s) provided: cannot get Google Form(s).')
    
    result = []
    for formId in ids:
        form = connection.connection.forms().get(formId=formId).execute()
        if not form:
            logwarning(f'Failed to get Google Form: {formId}')
        result.append(GoogleForm(connection, load=form, id=formId, TEST=TEST))
    
    return result

def create_new_form(
    account: GoogleAccount | None = None,
    connection: GoogleAPIService | None = None,
    title: str = 'New Form',
    documentTitle: str = 'New Form',
    isQuiz: bool = False,
    TEST: bool = False,) -> GoogleForm:
    """
    Creates a new Google Form with the specified title and document title.

    Args:
        account: GoogleAccount instance for authentication
        connection: Existing GoogleAPIService connection to use
        title: Title of the new form
        documentTitle: Document title of the new form
        TEST: Flag to enable test mode

    Returns:
        GoogleForm: The newly created Google Form object
    """
    connection = sort_connection(account, connection, connectionCall=get_google_forms_connection)
    form = GoogleForm(connection=connection, TEST=TEST)
    form.title = title
    form.documentTitle = documentTitle
    form.create()
    form.isQuiz = isQuiz
    return form