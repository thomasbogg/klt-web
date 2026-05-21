from libraries.google.connect import GoogleAPIService
from googleapiclient.discovery import Resource
from typing import Any
from libraries.utils import (
    Object,
    logtest
)


class GoogleDrivePermissions(Object):
    """
    Represents Google Drive file permissions with methods to manage access rights.
    
    This class provides functionality to create, retrieve, and update permissions
    for Google Drive files, including managing roles, access types, and email notifications.
    
    Attributes:
        connection: Google API service connection
        fileId: ID of the Drive file to manage permissions for
        role: Permission role (e.g., 'reader', 'writer', 'owner')
        type: Permission type (e.g., 'user', 'group', 'domain', 'anyone')
        emailAddress: Email address of the permission recipient
        emailMessage: Notification message to send when creating permissions
    """

    def __init__(
            self, 
            connection: GoogleAPIService | None = None,
            load: dict | None = None,
            id: str | None = None, 
            fileId: str | None = None, 
            TEST: bool = False) -> None:
        """
        Initialize a Google Drive Permissions object.
        
        Args:
            connection: Connection to Google API service
            load: Dictionary containing permission data to load
            id: ID of the permission
            fileId: ID of the associated Drive file
            TEST: Flag for test mode
        """
        super().__init__(load=load, id=id, TEST=TEST)
        self._connection: GoogleAPIService = connection
        self._service: Resource = connection.connection if connection else None
        self._fileId: str | None = fileId

    # =============================================================================
    # Properties
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
    def connection(self, connection: GoogleAPIService) -> None:
        """
        Set the Google API service connection.
        
        Args:
            connection: GoogleAPIService connection object
        """
        self._connection = connection
        self._service = connection.connection

    @property
    def fileId(self) -> str | None:
        """
        Get the ID of the associated Drive file.
        
        Returns:
            String ID of the file or None if not set
        """
        return self._fileId

    @fileId.setter
    def fileId(self, fileId: str) -> None:
        """
        Set the ID of the associated Drive file.
        
        Args:
            fileId: String ID of the file
        """
        self._fileId = fileId

    @property
    def role(self) -> str:
        """
        Get the permission role.
        
        Returns:
            Role string (e.g., 'reader', 'writer', 'owner')
        """
        return self._get('role')

    @role.setter
    def role(self, value: str) -> None:
        """
        Set the permission role.
        
        Args:
            value: Role string to set
        """
        self._set('role', value)

    @property
    def type(self) -> str:
        """
        Get the permission type.
        
        Returns:
            Type string (e.g., 'user', 'group', 'domain', 'anyone')
        """
        return self._get('type')

    @type.setter
    def type(self, value: str) -> None:
        """
        Set the permission type.
        
        Args:
            value: Type string to set
        """
        self._set('type', value)

    @property
    def emailAddress(self) -> str | None:
        """
        Get the email address associated with this permission.
        
        Returns:
            Email address string or None if not set
        """
        return self._get('emailAddress')
    
    @emailAddress.setter
    def emailAddress(self, value: str) -> None:
        """
        Set the email address associated with this permission.
        
        Args:
            value: Email address string
        """
        self._set('emailAddress', value)
    
    @property
    def emailMessage(self) -> str:
        """
        Get the email notification message.
        
        Returns:
            Email message string
        """
        if not hasattr(self, '_emailMessage'):
            self._emailMessage = self._generic_email_message()
        return self._emailMessage
    
    @emailMessage.setter
    def emailMessage(self, emailMessage: str) -> None:
        """
        Set the email notification message.
        
        Args:
            emailMessage: Email message string
        """
        self._emailMessage = emailMessage

    # =============================================================================
    # Public Methods
    # =============================================================================

    def get(self) -> 'GoogleDrivePermissions':
        """
        Retrieve permission details from Google Drive API.
        
        Returns:
            Self reference for method chaining
        """
        result: list[dict] = self._service.permissions().list(
                                                        fileId=self._fileId, 
                                                        supportsAllDrives=True).execute().get('permissions', [])
        if result:
            self._values = result[0]
        return self
    
    def update(self) -> dict:
        """
        Update the permission details in Google Drive.
        
        Returns:
            API response dictionary
        """
        if self.TEST: 
            logtest(f'Would update permission for {self.emailAddress} on {self._fileId}')
        return self._service.permissions().update(
                                            fileId=self._fileId, 
                                            permissionId=self.id, 
                                            body=self._values).execute()

    def create(self) -> dict:
        """
        Create a new permission in Google Drive.
        
        Returns:
            API response dictionary
        """
        if 'emailAddress' in self._values:
            return self._create_with_email()
        return self._create_without_email()

    # =============================================================================
    # Private Methods
    # =============================================================================

    def _create_without_email(self) -> dict:
        """
        Create permission without sending an email notification.
        
        Returns:
            API response dictionary
        """
        return self._service.permissions().create(
                                            fileId=self._fileId,
                                            body=self._values,
                                            fields="id",
                                            supportsAllDrives=True).execute()

    def _create_with_email(self) -> dict:
        """
        Create permission and send an email notification.
        
        Returns:
            API response dictionary
        """
        return self._service.permissions().create(
                                            fileId=self._fileId,
                                            body=self._values,
                                            fields="id",
                                            emailMessage=self.emailMessage,
                                            supportsAllDrives=True).execute()

    def _generic_email_message(self) -> str:
        """
        Generate a default email notification message.
        
        Returns:
            Default email message string
        """
        return 'You are now able to open and view this file. Please let me know if you have questions.'
    
    # =============================================================================
    # Magic Methods
    # =============================================================================
    
    def __getattr__(self, attr: str) -> Any:
        """
        Handle getting special attributes.
        
        Args:
            attr: Name of the attribute to get
            
        Returns:
            Attribute value
        """
        if attr == 'email':
            return self.emailAddress
        if attr == 'message':
            return self.emailMessage
        return super().__getattr__(attr)
    
    def __setattr__(self, attr: str, value: Any) -> None:
        """
        Handle setting special attributes.
        
        Args:
            attr: Name of the attribute to set
            value: New value for the attribute
        """
        if attr == 'email':
            self.emailAddress = value
        elif attr == 'message':
            self.emailMessage = value
        else:
            super().__setattr__(attr, value)