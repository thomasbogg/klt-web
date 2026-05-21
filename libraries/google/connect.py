import os.path
from libraries.dates import dates
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
import json


class GoogleAPIService:
    """
    Service class to authenticate and connect to various Google APIs.
    
    Handles OAuth2 authentication flow, token management, and provides
    a connection to specified Google API services.
    """

    def __init__(self,
            username: str = '',
            api: str = '',
            version: str = '',
            scopes: list[str] | None = None,
            credentials: str = '',
            LOCAL: bool = False) -> None:
        """
        Initialize a new GoogleAPIService instance.
        
        Args:
            username: Google account username
            api: Google API service name (e.g., 'gmail', 'drive')
            version: API version (e.g., 'v1', 'v3')
            scopes: List of OAuth scopes required for API access
            credentials: Path to the directory containing credentials files or service account info
            LOCAL: Whether to use local credentials flow (True) or service account flow (False)
        """
        self._username = username
        self._api = api 
        self._version = version
        self._scopes = scopes or []
        self._credentials = credentials
        self._local = LOCAL
        self._connection = None
        self._connection_time = None
    
    # =============================================================================
    # Connection Methods
    # =============================================================================
        
    def get_local_connection(self) -> 'GoogleAPIService':
        """
        Establishes a connection to the specified Google API.
        
        Returns:
            GoogleAPIService: Self for method chaining
        """
        self._connection_time = dates.now()
        self._connection: Resource = build(
                            self._api, 
                            self._version, 
                            credentials=self._get_token())
        return self
    
    def get_service_account_connection(self) -> 'GoogleAPIService':
        """
        Establishes a connection to the specified Google API using a service account.
        
        Returns:
            GoogleAPIService: Self for method chaining
        """
        #print('\n'.join(self._parse_service_account_info()['private_key'].split('\\n')))
        credentials = service_account.Credentials.from_service_account_info(
            self._parse_service_account_info(),
            scopes=self._scopes
        )
        delegated_credentials = credentials.with_subject(self.username)
        self._connection_time = dates.now()
        self._connection: Resource = build(
                            self._api, 
                            self._version, 
                            credentials=delegated_credentials)
        return self
    
    def connect(self) -> 'GoogleAPIService':
        """
        Alias for get_local_connection() - establishes a connection to the specified Google API.
        
        Returns:
            GoogleAPIService: Self for method chaining
        """
        if self._local:
            return self.get_local_connection()
        return self.get_service_account_connection()

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def username(self) -> str:
        """Get the username associated with this service."""
        return self._username

    @username.setter
    def username(self, username: str) -> None:
        """Set the username for this service."""
        self._username = username

    @property
    def connection(self) -> Resource:
        """Get the API connection object."""
        return self._connection
    
    @property
    def isConnected(self) -> bool:
        """Check if the service is connected to the API."""
        return self._connection is not None
    
    @property
    def connectionTime(self) -> str:
        """Get the time when the connection was established."""
        return self._connection_time
    
    @property
    def api(self) -> str:
        """Get the API service name."""
        return self._api
    
    @api.setter
    def api(self, api: str) -> None:
        """Set the API service name."""
        self._api = api
    
    @property
    def version(self) -> str:
        """Get the API version."""
        return self._version
    
    @version.setter
    def version(self, version: str) -> None:
        """Set the API version."""
        self._version = version
    
    @property
    def scopes(self) -> list[str]:
        """Get the API scopes."""
        return self._scopes
    
    @scopes.setter
    def scopes(self, scopes: list[str]) -> None:
        """Set the API scopes."""
        self._scopes = scopes
    
    @property
    def credentials(self) -> str:
        """Get the path to credentials directory."""
        return self._credentials

    # =============================================================================
    # Authentication Methods
    # =============================================================================

    def _get_token(self) -> Credentials:
        """
        Retrieves or creates an OAuth token for API authentication.
        
        Returns:
            Credentials: OAuth credentials object
        """
        credentials = self._get_existing_token()
        
        if credentials and credentials.valid: 
            return credentials 
        
        if credentials and credentials.expired and credentials.refresh_token: 
            return self._get_refreshed_token(credentials)
        return self._get_new_token()

    def _get_existing_token(self) -> Credentials | None:
        """
        Retrieves existing token from file if it exists.
        
        Returns:
            Credentials | None: Existing credentials or None if not found
        """
        if not os.path.exists(self._get_token_file_path()): 
            return None
        return Credentials.from_authorized_user_file(
                                                self._get_token_file_path(), 
                                                self._scopes
        )

    def _get_refreshed_token(self, credentials: Credentials) -> Credentials:
        """
        Refreshes expired OAuth credentials.
        
        Args:
            credentials: Expired credentials with valid refresh token
            
        Returns:
            Credentials: Refreshed credentials
        """
        try: 
            credentials.refresh(Request())
            return self._write_to_token_file(credentials)
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return self._get_new_token()

    def _get_new_token(self) -> Credentials:
        """
        Creates new OAuth credentials through authorization flow.
        
        Returns:
            Credentials: New OAuth credentials
        """
        flow = InstalledAppFlow.from_client_secrets_file(
                                                self._get_credentials_file_path(), 
                                                self._scopes
        )
        credentials = flow.run_local_server(port=0)
        return self._write_to_token_file(credentials)
    
    def _get_credentials_file_path(self) -> str:
        """
        Gets the file path to the credentials JSON file.
        
        Returns:
            str: Path to credentials file
        """
        return f'{self._credentials}/credentials-{self._username.split(".")[0]}.json'
    
    def _get_token_file_path(self) -> str:
        """
        Gets the file path where the token should be stored.
        
        Returns:
            str: Path to token file
        """
        return f'{self._credentials}/token-{self._username.split(".")[0]}-{self._api}-{self._version}.json'

    def _write_to_token_file(self, credentials: Credentials) -> Credentials:
        """
        Writes OAuth credentials to a token file.
        
        Args:
            credentials: OAuth credentials to save
            
        Returns:
            Credentials: The saved credentials
        """
        with open(self._get_token_file_path(), 'w') as token:
            token.write(credentials.to_json())
        return credentials
    
    def _parse_service_account_info(self) -> dict:
        """
        Parses service account information from the credentials string.
        
        Returns:
            dict: Service account information as a dictionary
        """
        if isinstance(self._credentials[0], str):
            if '.json' in self._credentials[0]:
                with open(self._credentials[0], 'r') as f:
                    return json.load(f)
            string = self._credentials[0].replace('{', '').replace('}', '')
            d = dict()
            for k_v in string.split(','):
                k = None
                v = None
                for item in k_v.split('"'):
                    item = item.strip()
                    if len(item) > 0:
                        if not k:
                            k = item
                        else:
                            v = item
                d[k] = v
            return d
        return self._credentials[0]