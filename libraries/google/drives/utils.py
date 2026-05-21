from libraries.google.account import GoogleAccount
from libraries.google.connect import GoogleAPIService
from libraries.google.drives.directory import GoogleDriveDirectory
from libraries.google.utils import (
    get_google_api_connection,
    sort_connection
)

# =============================================================================
# API Connection Functions
# =============================================================================

def get_google_drives_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a connection to Google Drive API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Drive API service instance
    """
    apiKwargs = {
        'api': 'drive', 
        'version': 'v3', 
        'scopes': ['https://www.googleapis.com/auth/drive']
    }
    return get_google_api_connection(account, **apiKwargs)


def get_refreshed_google_drives_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a fresh connection to Google Drive API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Drive API service instance
    """
    return get_google_drives_connection(account)


# =============================================================================
# Drive Management Functions
# =============================================================================

def get_google_drives(
        name: str | None = None, 
        account: GoogleAccount | None = None, 
        connection: GoogleAPIService | None = None, 
        TEST: bool = False,
        LOCAL: bool = True) -> GoogleDriveDirectory | list[GoogleDriveDirectory]:
    """
    Retrieves Google Drive(s) by name or lists all available drives.
    
    Args:
        name: Name of the drive to retrieve, or None to get all drives
        account: GoogleAccount instance for authentication
        connection: Existing GoogleAPIService connection to use
        TEST: Flag to enable test mode
        
    Returns:
        GoogleDriveDirectory: Single drive directory if name is provided
        list[GoogleDriveDirectory]: List of all drive directories if name is None
        
    Raises:
        Exception: If no drives are found or the specified drive name is not found
    """
    connection = sort_connection(account, connection, connectionCall=get_google_drives_connection)
    drives = connection.connection.drives().list(
                                            pageSize=5, 
                                            fields='nextPageToken, drives(id, name)', 
                                            useDomainAdminAccess=LOCAL).execute().get('drives', [])

    if not drives:
        raise Exception(f'No Google drives found using search word {name}.')

    if not name:
        return list(map(lambda x: GoogleDriveDirectory(connection, load=x, driveId=x['id'], TEST=TEST), drives))

    for drive in drives:
        if drive['name'] == name:
            return GoogleDriveDirectory(connection, load=drive, driveId=drive['id'], TEST=TEST)
    
    raise Exception(f'Drive {name} not found')