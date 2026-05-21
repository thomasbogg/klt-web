from libraries.utils import logdivider, logerror, logtest, sublog
from libraries.database.table import Table
import sqlite3
from typing import Any


class Database:
    """
    SQLite database wrapper with fluent interface for building and executing SQL queries.
    """
    
    # Initialization and core properties
    def __init__(self, path: str | None = None, name: str | None = None, 
                 tables: dict[str, Table] | None = None, TEST: bool = False) -> None:
        """
        Initialize a new Database instance.
        
        Args:
            path: Path to the SQLite database file
            name: Name of the database
            tables: Dictionary of tables with table names as keys
            TEST: Flag to indicate if in test mode (prevents actual file operations)
        """
        self._path = path
        self._connection = None
        self._cursor = None
        self.TEST = TEST
        self._name = name
        self._tables = tables if tables is not None else {}

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def name(self) -> str | None:
        """
        Get the database name.
        
        Returns:
            The name of the database
        """
        return self._name
    
    @name.setter
    def name(self, value: str) -> 'Database':
        """
        Set the database name.
        
        Args:
            value: The new name for the database
            
        Returns:
            The Database instance for method chaining
        """
        self._name = value
        return self
    
    @property
    def path(self) -> str | None:
        """
        Get the database file path.
        
        Returns:
            The path to the database file
        """
        return self._path

    @path.setter
    def path(self, value: str) -> 'Database':
        """
        Set the database file path.
        
        Args:
            value: The new path for the database file
            
        Returns:
            The Database instance for method chaining
        """
        self._path = value
        return self
    
    # =============================================================================
    # Table Management
    # =============================================================================
    
    @property
    def tables(self) -> list[Table]:
        """
        Get all tables in the database.
        
        Returns:
            A list of Table objects
        """
        return list(self._tables.values())

    @tables.setter
    def tables(self, value: Table | list[Table] | tuple[Table, ...]) -> 'Database':
        """
        Set tables for the database.
        
        Args:
            value: A single Table object or a list/tuple of Table objects
            
        Returns:
            The Database instance for method chaining
        """
        if not isinstance(value, list):
            if isinstance(value, tuple):
                value = list(value)
            else:
                value = [value]
        self._tables.update({table.name: table for table in value})
        return self

    def _table(self, name: str | None = None) -> Table:
        """
        Get or create a table with the given name.
        
        Args:
            name: The name of the table
            
        Returns:
            The Table object for the given name
        """
        if name not in self._tables:
            tableObject = Table(self, name=name)
            self._tables[name] = tableObject
        return self._tables[name]

    def reset(self) -> 'Database':
        """
        Reset the database state by clearing the tables cache.
        
        Returns:
            The Database instance for method chaining
        """
        self._tables = {}
        return self

    # =============================================================================
    # Connection Management
    # =============================================================================

    @property
    def connection(self) -> sqlite3.Connection | None:
        """
        Get the SQLite connection object.
        
        Returns:
            The current SQLite connection or None if not connected
        """
        return self._connection
    
    @property
    def cursor(self) -> sqlite3.Cursor | None:
        """
        Get the SQLite cursor object.
        
        Returns:
            The current SQLite cursor or None if not connected
        """
        return self._cursor

    def connect(self) -> 'Database':
        """
        Connect to the SQLite database.
        
        Returns:
            The Database instance for method chaining
        """
        self._connection = sqlite3.connect(self._path)
        self._cursor = self._connection.cursor()
        return self
    
    def close(self) -> 'Database':
        """
        Close the database connection.
        
        Returns:
            The Database instance for method chaining
        """
        self._connection.close()
        self._connection = None
        self._cursor = None
        return self
    
    def isConnected(self) -> bool:
        """
        Check if the database is connected.
        
        Returns:
            True if connected, False otherwise
        """
        return self._cursor is not None

    def delete(self) -> 'Database':
        """
        Delete the database file.
        In test mode, logs but doesn't actually delete the file.
        
        Returns:
            The Database instance for method chaining
        """
        if not self.TEST:
            if self._connection:
                self._connection.close()
            import os
            os.remove(self._path)
        else:
            logtest(f'database {self._name} would be deleted')
        return self

    # =============================================================================
    # SQL Execution
    # =============================================================================

    def runSQL(self, sql: str, values: tuple | dict[str, Any] | None = None) -> 'Database':
        """
        Execute an SQL statement.
        
        Args:
            sql: The SQL statement to execute
            values: Parameter values for the SQL statement
            
        Returns:
            The Database instance for method chaining
        """
        try:
            if values is None:
                if self.TEST:
                    self.logsql(sql)
                self._cursor.execute(sql)
            else:
                if self.TEST:
                    self.logsql(sql, values)
                else:
                    self._cursor.execute(sql, values)
        except Exception as e: 
            self.logsql(sql, values, error=e)
        return self.reset()

    def logsql(self, sql: str, values: Any | None = None, error: Exception | None = None) -> 'Database':
        """
        Log SQL statements for debugging or testing.
        
        Args:
            sql: The SQL statement
            values: Parameter values for the SQL statement
            error: Optional exception if the SQL execution failed
            
        Returns:
            The Database instance for method chaining
        """
        logdivider()
        if self.TEST:
            logtest('write sql statement:')
        else:
            logerror(f'sql statement executed without success: {error}')
        sublog(f'{sql}')
        if self.TEST and values is not None:
            sublog('insert VALUES:')
            sublog(values)
        return self

    # =============================================================================
    # Transaction Management
    # =============================================================================

    def commit(self) -> 'Database':
        """
        Commit the current transaction.
        
        Returns:
            The Database instance for method chaining
        """
        self._connection.commit()
        return self

    def rollback(self) -> 'Database':
        """
        Roll back the current transaction.
        
        Returns:
            The Database instance for method chaining
        """
        self._connection.rollback()
        return self

    def lastRowId(self) -> int:
        """
        Get the row ID of the last inserted row.
        
        Returns:
            The ID of the last inserted row
        """
        return self._cursor.lastrowid

    # =============================================================================
    # Query Building and Data Fetching
    # =============================================================================

    def _fetch_SQL(self) -> tuple[str, list[str]]:
        """
        Generate the SQL statement for fetching data.
        
        Returns:
            A tuple containing the SQL statement and the selection columns
        """
        selection: list[str] = list()
        conditions: list[str] = list()
        orders: list[str] = list()
        tables: list[Table] = list()
        tablenames = [table._name for table in self.tables]
        joins: list[str] = list()

        for table in self.tables:
            sel = table.selection
            conds = table.conditions
            order = table.orderBy

            if sel:
                selection.extend(sel)
                tables.append(table)
            if conds:
                conditions.append(conds)
            if order:
                orders.append(order)

            joins += table.joinStatement(tablenames)
            table.reset()

        sql = f"SELECT {', '.join(selection)} "
        sql += f"FROM {[table._name for table in tables if table._isPrimaryTable][0]} "
        if joins:
            sql += f"{' '.join(joins)}"
        if conditions:
            sql += f" WHERE {' AND '.join(conditions)}"
        if orders:
            sql += f" ORDER BY {', '.join(orders)}"

        return sql, selection

    def _load_fetched(self, loadedObject: type | None, load: tuple, selection: list[str]) -> dict[str, dict[str, Any]] | Any:
        """
        Load fetched data into a dictionary or object.
        
        Args:
            loadedObject: Optional class to instantiate with the fetched data
            load: The tuple of values fetched from the database
            selection: List of column names in the format "table.column"
            
        Returns:
            A nested dictionary or an instance of loadedObject with the fetched data
        """
        result = {}
        for i, attr in enumerate(selection):
            table, attr = attr.split('.')
            if table not in result:
                result[table] = {}
            result[table][attr] = load[i]
        if loadedObject:
            return loadedObject(self).set(result)
        return result

    def fetchone(self, loadedObject: type | None = None) -> dict[str, dict[str, Any]] | Any | None:
        """
        Fetch a single row from the result of the last query.
        
        Args:
            loadedObject: Optional class to instantiate with the fetched data
            
        Returns:
            A dictionary with the fetched data, an instance of loadedObject, or None if no data
        """
        sql, selection = self._fetch_SQL()
        load = self.runSQL(sql)._cursor.fetchone()
        if not load:
            return None
        return self._load_fetched(loadedObject, load, selection)
    
    def fetchall(self, loadedObject: type | None = None) -> list[dict[str, dict[str, Any]] | Any]:
        """
        Fetch all rows from the result of the last query.
        
        Args:
            loadedObject: Optional class to instantiate with the fetched data
            
        Returns:
            A list of dictionaries or instances of loadedObject
        """
        sql, selection = self._fetch_SQL()
        objects = list()
        for load in self.runSQL(sql)._cursor.fetchall():
            objects.append(self._load_fetched(loadedObject, load, selection))
        return objects