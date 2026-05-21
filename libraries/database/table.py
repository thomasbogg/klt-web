from libraries.database.column import Column
from libraries.database.object import DatabaseObject
from typing import Self, Any
from libraries.utils import logerror, toList


class Table(DatabaseObject):
    """
    Represents a database table with operations for creation, modification, and querying.
    Provides an abstraction layer for SQL operations on tables.
    """

    def __init__(self, database: Any | None = None, columns: dict[str, Column] | None = None, name: str | None = None):
        """
        Initialize a new Table instance.
        
        Args:
            database: The database connection this table belongs to
            columns: Dictionary of column objects with column names as keys
            name: The name of the table in the database
        """
        super().__init__(database=database, name=name)
        self._columns = columns if columns is not None else {}
        self._conditions: dict[str, Column] = {}
        self._selection: dict[str, Column] = {}
        self._orders: dict[str, Column] = {}
        self._select = False
        self._where = False
        self._order = False
        self._foreignKeys = list()
        self._isPrimaryTable = False
        self._old_name = None
    
    # =============================================================================
    # Table Operations
    # =============================================================================

    def create(self) -> Self:
        """
        Creates the table in the database if it does not already exist.
        
        Returns:
            Self: Returns the table instance for method chaining
            
        Raises:
            RuntimeError: If the table has no name
        """
        if not self._name:
            return logerror('Cannot create a Table with no name')
        sql = f'create table if not exists {self._name}'
        if self.columns:
            sql += self._colums_creation_statement()
        self.database.runSQL(sql)
        return self

    def delete(self) -> Self:
        """
        Deletes the table from the database if it exists.
        
        Returns:
            Self: Returns the table instance for method chaining
        """
        self.database.runSQL(f'drop table if exists {self.fullName}')
        return self
    
    def add(self, name: str | None = None, dataType: str | None = None, object: Column | None = None) -> Self:
        """
        Adds a new column to an existing table.
        
        Args:
            name: Name of the column to add
            dataType: SQL data type of the column
            object: Existing Column object to use instead of specifying name and dataType
            
        Returns:
            Self: Returns the table instance for method chaining
            
        Raises:
            RuntimeError: If the table has no name or the column parameters are invalid
        """
        if not self._name:
            return logerror('Cannot add a column to a table with no name')
        
        # Get or create the column object
        if object:
            column = object
        elif name and dataType:
            column = Column(name=name, tablename=self._name, dataType=dataType)
        else:
            return logerror('Must provide either a Column object or a column name and data type')
        
        # Add the column to the database
        sql = f'ALTER TABLE {self.name} ADD COLUMN {column.createStatement}'
        self.database.runSQL(sql).commit()
        
        # Add the column to the internal columns dictionary
        self._columns[column.name] = column
        return self
    
    def insert(self, values: list[Any]) -> Self:
        """
        Inserts values into the table.
        
        Args:
            values: A list of values to insert into the table
            
        Returns:
            Self: Returns the table instance for method chaining
        """
        placeholders = ', '.join(['?' for _ in values])
        self.database.runSQL(f'insert into {self.fullName} values ({placeholders})', values)
        return self

    def update(self, values: list[Any]) -> Self:
        """
        Updates existing records in the table.
        
        Args:
            values: List of values to update, where the last value is the ID of the record to update
            
        Returns:
            Self: Returns the table instance for method chaining
        """
        columns = ', '.join([f'{column} = ?' for column in self.columns])
        self.database.runSQL(f'update {self.fullName} set {columns} where id = ?', values)
        return self

    def rename(self) -> Self:
        """
        Renames the table from its old name to the current name.
        
        Returns:
            Self: Returns the table instance for method chaining
            
        Raises:
            RuntimeError: If old_name is not set, new name is None, or old and new names are identical
        """
        if not hasattr(self, '_old_name'):
            return logerror('No new/old name to rename Table to/from')
   
        if self._name is None:
            return logerror('Cannot rename Table to a None value')
   
        if self._old_name == self._name:
            return logerror('Old name and new name are the same; no change needed')
   
        self.database.runSQL(f'alter table {self.databaseName}.{self._old_name} rename to {self.fullName}')
        return self

    def reset(self) -> Self:
        """
        Resets the table to its initial state, clearing columns and query state.
        
        Returns:
            Self: Returns the table instance for method chaining
        """
        self._columns = {}
        self._conditions = {}
        self._selection = {}
        self._select = False
        self._where = False
        self._order = False
        return self

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def name(self) -> str:
        """
        Gets the name of the table.
        
        Returns:
            str: The name of the table
        """
        return self._name
    
    @name.setter
    def name(self, value: str) -> Self:
        """
        Sets the name of the table.
        
        Args:
            value: The new name for the table
            
        Returns:
            Self: Returns the table instance for method chaining
        """
        self._name = value
        return self

    @property
    def isPrimaryTable(self) -> bool:
        """
        Whether this table is the primary table in a query.
        
        Returns:
            bool: True if this is the primary table, False otherwise
        """
        return self._isPrimaryTable

    @isPrimaryTable.setter
    def isPrimaryTable(self, value: bool) -> Self:
        """
        Sets whether this table is the primary table in a query.
        
        Args:
            value: True if this should be the primary table, False otherwise
            
        Returns:
            Self: Returns the table instance for method chaining
        """
        self._isPrimaryTable = value
        return self

    @property
    def fullName(self) -> str:
        """
        Gets the fully qualified name of the table including database name.
        
        Returns:
            str: The full table name in format 'database.table'
        """
        return f'{self.databaseName}.{self._name}'
    
    @property
    def columns(self) -> list[Column]:
        """
        Gets all columns in this table.
        
        Returns:
            list[Column]: List of Column objects in this table
        """
        return list(self._columns.values())
    
    @columns.setter
    def columns(self, objects: list[Column] | Column | None) -> Self:
        """
        Sets columns for this table.
        
        Args:
            objects: Column object or list of Column objects to add to this table
            
        Returns:
            Self: Returns the table instance for method chaining
        """
        if not objects:
            return self
        
        objects = toList(objects)
        self._columns.update({object.name: object for object in objects})
        return self

    @property
    def foreignKeys(self) -> list[Column]:
        """
        Gets all foreign key columns in this table.
        
        Returns:
            list[Column]: List of foreign key Column objects
        """
        return self._foreignKeys
    
    @foreignKeys.setter
    def foreignKeys(self, value: str | Column | list[str | Column]) -> Self:
        """
        Sets foreign keys for this table.
        
        Args:
            value: Column name, Column object, or list of these to set as foreign keys
            
        Returns:
            Self: Returns the table instance for method chaining
        """
        if isinstance(value, str):
            value = [value]
        elif isinstance(value, Column):
            value = [value]
        elif not isinstance(value, list):
            value = list(value)

        self._foreignKeys.extend(value)
        return self
    
    @property
    def conditions(self) -> str:
        """
        Gets the SQL WHERE conditions for this table.
        
        Returns:
            str: SQL WHERE conditions string
        """
        joined = f'{self.joiner.join([column.conditions for column in self._conditions.values()])}'
        if self.joiner.strip().lower() == 'or':
            joined = f'({joined})'
        return joined

    @property
    def selection(self) -> list[str]:
        """
        Gets the SQL SELECT columns for this table.
        
        Returns:
            list[str]: List of column selection strings
        """
        return [column.selection for column in self._selection.values()]

    @property
    def orderBy(self) -> str:
        """
        Gets the SQL ORDER BY clause for this table.
        
        Returns:
            str: SQL ORDER BY clause string
        """
        return f'{", ".join([column.order for column in self._orders.values()])}'
    
    # =============================================================================
    # Query Building Methods
    # =============================================================================
    
    def select(self) -> Self:
        """
        Enters SELECT mode for building a query.
        
        Returns:
            Self: Returns the table instance for method chaining
        """
        self._select = True
        self._where = False
        self._order = False
        return self
    
    def where(self) -> Self:
        """
        Enters WHERE mode for building a query.
        
        Returns:
            Self: Returns the table instance for method chaining
        """
        self._where = True
        self._select = False
        self._order = False
        return self

    def order(self) -> Self:
        """
        Enters ORDER BY mode for building a query.
        
        Returns:
            Self: Returns the table instance for method chaining
        """
        self._order = True
        self._select = False
        self._where = False
        return self

    def joinStatement(self, table: Self) -> list[str]:
        """
        Generates SQL JOIN statements for joining with another table.
        
        Args:
            table: The table to join with
            
        Returns:
            list[str]: List of SQL JOIN statements
        """
        return []
    
    # =============================================================================
    # Helper Methods
    # =============================================================================
    
    def _column(self, name: str | None = None, dataType: str | None = None, 
                object: Column | None = None) -> Column | Self:
        """
        Internal method to get or create a column.
        
        Args:
            name: Name of the column
            dataType: SQL data type of the column
            object: Existing Column object to use instead of creating a new one
            
        Returns:
            Column or Table: Column object if in where/order mode, otherwise the Table instance
        """
        if object:
            name = object.name
        else:
            object = Column(name=name, tablename=self._name, dataType=dataType)

        if self._select:
            if name not in self._selection:
                self._selection[name] = object
            if 'id' not in self._selection:
                self._selection['id'] = Column(name='id', tablename=self._name, dataType='integer')
            return self

        elif self._where:
            if name not in self._conditions:
                self._conditions[name] = object
            return self._conditions[name]

        elif self._order:
            if name not in self._orders:
                self._orders[name] = object
            return self._orders[name]

        else:
            if name not in self._columns:
                self._columns[name] = object
            return self._columns[name]
    
    def _colums_creation_statement(self) -> str:
        """
        Generates the SQL statement for creating columns in the
        table.
        
        Returns:
            str: SQL statement for column creation
        """   
        sql = ' ('
        sql += ', '.join([column.createStatement for column in self._columns.values()])
        if self.foreignKeys:
            sql += ', '
            sql += ', '.join([column.foreignKeyStatement for column in self.foreignKeys])
        sql += ');'
        return sql