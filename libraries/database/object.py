from libraries.utils import Object
from typing import Any


class DatabaseObject(Object):
    """
    Base class for database objects like tables and columns.
    Provides common properties and methods for database operations.
    
    Args:
        database: Database connection object
        name: Name of the database object
        joiner: SQL boolean operator for joining conditions ('AND' or 'OR')
        TEST: If True, operations will be logged but not executed
    """

    def __init__(
            self, 
            database: Any | None = None, 
            name: str | None = None, 
            joiner: str = 'AND', 
            TEST: bool = False) -> None:
        super().__init__(TEST=TEST)
        self._name = name
        self._database = database
        self._joiner = joiner
        self._selection = []
        self._conditions = []
        self._old_name = None

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def database(self) -> Any:
        """
        Get the database connection object.
        
        Returns:
            The database connection object
        """
        return self._database
    
    @property
    def databaseName(self) -> str:
        """
        Get the name of the associated database.
        
        Returns:
            The database name
        """
        return self._database.name

    @property
    def joiner(self) -> str:
        """
        Get the SQL boolean operator for joining conditions.
        
        Returns:
            The SQL join operator with proper spacing
            
        Raises:
            ValueError: If joiner is not 'AND' or 'OR'
        """
        joiner = self._joiner.upper().strip()
        if joiner not in ['AND', 'OR']:
            raise ValueError('joiner must be either "and" or "or"')
        return f' {joiner} '
    
    @joiner.setter
    def joiner(self, value: str) -> 'DatabaseObject':
        """
        Set the SQL boolean operator for joining conditions.
        
        Args:
            value: The boolean operator ('AND' or 'OR')
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If value is not 'and' or 'or'
        """
        if value.lower() not in ['and', 'or']:
            raise ValueError('joiner must be either "and" or "or"')
        self._joiner = value
        return self

    @property
    def selection(self) -> list:
        """
        Get the list of selected columns.
        
        Returns:
            List of selected column names or expressions
        """
        return self._selection
    
    @property
    def conditions(self) -> list:
        """
        Get the list of SQL conditions.
        
        Returns:
            List of SQL WHERE conditions
        """
        return self._conditions
    
    # =============================================================================
    # Methods
    # =============================================================================

    def reset(self) -> 'DatabaseObject':
        """
        Reset the database object to its initial state.
        
        Returns:
            Self for method chaining
        """
        self._selection = []
        self._conditions = []
        self._joiner = 'and'
        return self