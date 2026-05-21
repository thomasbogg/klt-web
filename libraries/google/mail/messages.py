from libraries.google.account import GoogleAccount
from libraries.google.connect import GoogleAPIService
from libraries.google.mail.message import GoogleMailMessage
from libraries.utils import Object


class GoogleMailMessages(Object):
    """
    Manages and queries Gmail messages through the Google API.
    
    Provides functionality to search, filter, and retrieve messages and drafts
    from a Gmail account, as well as to create new message instances.
    """
  
    def __init__(
            self, 
            connection: GoogleAPIService, 
            account: GoogleAccount, 
            TEST: bool = False) -> None:
        """
        Initialize a new GoogleMailMessages instance.
        
        Args:
            connection: Authenticated Google API connection
            account: Google account associated with these messages
            TEST: If True, operations will not affect production data
        """
        super().__init__(TEST=TEST)
        self._connection = connection
        self._service = connection.connection
        self._account = account
        self._userId = 'me' #if account.local else account.credentials[0].get('client_id')

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def list(self) -> list[GoogleMailMessage]:
        """
        Retrieve messages based on the current search criteria.
        
        Returns:
            list[GoogleMailMessage]: List of message objects matching the criteria
        """
        type = self._values.pop('type', 'messages')
        query = ' '.join([f'{k}:{v}' for k, v in self._values.items()])
        self._values = {} # Reset search parameters

        if type == 'messages':
            search = self._service.users().messages()
        elif type == 'drafts':
            search = self._service.users().drafts()
        elif type == 'labels':
            search = self._service.users().labels()

        response = search.list(userId=self._userId, q=query).execute()
        results = response.get(type, [])
        
        if not results or type == 'labels':
            return results
        
        setter = lambda x: self.set(x, isDraft=type=='drafts')
        return list(map(setter, results))
    
    def _set(self, key: str, value: str | None) -> 'GoogleMailMessages':
        """
        Set a search parameter for message retrieval.
        
        Args:
            key: Search parameter key
            value: Search parameter value
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        if value is None:
            return self
        super()._set(key, value)
        return self
    
    @property
    def connection(self) -> GoogleAPIService:
        """Get the API connection associated with this message collection."""
        return self._connection
    
    @connection.setter
    def connection(self, value: GoogleAPIService) -> None:
        """Set the API connection for this message collection."""
        self._connection = value
        self._service = value.connection if value else None

    @property
    def hasConnection(self) -> bool:
        """Check if there is an active API connection."""
        return self._connection is not None
    
    @property
    def account(self) -> GoogleAccount:
        """Get the Google account associated with these messages."""
        return self._account

    # =============================================================================
    # Search Methods
    # =============================================================================

    def subject(self, value: str) -> 'GoogleMailMessages':
        """
        Filter messages by subject.
        
        Args:
            value: Subject text to search for
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('subject', value)

    def to(self, value: str) -> 'GoogleMailMessages':
        """
        Filter messages by recipient.
        
        Args:
            value: Recipient email address
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('to', value)

    def sender(self, value: str) -> 'GoogleMailMessages':
        """
        Filter messages by sender.
        
        Args:
            value: Sender email address
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('from', value)

    def start(self, value: str) -> 'GoogleMailMessages':
        """
        Filter messages by start date.
        
        Args:
            value: Start date in format YYYY/MM/DD
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('after', value)

    def end(self, value: str) -> 'GoogleMailMessages':
        """
        Filter messages by end date.
        
        Args:
            value: End date in format YYYY/MM/DD
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('before', value)

    def recipient(self, value: str) -> 'GoogleMailMessages':
        """
        Alias for to() - filter messages by recipient.
        
        Args:
            value: Recipient email address
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('to', value)

    def inbox(self) -> 'GoogleMailMessages':
        """
        Filter messages to those in the inbox.
        
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('in', 'inbox')
    
    def sent(self) -> 'GoogleMailMessages':
        """
        Filter messages to those in the sent folder.
        
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('in', 'sent')

    def drafts(self) -> 'GoogleMailMessages':
        """
        Set type to retrieve drafts instead of messages.
        
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('type', 'drafts')

    def labels(self) -> 'GoogleMailMessages':
        """
        Set type to retrieve labels instead of messages.
        
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('type', 'labels')
    
    def folder(self, value: str) -> 'GoogleMailMessages':
        """
        Filter messages by folder.
        
        Args:
            value: Folder name to search in
            
        Returns:
            GoogleMailMessages: Self for method chaining
        """
        return self._set('in', value)
    
    # =============================================================================
    # Message Methods
    # =============================================================================
    
    def message(self, id: str = None, load: dict = None, isDraft: bool = False) -> GoogleMailMessage:
        """
        Create or retrieve a specific message instance.
        
        Args:
            id: Message ID to retrieve
            load: Message data to load
            isDraft: Whether this message is a draft
            
        Returns:
            GoogleMailMessage: Message object
        """
        if id and not load:
            load = self._service.users().messages().get(userId=self._userId, id=id).execute()
        return GoogleMailMessage(
            connection=self._connection, load=load, id=id, isDraft=isDraft, TEST=self.TEST)
    
    def set(self, message: dict, isDraft: bool = False) -> GoogleMailMessage:
        """
        Create a message object from API response data.
        
        Args:
            message: Message data from the API
            isDraft: Whether this message is a draft
            
        Returns:
            GoogleMailMessage: Message object
        """
        if isDraft:
            return self.message(id=message['message']['id'], isDraft=True)
        return self.message(id=message['id'], isDraft=False)