import os
from libraries.utils import Object, logtest


class Symlink(Object):
    """
    Symlink class to manage symlinks in the file system.
    
    Args:
        path: Path to the symlink
        name: Name of the symlink
        TEST: If True, operations will be logged but not executed
    
    Raises:
        Exception: If the provided path does not exist
    """

    def __init__(
            self, 
            path: str, 
            name: str | None = None, 
            TEST: bool = False) -> None:
        super().__init__(name=name, TEST=TEST)
        if not os.path.exists(path):
            raise Exception(
                f"System Item not Found: the path {path} does not exist.")
        self.path = path

    # =============================================================================
    # Properties
    # =============================================================================
    
    @property
    def isFile(self) -> bool:
        """
        Check if the object is a file.
        
        Returns:
            Always False for Symlink objects
        """
        return False
    
    @property
    def isDirectory(self) -> bool:
        """
        Check if the object is a directory.
        
        Returns:
            Always False for Symlink objects
        """
        return False

    @property
    def path(self) -> str:
        """
        Get the symlink path.
        
        Returns:
            The path string
        """
        return self._get('path')

    @path.setter
    def path(self, value: str) -> None:
        """
        Set the path of the symlink.
        
        Args:
            value: The path to set
            
        Raises:
            FileNotFoundError: If the path does not exist
            ValueError: If the path is not a symlink
        """
        if not os.path.exists(value):
            raise FileNotFoundError(
                f"The path {value} does not exist.")
        
        if not os.path.islink(value):
            raise ValueError(
                f"The path {value} is not a symlink.")
        
        self._set('path', value)

    # =============================================================================
    # Operations
    # =============================================================================

    def delete(self) -> 'Symlink':
        """
        Delete the symlink.
        
        Returns:
            Self for method chaining
        """
        if self.TEST: 
            return logtest(f'delete Symlink @ {self.path}')
        os.unlink(self.path)
        return self