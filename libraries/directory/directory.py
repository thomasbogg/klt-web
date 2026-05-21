import os
from typing import Generator
from libraries.directory.file import File
from libraries.directory.symlink import Symlink
from libraries.utils import Object, logtest


class Directory(Object):
    """
    Directory class to manage directories in the file system.
    
    Args:
        path: Path to the directory
        name: Name of the directory
        TEST: If True, operations will be logged but not executed
    
    Raises:
        Exception: If the provided path does not exist
    """

    def __init__(
            self, 
            path: str | None = None, 
            name: str | None = None, 
            TEST: bool = False) -> None:
        super().__init__(name=name, TEST=TEST)
        #if not os.path.exists(path):
        #    raise Exception(
        #        f"System Item not Found: the path {path} does not exist.")
        self.path = path

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def isFile(self) -> bool:
        """
        Check if the object is a file.
        
        Returns:
            Always False for Directory objects
        """
        return False
    
    @property
    def isDirectory(self) -> bool:
        """
        Check if the object is a directory.
        
        Returns:
            Always True for Directory objects
        """
        return True

    @property
    def path(self) -> str:
        """
        Get the directory path.
        
        Returns:
            The path string
        """
        return self._get('path')

    @path.setter
    def path(self, value: str) -> None:
        """
        Set the directory path.
        
        Args:
            value: The path to set
        """
        self._set('path', value)

    @property
    def exists(self) -> bool:
        """
        Check if the directory exists.
        
        Returns:
            True if the directory exists, False otherwise
        """
        return os.path.exists(self.path)

    # =============================================================================
    # Content Methods
    # =============================================================================

    @property
    def contents(self) -> Generator[Object, None, None]:
        """
        Get all items in the directory.
        
        Yields:
            Files, symlinks, and subdirectories as appropriate objects
        """
        for item in os.listdir(self.path):
            path = os.path.join(self.path, item)
            if os.path.isfile(path):
                yield self.file(path=path, name=item)
            elif os.path.islink(path):
                yield self.symlink(path=path, name=item)
            elif os.path.isdir(path):
                yield self.subdirectory(path=path, name=item)
    
    @property
    def files(self) -> Generator[File, None, None]:
        """
        Get all files in the directory.
        
        Yields:
            File objects for each file in the directory
        """
        for item in os.listdir(self.path):
            path = os.path.join(self.path, item)
            if os.path.isfile(path):
                yield self.file(path=path, name=item)
    
    @property
    def subdirectories(self) -> Generator['Directory', None, None]:
        """
        Get all subdirectories in the directory.
        
        Yields:
            Directory objects for each subdirectory
        """
        for item in os.listdir(self.path):
            path = os.path.join(self.path, item)
            if os.path.isdir(path):
                yield self.subdirectory(path=path, name=item)
    
    # =============================================================================
    # Operations
    # =============================================================================
    
    def delete(self) -> 'Directory':
        """
        Delete the directory and all its contents.
        
        Returns:
            Self for method chaining
        """
        for item in self.contents:
            item.delete()
        if self.TEST: 
            logtest(f'Would delete Directory @ {self.path}')
        else: 
            os.rmdir(self.path)

        return self

    # =============================================================================
    # Factory Methods
    # =============================================================================

    def subdirectory(self, path: str, name: str | None = None) -> 'Directory':
        """
        Create a Directory object for a subdirectory.
        
        Args:
            path: Path to the subdirectory
            name: Name of the subdirectory
            
        Returns:
            A Directory object
        """
        return Directory(path, name, self.TEST)
    
    def file(self, path: str, name: str | None = None) -> File:
        """
        Create a File object.
        
        Args:
            path: Path to the file
            name: Name of the file
            
        Returns:
            A File object
        """
        extension = os.path.splitext(name)[-1]
        return File(path, name, extension, self.TEST)

    def symlink(self, path: str, name: str | None = None) -> Symlink:
        """
        Create a Symlink object.
        
        Args:
            path: Path to the symlink
            name: Name of the symlink
            
        Returns:
            A Symlink object
        """
        return Symlink(path, name, self.TEST)