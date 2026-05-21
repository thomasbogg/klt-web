import os
from libraries.utils import Object, logtest


class File(Object):
    """
    File class to manage files in the file system.
    
    Args:
        path: Path to the file
        name: Name of the file
        extension: File extension
        TEST: If True, operations will be logged but not executed
    
    Raises:
        Exception: If the provided path does not exist
    """

    def __init__(
            self, 
            path: str, 
            name: str | None = None, 
            extension: str | None = None, 
            TEST: bool = False) -> None:
        super().__init__(name=name, TEST=TEST)
        if not os.path.exists(path):
            raise Exception(
                f"System File not Found: the path {path} does not exist.")
        self.path = path
        self.extension = extension
        
    # =============================================================================
    # Properties
    # =============================================================================
    
    @property
    def isFile(self) -> bool:
        """
        Check if the object is a file.
        
        Returns:
            Always True for File objects
        """
        return True
    
    @property
    def isDirectory(self) -> bool:
        """
        Check if the object is a directory.
        
        Returns:
            Always False for File objects
        """
        return False

    @property
    def path(self) -> str:
        """
        Get the file path.
        
        Returns:
            The path string
        """
        return self._get('path')
    
    @path.setter
    def path(self, value: str) -> None:
        """
        Set the file path.
        
        Args:
            value: The path to set
        """
        self._set('path', value)

    @property
    def extension(self) -> str | None:
        """
        Get the file extension.
        
        Returns:
            The file extension string or None
        """
        return self._get('extension')
    
    @extension.setter
    def extension(self, value: str | None) -> None:
        """
        Set the file extension.
        
        Args:
            value: The extension to set
        """
        self._set('extension', value)

    # =============================================================================
    # Operations
    # =============================================================================

    def delete(self) -> 'File':
        """
        Delete the file.
        
        Returns:
            Self for method chaining
        """
        if self.TEST: 
            return logtest(f'delete file @ {self.path}')
        os.remove(self.path)
        return self

    def rename(self) -> 'File':
        """
        Rename the file to the name stored in the object.
        
        Returns:
            Self for method chaining
        """
        oldName = self.path
        directory = '/'.join(oldName.split('/')[:-1])
        newName = os.path.join(directory, self.name)

        if self.TEST:
            logtest(f'rename file @ {oldName} to {newName}')
        else:
            os.rename(oldName, newName)
        return self
        
    def create(self) -> 'File':
        """
        Create an empty file at the specified path.
        
        Returns:
            Self for method chaining
        """
        baseName = os.path.basename(self.path)

        if self.name != baseName:
            self.path = os.path.join(self.path, self.name)
        with open(self.path, "w") as file:
            file.write("")
        return self