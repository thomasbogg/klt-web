import io
from libraries.google.connect import GoogleAPIService
from libraries.google.drives.permissions import GoogleDrivePermissions
from googleapiclient.http import (
    MediaFileUpload, 
    MediaIoBaseDownload
)
from typing import Any
from libraries.utils import (
    Object,
    logtest,
    logwarning,
    sublog
)


class GoogleDriveFile(Object):
    """
    Represents a Google Drive file with methods for file operations.
    
    This class provides functionality to create, retrieve, update, delete, 
    download, and manage Google Drive files and their permissions.
    
    Attributes:
        connection: Google API service connection
        id: Unique identifier for the file
        name: Name of the file
        parents: List of parent folder IDs
        path: Local file path
        mimeType: MIME type of the file
        link: Web view link to the file
        permissions: File permissions manager
    """

    def __init__(
            self,
            connection: GoogleAPIService,
            load: dict | None = None,
            id: str | None = None,
            name: str | None = None,
            parents: list | None = None,
            path: str | None = None,
            driveId: str | None = None,
            TEST: bool = False) -> None:
        """
        Initialize a Google Drive File object.
        
        Args:
            connection: Connection to Google API service
            load: Dictionary containing file data to load
            id: ID of the file
            name: Name of the file
            parents: List of parent folder IDs
            path: Local path to the file
            driveId: ID of the drive containing this file
            TEST: Flag for test mode
        """
        super().__init__(load, id, name, TEST)
        self._connection = connection
        self._service = connection.connection if connection else None
        if parents:
            self._values['parents'] = parents
        self._path = path
        self._driveId = driveId

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
        Check if the file has a valid API connection.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connection is not None
    
    @property
    def exists(self) -> bool:
        """
        Check if the file exists in Google Drive.
        
        Returns:
            True if the file exists, False otherwise
        """
        try:
            return bool(self.id)
        except KeyError:
            return self._retrieve()
    
    @property
    def parents(self) -> list | None:
        """
        Get the parent folder IDs of the file.
        
        Returns:
            List of parent folder IDs or None if not set
        """
        return self._get('parents')
    
    @parents.setter
    def parents(self, value: list) -> None:
        """
        Set the parent folder IDs of the file.
        
        Args:
            value: List of parent folder IDs
        """
        self._set('parents', value)
    
    @property
    def mimeType(self) -> str:
        """
        Get the MIME type of the file.
        
        Returns:
            MIME type string
        """
        return self._get('mimeType')
    
    @mimeType.setter
    def mimeType(self, value: str) -> None:
        """
        Set the MIME type of the file.
        
        Args:
            value: MIME type string
        """
        self._set('mimeType', value)

    @property
    def link(self) -> str:
        """
        Get the web view link to the file.
        
        Returns:
            Web view link string
        """
        return self._get('webViewLink')

    @property
    def path(self) -> str:
        """
        Get the local file path.
        
        Returns:
            Local file path string
        """
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        """
        Set the local file path.
        
        Args:
            value: Local file path string
        """
        self._path = value

    @property
    def localPath(self) -> str:
        """
        Get the local file path (alias for path).
        
        Returns:
            Local file path string
        """
        return self.path
    
    @localPath.setter
    def localPath(self, value: str) -> None:
        """
        Set the local file path (alias for path).
        
        Args:
            value: Local file path string
        """
        self.path = value

    @property
    def permissions(self) -> GoogleDrivePermissions:
        """
        Get the permissions manager for this file.
        
        Returns:
            GoogleDrivePermissions object
        """
        if not hasattr(self, '_permissions'):
            self._permissions = GoogleDrivePermissions(
                                                    connection=self._connection, 
                                                    fileId=self.id, 
                                                    TEST=self.TEST,
            )
        return self._permissions

    # =============================================================================
    # File Information Methods
    # =============================================================================

    def get(self) -> 'GoogleDriveFile':
        """
        Retrieve file metadata from Google Drive.
        
        Returns:
            Self reference for method chaining
        """
        if not self._values.get('id', None):
            logwarning('File ID not set: cannot use get() method on file. Trying _retrieve() instead.') 
        
            if not self._retrieve():
                logwarning(f'File {self.name} not found using _retrieve() either.')
            return self

        self._values = self._service.files().get(
                                        fileId=self.id, 
                                        supportsAllDrives=True).execute()
        return self

    def _retrieve(self) -> bool:
        """
        Retrieve file information by query parameters instead of ID.
        
        Returns:
            True if file was found, False otherwise
        """
        query = []
        query += ['trashed = false']
        query += ['mimeType!="application/vnd.google-apps.folder"']
        query += [f'name="{self.name}"']
        if 'parents' in self._values:
            query += [f'"{self._values["parents"][0]}" in parents']
        query = ' and '.join(query)
   
        searchFields = 'nextPageToken, files(id, name, mimeType, parents, webViewLink)'
        result = self._service.files().list(
                                        pageSize=1000,
                                        corpora='drive',
                                        driveId=self._driveId, 
                                        fields=searchFields,
                                        includeItemsFromAllDrives=True,
                                        supportsAllDrives=True,
                                        q=query).execute().get('files', [])
        if not result:
            return False
        self._values = result[0]
        return True

    # =============================================================================
    # File Content Methods
    # =============================================================================

    def update(self, verbose: bool = True) -> dict | None:
        """
        Update an existing file in Google Drive with local content.
        
        Uploads the current local file content to replace the existing file in Drive.
        
        Args:
            verbose: Whether to log the operation
            
        Returns:
            API response dictionary or None in test mode
        """
        if self.TEST: 
            return logtest(f'update {self.name} at {self.id}')
       
        if verbose: 
            sublog(f'REUPLOADING: {self.name}')
       
        media = MediaFileUpload(
                            self.path, 
                            mimetype=self.mimeType)
       
        return self._service.files().update(
                                        fileId=self.id,
                                        supportsAllDrives=True,
                                        media_body=media).execute()

    def upload(self, verbose: bool = True) -> 'GoogleDriveFile':
        """
        Upload a new file to Google Drive.
        
        Creates a new file in Drive based on the local file path.
        
        Args:
            verbose: Whether to log the operation
            
        Returns:
            Self reference for method chaining or None in test mode
        """
        if self.TEST:   
            return logtest(f'upload new file {self.name} to folder {self.parents}')
      
        if verbose: 
            sublog(f'UPLOADING: {self.name}')
      
        self._service.files().create(
                                body=super().get(), 
                                media_body=MediaFileUpload(
                                                        self.path, 
                                                        mimetype=self.mimeType), 
                                supportsAllDrives=True).execute()
      
        return self

    def download(self, verbose: bool = True) -> 'GoogleDriveFile':
        """
        Download file content from Google Drive to local path.
        
        Args:
            verbose: Whether to log the operation
            
        Returns:
            Self reference for method chaining
        """
        if verbose: 
            sublog(f'DOWNLOADING: {self.name}')
     
        request = self._sort_request_for_file_type()
        targetFile = io.BytesIO()
        downloader = MediaIoBaseDownload(targetFile, request)
        done = False
       
        while not done:
            _, done = downloader.next_chunk()
     
        with io.open(self._path, 'wb') as f:
            targetFile.seek(0)
            f.write(targetFile.read())
        return self

    # =============================================================================
    # File Management Methods
    # =============================================================================

    def backup(self, backupParentId: str) -> 'GoogleDriveFile':
        """
        Create a backup copy of the file in a specified folder.
        
        Args:
            backupParentId: ID of the parent folder for the backup
            
        Returns:
            Self reference for method chaining
        """
        sublog(f'BACKING UP: {self.name}')
        newFile = self._new_file([backupParentId])
        newFile.mimeType = self.mimeType
        if self.TEST: 
            newFile.name = f'{self.name} - TEST'
        newFile.upload(verbose=False)
        return self

    def delete(self) -> dict | None:
        """
        Delete the file from Google Drive.
        
        Returns:
            API response dictionary or None in test mode
        """
        try:
            content = self.name
        except KeyError:
            content = self.id
        
        if self.TEST: 
            return logtest(f'delete file {content}')
     
        sublog(f'DELETING: {content}')
        return self._service.files().delete(
                                        fileId=self.id, 
                                        supportsAllDrives=True).execute()

    def move(self, targetFolderId: str) -> 'GoogleDriveFile':
        """
        Move the file to a different folder in Google Drive.
        
        Downloads the file, deletes the original, and uploads to the new location.
        
        Args:
            targetFolderId: ID of the target folder
            
        Returns:
            Self reference for method chaining
        """
        if self.TEST: 
            return logtest(f'move file {self.name} at {self.parents} to {targetFolderId}')
      
        sublog(f'MOVING: {self.name}')
        self.download()
        self.delete()
        self.parents = [targetFolderId]

        del self._values['id']
        del self._values['webViewLink']

        self.upload()
        return self

    def copy(self, copyName: str) -> 'GoogleDriveFile':
        """
        Create a copy of the file with a new name.
        
        Args:
            copyName: Name for the copied file
            
        Returns:
            New GoogleDriveFile object for the copy
        """
        if self.TEST: 
            copyName = f'{copyName} - TEST'

        sublog(f'COPYING: {self.name}')
        copied = self._service.files().copy(
                                        fileId=self.id, 
                                        body={'name': copyName}, 
                                        supportsAllDrives=True).execute()
     
        return GoogleDriveFile(self._connection, load=copied)

    # =============================================================================
    # Helper Methods
    # =============================================================================

    def _get(self, key: str) -> Any:
        """
        Get a value from the file's metadata.
        
        Args:
            key: Key to retrieve from metadata
            
        Returns:
            Value associated with the key or None if not found
        """
        if key == 'webViewLink':
            value = self._values.get(key, None)
            if value is None:
                self.get()
        return super()._get(key)

    def _sort_request_for_file_type(self):
        """
        Create appropriate download request based on file type.
        
        Returns:
            API request object for downloading the file
        """
        if not self.id:
            return None
        
        mimetype = self.mimeType
        if mimetype not in self._mimetype_equivalents():
            return self._service.files().get_media(fileId=self.id)
        
        return self._service.files().export_media(
                                            fileId=self.id, 
                                            mimeType=self._mimetype_equivalents()[mimetype]
        )
        
    def _mimetype_equivalents(self) -> dict[str, str]:
        """
        Map Google Workspace file types to export formats.
        
        Returns:
            Dictionary mapping Google MIME types to export formats
        """
        return {
            'application/vnd.google-apps.document': 'application/vnd.oasis.opendocument.text',
            'application/vnd.google-apps.spreadsheet': 'text/csv',
            'application/vnd.google-apps.presentation': 'application/vnd.oasis.opendocument.presentation',
            'application/vnd.google-apps.drawing': 'application/vnd.oasis.opendocument.graphics',
            'application/vnd.google-apps.script': 'application/vnd.google-apps.script+json',
        }
    
    def _new_file(self, parents: list) -> 'GoogleDriveFile':
        """
        Create a new file object with the same properties as this one.
        
        Args:
            parents: List of parent folder IDs for the new file
            
        Returns:
            New GoogleDriveFile object
        """
        return GoogleDriveFile(
                            self._connection, 
                            name=self.name, 
                            parents=parents, 
                            path=self._path, 
                            TEST=self.TEST
        )
    
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
        if attr == 'localPath':
            return self._path
        if attr == 'mimetype':
            return self.mimeType
        return super().__getattr__(attr)
    
    def __setattr__(self, attr: str, value: Any) -> None:
        """
        Handle setting special attributes.
        
        Args:
            attr: Name of the attribute to set
            value: New value for the attribute
        """
        if attr == 'localPath':
            self.path = value
            return
        if attr == 'mimetype':
            self.mimeType = value
            return
        super().__setattr__(attr, value)