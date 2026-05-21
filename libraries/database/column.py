from typing import Any, List, Optional, Tuple, Union, Self, Literal
from libraries.database.functions import sort_value_for_database, sort_values_for_database
from libraries.utils import logerror, toList
from libraries.database.object import DatabaseObject

# Define Literal types for column-related constraints
DataType = Literal['integer', 'boolean', 'real', 'text', 'blob', 'null']
ForeignKeyAction = Literal['cascade', 'set null', 'set default', 'restrict', 'no action']


class Column(DatabaseObject):
    """
    Represents a column in a database table with methods for defining properties,
    creating SQL conditions, and generating SQL statements.
    """

    _dataTypes = [
        'integer',
        'boolean',
        'real',
        'text',
        'blob',
        'null'
    ]
    _defaults = [
        'null',
        0.0,
        0,
        '""'
    ]

    def __init__(self, database: Any | None = None, name: str | None = None, 
                 tablename: str | None = None, dataType: DataType = 'text') -> None:
        """
        Initialize a new database column.
        
        Args:
            database: The database connection
            name: The name of the column
            tablename: The name of the table this column belongs to
            dataType: The SQL data type of this column
        """
        super().__init__(database=database, name=name)
        self._tablename = tablename
        self._dataType = dataType
        self._isPrimaryKey = False
        self._isAutoIncrement = False
        self._isUnique = False
        self._isNotNull = False
        self._defaultValue = None
        self._isForeignKey = False
        self._references = None
        self._onUpdate: ForeignKeyAction = 'cascade'
        self._onDelete: ForeignKeyAction = 'cascade'
        self._value = None
        self._orders = []
        self._asc = False
        self._desc = False

    # Core database operations
    def create(self) -> 'Column':
        """
        Create this column in the database table.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self.database.runSQL(f'alter table {self._tablename} add column {self.createStatement}')
        return self

    def delete(self) -> 'Column':
        """
        Delete this column from the database table.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self.database.runSQL(f'alter table {self._tablename} drop column {self._name}')
        return self

    def rename(self) -> 'Column':
        """
        Rename this column from its old name to the current name.
        
        Returns:
            Self: Returns the column instance for method chaining
            
        Raises:
            RuntimeError: If old name is not set, new name is None, or names are identical
        """
        if not self._old_name:
            return logerror('No old name to rename column from')
        if self._name is None:
            return logerror('Cannot rename column to a None value')
        if self._old_name == self._name:  
            return logerror('Old name and new name are the same; no change needed')
        
        self.database.runSQL(
            f'alter table {self._tablename} rename column {self._old_name} to {self._name}')
        return self

    # Core properties

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
    def value(self) -> Any:
        """
        Get the value of this column.
        
        Returns:
            Any: The column's value
        """
        return self._value

    @value.setter
    def value(self, value: Any) -> 'Column':
        """
        Set the value of this column.
        
        Args:
            value: The value to set
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._value = value
        return self

    @property
    def dataType(self) -> DataType:
        """
        Get the data type of this column.
        
        Returns:
            str: The SQL data type
        """
        return self._dataType

    @dataType.setter
    def dataType(self, value: str | type) -> 'Column':
        """
        Set the data type of this column.
        
        Args:
            value: The data type to set, can be SQL type string or Python type
            
        Returns:
            Self: Returns the column instance for method chaining
            
        Raises:
            RuntimeError: If the data type is invalid
        """
        if value not in self._dataTypes:
            if value == str():
                value = 'text'
            elif value == int():
                value = 'integer'
            elif value == bool(): 
                value = 'integer'
            elif value == 'boolean':
                value = 'integer'
            elif value == float(): 
                value = 'real'
            elif value == bytes():
                value = 'blob'
            else:
                return logerror(
                    f'Invalid data type {value} for column {self._name}')

        self._dataType = value
        return self

    @property
    def defaultValue(self) -> Any:
        """
        Get the default value for this column.
        
        Returns:
            Any: The default value
        """
        return self._defaultValue

    @defaultValue.setter
    def defaultValue(self, value: Any) -> 'Column':
        """
        Set the default value for this column.
        
        Args:
            value: The default value to set
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._defaultValue = value
        return self

    # SQL statement generation properties
    @property
    def selection(self) -> str:
        """
        Get the SQL selection string for this column.
        
        Returns:
            str: The column selection expression
        """
        return f'{self._tablename}.{self._name}'

    @property
    def conditions(self) -> str:
        """
        Get the SQL conditions string for this column.
        
        Returns:
            str: The conditions expression
        """
        joined = f'{self.joiner.join(self._conditions)}'
        if self.joiner.strip().lower() == 'or':
            return f'({joined})'
        return joined

    @property
    def order(self) -> str:
        """
        Get the SQL order by string for this column.
        
        Returns:
            str: The order by expression
        """
        return f'{self._tablename}.{self._name}'

    @conditions.setter
    def conditions(self, value: str | list[str]) -> 'Column':
        """
        Set the conditions for this column.
        
        Args:
            value: The condition or list of conditions to set
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.extend(toList(value)) 
        return self

    # Column property methods
    def autoIncrement(self) -> 'Column':
        """
        Mark this column as auto-incrementing.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._isAutoIncrement = True
        return self

    def foreignKey(self) -> 'Column':
        """
        Mark this column as a foreign key.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._isForeignKey = True
        return self

    def primaryKey(self) -> 'Column':
        """
        Mark this column as the primary key.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._isPrimaryKey = True
        return self

    def unique(self) -> 'Column':
        """
        Mark this column as requiring unique values.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._isUnique = True
        return self

    def notNull(self) -> 'Column':
        """
        Mark this column as not allowing NULL values.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._isNotNull = True
        return self

    def onUpdate(self, action: ForeignKeyAction) -> 'Column':
        """
        Set the on-update action for this foreign key column.
        
        Args:
            action: The action to take ('cascade', 'set null', 'set default', 'restrict', 'no action')
            
        Returns:
            Self: Returns the column instance for method chaining
            
        Raises:
            RuntimeError: If the action is invalid
        """
        if action not in ['cascade', 'set null', 'set default', 'restrict', 'no action']:
            return logerror(f'Invalid action {action} for onUpdate')
        self._onUpdate = action
        return self

    def onDelete(self, action: ForeignKeyAction) -> 'Column':
        """
        Set the on-delete action for this foreign key column.
        
        Args:
            action: The action to take ('cascade', 'set null', 'set default', 'restrict', 'no action')
            
        Returns:
            Self: Returns the column instance for method chaining
            
        Raises:
            RuntimeError: If the action is invalid
        """
        if action not in ['cascade', 'set null', 'set default', 'restrict', 'no action']:
            return logerror(f'Invalid action {action} for onDelete')
        self._onDelete = action
        return self

    def references(self, table: str, column: str) -> 'Column':
        """
        Set the referenced table and column for this foreign key column.
        
        Args:
            table: The referenced table name
            column: The referenced column name
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._references = (table, column)
        return self

    @property
    def createStatement(self) -> str:
        """
        Generate the SQL CREATE statement for this column.
        
        Returns:
            str: The SQL fragment for creating this column
        """
        statement = f'{self._name} {self._dataType}'
        if self._isPrimaryKey:
            statement += ' primary key'
        if self._isAutoIncrement:
            statement += ' autoincrement'
        if self._isUnique:
            statement += ' unique'
        if self._isNotNull:
            statement += ' not null'
      
        if self._defaultValue is not None:
            default = self._defaultValue
     
            if self._dataType in ('text', 'blob'):
                default = f'"{default}"'
    
        elif self._dataType == 'text':
            default = '""'
        elif self._dataType == 'integer':
            default = 0
        elif self._dataType == 'real':
            default = 0.0
        elif self._dataType == 'blob':
            default = '""'
        elif self._dataType == 'boolean':
            default = 0
        statement += f' default {default}'
        return statement

    @property
    def foreignKeyStatement(self) -> str:
        """
        Generate the SQL foreign key constraint statement for this column.
        
        Returns:
            str: The SQL fragment for the foreign key constraint
        """
        statement = ''
        if self._isForeignKey:
            statement += f'foreign key ({self._name})'
            referencedTable = self._references[0]
            referencedColumn = self._references[1]
            statement += f' references {referencedTable} ({referencedColumn})'
            if self._onDelete:
                statement += f' on delete {self._onDelete}'
            if self._onUpdate:
                statement += f' on update {self._onUpdate}'
        return statement

    # Condition methods
    def isGreaterThan(self, value: Any) -> 'Column':
        """
        Add a condition that the column value must be greater than the provided value.
        
        Args:
            value: The value to compare against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        if value is None:
            return self
        value = sort_value_for_database(value, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} > {value}")
        return self

    def isLessThan(self, value: Any) -> 'Column':
        """
        Add a condition that the column value must be less than the provided value.
        
        Args:
            value: The value to compare against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        if value is None:
            return self
        value = sort_value_for_database(value, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} < {value}")
        return self

    def isGreaterThanOrEqualTo(self, value: Any) -> 'Column':
        """
        Add a condition that the column value must be greater than or equal to the provided value.
        
        Args:
            value: The value to compare against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        if value is None:
            return self
        value = sort_value_for_database(value, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} >= {value}")
        return self

    def isLessThanOrEqualTo(self, value: Any) -> 'Column':
        """
        Add a condition that the column value must be less than or equal to the provided value.
        
        Args:
            value: The value to compare against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        if value is None:
            return self
        value = sort_value_for_database(value, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} <= {value}")
        return self

    def isEqualTo(self, value: Any) -> 'Column':
        """
        Add a condition that the column value must be equal to the provided value.
        
        Args:
            value: The value to compare against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        value = sort_value_for_database(value, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} = {value}")
        return self

    def isNull(self) -> 'Column':
        """
        Add a condition that the column value must be NULL.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} is null")
        return self

    def isNotNull(self) -> 'Column':
        """
        Add a condition that the column value must not be NULL.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} is not null")
        return self

    def isNotEqualTo(self, value: Any) -> 'Column':
        """
        Add a condition that the column value must not be equal to the provided value.
        
        Args:
            value: The value to compare against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        value = sort_value_for_database(value, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} != {value}")
        return self

    def isTrue(self) -> 'Column':
        """
        Add a condition that the column value must be TRUE (1).
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} = 1")
        return self

    def isFalse(self) -> 'Column':
        """
        Add a condition that the column value must be FALSE (0).
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} = 0")
        return self

    def isNotFalse(self) -> 'Column':
        """
        Add a condition that the column value must not be FALSE (not 0).
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} != 0")
        return self

    def isLike(self, value: str) -> 'Column':
        """
        Add a condition that the column value must contain the provided value.
        
        Args:
            value: The substring to search for
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} like '%{value}%'")
        return self

    def isNotLike(self, value: str) -> 'Column':
        """
        Add a condition that the column value must not contain the provided value.
        
        Args:
            value: The substring that should not be present
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} not like '%{value}%'")
        return self

    def isIn(self, values: List[Any]) -> 'Column':
        """
        Add a condition that the column value must be in the provided list of values.
        
        Args:
            values: The list of values to check against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        if not values:
            return self
        values = sort_values_for_database(values, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} in ({values})")
        return self

    def isNotIn(self, values: List[Any]) -> 'Column':
        """
        Add a condition that the column value must not be in the provided list of values.
        
        Args:
            values: The list of values to check against
            
        Returns:
            Self: Returns the column instance for method chaining
        """
        if not values:
            return self
        values = sort_values_for_database(values, self.dataType)
        self._conditions.append(f"{self._tablename}.{self._name} not in ({values})")
        return self

    def isEmpty(self) -> 'Column':
        """
        Add a condition that the column value must be an empty string.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} = ''")
        return self

    def isNotEmpty(self) -> 'Column':
        """
        Add a condition that the column value must not be an empty string.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._conditions.append(f"{self._tablename}.{self._name} != ''")
        return self

    def isNullEmptyOrFalse(self) -> 'Column':
        """
        Add conditions that the column value must be NULL, empty, or FALSE (0).
        Sets the joiner to 'or' for combining conditions.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self.joiner = 'or'
        self.isNull().isEmpty().isFalse()
        return self

    def isNotNullEmptyOrFalse(self) -> 'Column':
        """
        Add conditions that the column value must not be NULL, not empty, and not FALSE (0).
        Sets the joiner to 'and' for combining conditions.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self.joiner = 'and'
        self.isNotNull().isNotEmpty().isNotFalse()
        return self

    # Order methods
    def asc(self) -> 'Column':
        """
        Add ascending order for this column.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._orders[-1] += ' asc'
        return self

    def desc(self) -> 'Column':
        """
        Add descending order for this column.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._orders[-1] += ' desc'
        return self

    def reset(self) -> 'Column':
        """
        Reset this column's state, clearing orders and other settings.
        
        Returns:
            Self: Returns the column instance for method chaining
        """
        self._orders = []
        return super().reset()