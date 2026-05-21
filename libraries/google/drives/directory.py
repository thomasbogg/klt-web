from libraries.google.connect import GoogleAPIService
from libraries.google.drives.file import GoogleDriveFile
from typing import Self
from libraries.utils import (
    Object,
    logtest,
    sublog,
    toList
)


class GoogleDriveDirectory(Object):
    """
    Represents a Google Drive directory with methods for directory operations.
    
    This class provides functionality to create, delete, search, and manage
    Google Drive directories and the files/subdirectories within them.
    
    Attributes:
        connection: Google API service connection
        id: Unique identifier for the directory
        name: Name of the directory
        parents: List of parent folder IDs
        driveId: ID of the drive containing this directory
        mimeType: MIME type of the directory
        link: Web view link to the directory
        files: List of files in the directory
        subdirectories: List of subdirectories in the directory
        contents: List of all files and subdirectories
    """

    def __init__(
            self,
            connection: GoogleAPIService,
            load: dict | None = None,
            id: str | None = None,
            name: str | None = None,
            parents: list | None = None,
            driveId: str | None = None,
            TEST: bool = False) -> None:
        """
        Initialize a Google Drive Directory object.
        
        Args:
            connection: Connection to Google API service
            load: Dictionary containing directory data to load
            id: ID of the directory
            name: Name of the directory
            parents: List of parent folder IDs
            driveId: ID of the drive containing this directory
            TEST: Flag for test mode
        """
        super().__init__(load, id, name, TEST)
        self._connection = connection
        self._service = connection.connection if connection else None
        if parents:
            self._values['parents'] = parents
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
        Check if the directory has a valid API connection.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connection is not None

    @property
    def parents(self) -> list | None:
        """
        Get the parent folder IDs of the directory.
        
        Returns:
            List of parent folder IDs or None if not set
        """
        return self._get('parents')
  
    @parents.setter
    def parents(self, value: list) -> None:
        """
        Set the parent folder IDs of the directory.
        
        Args:
            value: List of parent folder IDs
        """
        self._set('parents', toList(value))

    @property
    def driveId(self) -> str | None:
        """
        Get the ID of the drive containing this directory.
        
        Returns:
            Drive ID string or None if not set
        """
        return self._driveId
    
    @driveId.setter
    def driveId(self, value: str) -> None:
        """
        Set the ID of the drive containing this directory.
        
        Args:
            value: Drive ID string
        """
        self._driveId = value

    @property
    def mimeType(self) -> str | None:
        """
        Get the MIME type of the directory.
        
        Returns:
            MIME type string or None if not set
        """
        return self._get('mimeType')
    
    @mimeType.setter
    def mimeType(self, value: str) -> None:
        """
        Set the MIME type of the directory.
        
        Args:
            value: MIME type string
        """
        self._set('mimeType', value)

    @property
    def link(self) -> str | None:
        """
        Get the web view link to the directory.
        
        Returns:
            Web view link string or None if not set
        """
        return self._get('webViewLink')

    @property
    def exists(self) -> bool:
        """
        Check if the directory exists in Google Drive.
        
        Returns:
            True if the directory exists, False otherwise
        """
        try:
            return bool(self.id)
        except KeyError:
            result = self._search(self._get_query(
                                                name=self.name, 
                                                exactMatch=True, 
                                                isFile=False,
                                                lookingForItself=True))
        if not result:
            return False
        
        self._values = result[0]
        return True
    
    @property
    def isMain(self) -> bool:
        """
        Check if this is the main directory of a Drive.
        
        Returns:
            True if this is the main directory, False otherwise
        """
        return self.id == self.driveId
    
    @property
    def files(self) -> list[GoogleDriveFile] | None:
        """
        Get all files in this directory.
        
        Returns:
            List of GoogleDriveFile objects or None if no files found
        """
        files = self._search(self._get_query(isDirectory=False))
        return list(map(self.file, files)) if files else None
    
    @property
    def subdirectories(self) -> list[Self] | None:
        """
        Get all subdirectories in this directory.
        
        Returns:
            List of GoogleDriveDirectory objects or None if no subdirectories found
        """
        folders = self._search(self._get_query(isFile=False))
        return list(map(self.subdirectory, folders)) if folders else None
    
    @property
    def contents(self) -> list[GoogleDriveFile | Self] | None:
        """
        Get all files and subdirectories in this directory.
        
        Returns:
            Mixed list of GoogleDriveFile and GoogleDriveDirectory objects,
            or None if directory is empty
        """
        contents = self._search(self._get_query())
        if not contents:
            return None
        
        result = []
        for item in contents:
            if 'folder' in item.get('mimeType', ''):
                result.append(self.subdirectory(item))
            else:
                result.append(self.file(item))
        return result

    # =============================================================================
    # Directory Methods
    # =============================================================================

    def create(self) -> Self:
        """
        Create this directory in Google Drive.
        
        Returns:
            Self reference for method chaining
            
        Raises:
            ValueError: If no service connection is available
        """
        if self._service is None:
            raise ValueError('No service connection found for Directory Creation.')
        name = self.name
        if self.TEST:
            name += ' (TEST)'
        sublog(f'CREATING google drive directory: {name}')

        self.mimeType = 'application/vnd.google-apps.folder'
        self._values = self._service.files().create(
                                            body=self._values,
                                            supportsAllDrives=True).execute()
        return self

    def delete(self) -> Self:
        """
        Delete this directory from Google Drive.
        
        Returns:
            Self reference for method chaining
            
        Raises:
            ValueError: If directory doesn't exist, is the main directory, 
                       or no service connection is available
        """
        if not self.exists:
            raise ValueError(f'Directory {self.name} does not exist.')
        if self.isMain:
            raise ValueError(f'Cannot delete main directory {self.name}.')
        if self._service is None:
            raise ValueError('No service connection found for Directory Deletion.')
        if self.TEST:
            return logtest(f'delete folder {self.name} at {self.id}')
        sublog(f'DELETING google drive directory: {self.name}')
        self._service.files().delete(
                                fileId=self.id, 
                                supportsAllDrives=True).execute()
        return self
    
    def search(
            self, 
            name: str | None = None, 
            exactMatch: bool = True, 
            isFile: bool = True) -> list[GoogleDriveFile | Self] | None:
        """
        Search for files or directories within this directory.
        
        Args:
            name: Name to search for, or None to get all items
            exactMatch: Whether to match the name exactly or use partial matching
            isFile: True to search for files, False to search for directories
            
        Returns:
            List of matching items (either files or directories) or None if no matches
        """
        result = self._search(self._get_query(
                                      name=name, 
                                      exactMatch=exactMatch, 
                                      isFile=isFile, 
                                      isDirectory=not isFile))
        if not result:
            return None
        
        searchResults = []
        for item in result:
            if 'folder' in item.get('mimeType', ''):
                searchResults.append(self.subdirectory(item))
            else:
                searchResults.append(self.file(item))
        return searchResults

    def file(
            self, 
            load: dict | None = None, 
            id: str | None = None, 
            name: str | None = None, 
            path: str | None = None) -> GoogleDriveFile:
        """
        Create a GoogleDriveFile object for a file in this directory.
        
        Args:
            load: Dictionary containing file data to load
            id: ID of the file
            name: Name of the file
            path: Local path to the file
            
        Returns:
            GoogleDriveFile object
        """
        return GoogleDriveFile(
                            self._connection,
                            load=load,
                            id=id,
                            name=name,
                            parents=[self.id],
                            path=path,
                            driveId=self._driveId,
                            TEST=self.TEST
        )
    
    def subdirectory(
            self, 
            load: dict | None = None, 
            id: str | None = None, 
            name: str | None = None) -> Self:
        """
        Create a GoogleDriveDirectory object for a subdirectory of this directory.
        
        Args:
            load: Dictionary containing directory data to load
            id: ID of the directory
            name: Name of the directory
            
        Returns:
            GoogleDriveDirectory object
        """
        return GoogleDriveDirectory(
                                self._connection,
                                load=load,
                                id=id,
                                name=name,
                                parents=[self.id],
                                driveId=self._driveId,
                                TEST=self.TEST
        )
    
    def newFile(self, name: str | None = None, path: str | None = None) -> GoogleDriveFile:
        """
        Create a new file in this directory.
        
        Args:
            name: Name of the new file
            path: Local path to the file
            
        Returns:
            GoogleDriveFile object
        """
        return self.file(name=name, path=path)

    def directory(self, name: str | None = None) -> Self:
        """
        Get or create a subdirectory by name.
        
        Args:
            name: Name of the directory
            
        Returns:
            GoogleDriveDirectory object
        """
        return self.subdirectory(name=name)
    
    def newSubdirectory(self, name: str | None = None) -> Self:
        """
        Create a new subdirectory in this directory.
        
        Args:
            name: Name of the new subdirectory
            
        Returns:
            GoogleDriveDirectory object
        """
        return self.subdirectory(name=name)

    # =============================================================================
    # Private Methods
    # =============================================================================

    def _search(self, query: str | None = None) -> list | None:
        """
        Execute a search query against Google Drive API.
        
        Args:
            query: Search query string
            
        Returns:
            List of search results or None if no results
            
        Raises:
            ValueError: If no service connection is available
        """
        if self._service is None:
            raise ValueError('No service connection found for Directory Search.')
        searchFields = 'nextPageToken, files(id, name, mimeType, parents, webViewLink)'
        result = self._service.files().list(
                                pageSize=1000,
                                corpora='drive',
                                driveId=self._driveId, 
                                fields=searchFields,
                                includeItemsFromAllDrives=True,
                                supportsAllDrives=True,
                                q=query).execute().get('files', [])
        return result

    def _get_query(
            self, 
            name: str | None = None, 
            exactMatch: bool = True, 
            isFile: bool = True, 
            isDirectory: bool = True,
            lookingForItself: bool = False) -> str:
        """
        Build a query string for searching Google Drive.
        
        Args:
            name: Name to search for
            exactMatch: Whether to match the name exactly
            isFile: Include files in results
            isDirectory: Include directories in results
            lookingForItself: Whether searching for this directory itself
            
        Returns:
            Formatted query string
        """
        query = []
        query += ['trashed = false']
        
        if lookingForItself:
            query += [f'"{self.parents[0]}" in parents']
        else:
            query += [f'"{self.id}" in parents']
        
        if not isFile:
            query += ['mimeType="application/vnd.google-apps.folder"']
        elif not isDirectory:
            query += ['mimeType!="application/vnd.google-apps.folder"']
        
        if not name:
            return ' and '.join(query)
        
        if exactMatch:
            query += [f'name="{name}"']
        else:
            query += [f'name contains "{name}"'] 
        return ' and '.join(query)