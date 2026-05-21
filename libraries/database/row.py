from libraries.database.object import DatabaseObject
from libraries.dates import dates
from typing import Any, Tuple
from libraries.utils import is_date_string, is_time_string


class Row(DatabaseObject):
    """
    Represents a row in a database table with CRUD operations.
    Provides methods for storing, retrieving, and manipulating row data.
    
    Args:
        database: Database connection object
        tablename: Name of the table this row belongs to
        foreignKeys: List of foreign key column names
    """

    def __init__(self, database: Any | None = None, tablename: str | None = None, 
                 foreignKeys: list[str] | None = None) -> None:
        super().__init__(database)
        self._values = dict()
        self._foreignKeys = foreignKeys if foreignKeys else list()
        self._tablename = tablename

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def columns(self) -> list:
        """
        Get the columns for this row.
        
        Returns:
            List of column names
        """
        return self._columns

    @columns.setter
    def columns(self, value: str | list[str] | tuple[str, ...]) -> 'Row':
        """
        Set columns for this row.
        
        Args:
            value: Column name or list of column names
            
        Returns:
            Self for method chaining
        """
        if not isinstance(value, list):
            if isinstance(value, tuple):
                value = list(value)
            else:
                value = [value]
        self._columns += value
        return self

    @property
    def tablename(self) -> str | None:
        """
        Get the table name this row belongs to.
        
        Returns:
            The table name
        """
        return self._tablename 

    @property
    def foreignKeys(self) -> list[str]:
        """
        Get the foreign key column names.
        
        Returns:
            List of foreign key column names
        """
        return self._foreignKeys

    @property
    def foreignKey(self) -> Tuple[str, Any] | None:
        """
        Get the first valid foreign key and its value.
        
        Returns:
            Tuple of (column_name, value) or None if no valid foreign key exists
        """
        if not self._foreignKeys:
            return None
        else:
            for key in self.foreignKeys:
                if key not in self._values:
                    continue
                value = self._values[key]
                if value:
                    return key, value
        return None

    @foreignKey.setter
    def foreignKey(self, columnName: str) -> 'Row':
        """
        Add a foreign key column.
        
        Args:
            columnName: The column name to add as a foreign key
            
        Returns:
            Self for method chaining
        """
        self._foreignKeys.append(columnName)
        return self

    # =============================================================================
    # Value Access Methods
    # =============================================================================
    
    def hasValue(self, column: str) -> bool:
        """
        Check if a column has a value set.
        
        Args:
            column: The column name to check
            
        Returns:
            True if the column has a value, False otherwise
        """
        return column in self._values

    def _set(self, column: str, value: Any) -> 'Row':
        """
        Set a column value with database formatting.
        
        Args:
            column: The column name
            value: The value to set
            
        Returns:
            Self for method chaining
        """
        if value is None:
            if column in self._values:
                del self._values[column]
        self._values[column] = self._sort_value_for_database(value)
        return self

    def _get(self, column: str) -> Any:
        """
        Get a column value with Python type conversion.
        
        Args:
            column: The column name
            
        Returns:
            The column value
            
        Raises:
            KeyError: If the column is not found
        """
        try:
            return self._get_value_for_python(self._values[column])
        except KeyError:
            raise KeyError(f"COLUMN {column} NOT LOADED/SET from/for {self._tablename}.")

    def set(self, load: dict | None) -> 'Row':
        """
        Set multiple column values from a dictionary.
        
        Args:
            load: Dictionary of column:value pairs or None
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If load is not a dictionary
        """
        if not load:
            return self
        if not isinstance(load, dict):
            raise ValueError("load must be a dictionary")
        if self.tablename in load:
            load = load[self.tablename]
        self._values = load
        return self
    
    def get(self) -> dict:
        """
        Get all column values.
        
        Returns:
            Dictionary of column:value pairs
        """
        return self._values

    # =============================================================================
    # Database Operations
    # =============================================================================
    
    def insert(self) -> int:
        """
        Insert this row into the database.
        
        Returns:
            The ID of the inserted row
            
        Raises:
            ValueError: If no values are set
        """
        columns = ', '.join(list(self._values.keys()))
        values = list(self._values.values())
        insertions = ', '.join(['?' for _ in values])
        return self.database.runSQL(f'insert into {self._tablename} ({columns}) values ({insertions})', values).commit().lastRowId()

    def update(self) -> 'Row':
        """
        Update this row in the database.
        
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If no ID or foreign key is set to identify the row
        """
        condition = self._get_condition()
        if not condition:
            raise ValueError(f"Cannot update row in {self._tablename}: no ID or ForeignKey specified.")
        self.lastUpdated = dates.now()
        updates = [f'{column} = ?' for column in self._values.keys()]
        values = list(self._values.values())
        self.database.runSQL(f'update {self._tablename} set {", ".join(updates)} where {condition}', values).commit()
        return self.reset()
    
    def delete(self) -> 'Row':
        """
        Delete this row from the database.
        
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If no condition can be built to identify the row
        """
        condition = self._get_condition()
        if not condition:
            raise ValueError(f"Cannot delete from {self._tablename}: no condition specified.")
        self.database.runSQL(f'delete from {self._tablename} where {condition}').commit()
        return self.reset()

    def exists(self) -> bool:
        """
        Check if this row exists in the database.
        
        Returns:
            True if the row exists, False otherwise
        """
        return bool(self._get_from_database())

    def reset(self) -> 'Row':
        """
        Reset this row's state.
        
        Returns:
            Self for method chaining
        """
        self._columns = list()
        return self
    
    # =============================================================================
    # Helper Methods
    # =============================================================================
    
    def _get_from_database(self) -> tuple | None:
        """
        Fetch this row from the database.
        
        Returns:
            The row data or None if not found
            
        Raises:
            ValueError: If no condition can be built to identify the row
        """
        condition = self._get_condition()
        if not condition:
            raise ValueError(f"Cannot get from {self._tablename}: no ID or ForeignKey to specify search condition.")        
        result = self.database.runSQL(f'select id from {self._tablename} where {condition}')._cursor.fetchone()
        if result:
            self.id = result[0]
        return result

    def _get_condition(self) -> str | None:
        """
        Build a SQL condition to identify this row.
        
        Returns:
            SQL condition string or None if no identifying information is available
        """
        try:
            if self.id:
                return f'id = {self.id}'
        except KeyError:
            pass
        foreignKey = self.foreignKey
        if foreignKey:
            return f'{foreignKey[0]} = {foreignKey[1]}'
        else:
            return None

    def getForeignKeyId(self, ForeignKeyRowObject: type | None = None, 
                        foreignKeyRowAttr: str | None = None, 
                        foreignKeyRowAttrValue: Any = None) -> int | None:
        """
        Get the ID of a related row by attribute value.
        
        Args:
            ForeignKeyRowObject: The row class to instantiate
            foreignKeyRowAttr: The attribute to check
            foreignKeyRowAttrValue: The value the attribute should have
            
        Returns:
            The ID of the related row or None if not found
        """
        if ForeignKeyRowObject is None or foreignKeyRowAttr is None or foreignKeyRowAttrValue is None:
            return None
        row = ForeignKeyRowObject(self.database)
        setattr(row, foreignKeyRowAttr, foreignKeyRowAttrValue)
        if row.exists():
            return row.id
        else:
            return None
    
    @staticmethod
    def _sort_value_for_database(value: Any) -> Any:
        """
        Format a value for database storage.
        
        Args:
            value: The value to format
            
        Returns:
            The formatted value
        """
        if dates.isDatetimeTime(value):
            return dates.toIsoFormat(value)
        return value

    @staticmethod
    def _get_value_for_python(value: Any) -> Any:
        """
        Convert a database value to an appropriate Python type.
        
        Args:
            value: The value to convert
            
        Returns:
            The converted value
        """
        if value == '':
            return None
        if isinstance(value, str):
            if is_date_string(value):
                return dates.date(*tuple(value.split('-')))
            if is_time_string(value):
                return dates.time(*tuple(value.split(':')))
        return value
        
    def __str__(self) -> str:
        """
        Get a string representation of this row.
        
        Returns:
            A formatted string with all values
        """
        string = ''
        for key, value in self._values.items():
            string += f'\n\t\t{self.__class__.__name__.lower()}.{key}: {value}'
        return string