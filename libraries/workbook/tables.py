from libraries.utils import Object
from typing import Self


class Table(Object):

    class Column(Object):

        def __init__(self, **kwargs) -> None:
            super().__init__(load=kwargs)
            self._subcolumns = list()

        def subcolumn(self, **kwargs) -> Self:
            """
            Add subcolumn to the current column.
        
            Args:
                kwargs: Keyword arguments containing subcolumn details such as 'name'
            """
            layer = self.layer + 1
            self._subcolumns.append(Table.Column(layer=layer, **kwargs))
            return self
        
        @property
        def subcolumns(self) -> list['Table.Column']:
            """
            Get the subcolumns of the column.
        
            Returns:
                list: List of subcolumns
            """
            return self._subcolumns

        @property
        def hasSubcolumns(self) -> bool:
            """
            Check if the column has subcolumns.

            Returns:
                bool: True if the column has subcolumns, False otherwise
            """
            return len(self._subcolumns) > 0

        @property
        def totalSize(self) -> int:
            """
            Get the total size of the column including subcolumns.
        
            Returns:
                int: Total size of the column
            """
            total = 1
            if self.hasSubcolumns:
                total = 0
                for subcolumn in self.subcolumns:
                    total += subcolumn.totalSize
            return total
        
        @property
        def dataType(self) -> str:
            """
            Get the data type of the column.
        
            Returns:
                str: Data type of the column
            """
            return self._get('dataType')

        @dataType.setter
        def dataType(self, value: str) -> None:
            """
            Set the data type of the column.
        
            Args:
                value: New data type for the column
            """
            if not isinstance(value, str):
                raise TypeError("dataType must be a string")
            self._set('dataType', value)

        @property
        def dataKey(self) -> str:
            """
            Get the data key of the column.
        
            Returns:
                str: Data key of the column
            """
            return self._get('dataKey')
        
        @dataKey.setter
        def dataKey(self, value: str) -> None:
            """
            Set the data key of the column.
        
            Args:
                value: New data key for the column
            """
            if not isinstance(value, str):
                raise TypeError("dataKey must be a string")
            self._set('dataKey', value)

        @property
        def layer(self):
            return self._get('layer')
        
        @layer.setter
        def layer(self, value):
            self._set('layer', value)

        @property
        def width(self) -> int:
            """
            Get the width of the column.
        
            Returns:
                int: Width of the column
            """
            return self._get('width')
        
        @width.setter
        def width(self, value: int) -> None:
            """
            Set the width of the column.
        
            Args:
                value: New width for the column
            """
            if not isinstance(value, int):
                raise TypeError("width must be an integer")
            if value < 1:
                raise ValueError("width must be greater than or equal to 1")
            self._set('width', value)

        @property
        def layerHeight(self) -> int:
            """
            Get the height of the column layer.
        
            Returns:
                int: Height of the column layer
            """
            return self._get('layerHeight') 
        
        @layerHeight.setter
        def layerHeight(self, value: int) -> None:
            """
            Set the height of the column layer.
        
            Args:
                value: New height for the column layer
            """
            if not isinstance(value, int):
                raise TypeError("layerHeight must be an integer")
            if value < 1:
                raise ValueError("layerHeight must be greater than or equal to 1")
            self._set('layerHeight', value)

    class Row(Object):

        def __init__(self, **kwargs) -> None:
            super().__init__(load=kwargs)
        
        @property
        def dataCondition(self):
            """
            Get the data condition of the row.

            Returns:
                str: Data condition of the row
            """
            return self._get('dataCondition')
        
        @dataCondition.setter
        def dataCondition(self, value):
            """
            Set the data condition of the row.
        
            Args:
                value: New data condition for the row
            """
            self._set('dataCondition', value)

        @property
        def height(self) -> int:
            """
            Get the height of the row.
        
            Returns:
                int: Height of the row
            """
            return self._get('height')
        
        @height.setter
        def height(self, value: int) -> None:
            """
            Set the height of the row.
        
            Args:
                value: New height for the row
            """
            if not isinstance(value, int):
                raise TypeError("height must be an integer")
            if value < 1:
                raise ValueError("height must be greater than or equal to 1")
            self._set('height', value)

    def __init__(self, startColumnIndex=1, startRowIndex=1, **kwargs) -> None:
        """
        Initialize a Table instance.
        Args:
            startColumnIndex (int): Starting index for columns, default is 1.
            startRowIndex (int): Starting index for rows, default is 1.
            kwargs: Additional keyword arguments to initialize the table.
        """
        kwargs.update(
            {
                'startColumnIndex': startColumnIndex,
                'startRowIndex': startRowIndex
            }
        )
        super().__init__(load=kwargs)
        self._columns = list()
        self._rows = list()
        self._data = list()

    def column(self, **kwargs) -> Self:
        """
        Add a column to the table.
        
        Args:
            kwargs: Keyword arguments containing column details such as 'name', 'dataType', etc.
        
        Returns:
            Self: The current instance of the Table
        """
        self._columns.append(Table.Column(layer=1, **kwargs))
        return self
    
    def row(self, **kwargs) -> Self:
        """
        Add a row to the table.
        Args:
            kwargs: Keyword arguments containing row details such as 'index', 'dataCondition', etc.
        Returns:
            Self: The current instance of the Table
        """
        self._rows.append(Table.Row(**kwargs))
        return self
    
    @property
    def data(self) -> list:
        return self._data
    
    @data.setter
    def data(self, value: list[list]) -> None:
        self._data = value

    @property
    def columns(self) -> list['Table.Column']:
        """
        Get the columns of the table.
        
        Returns:
            list: List of columns in the table
        """
        return self._columns
    
    @columns.setter
    def columns(self, value: list['Table.Column']) -> None:
        """
        Set the columns of the table.
        
        Args:
            value: List of columns to set
        """
        if not isinstance(value, list):
            raise TypeError("columns must be a list")
        if not all(isinstance(column, Table.Column) for column in value):
            raise TypeError("All items in columns must be instances of Table.Column")
        if self.columns == value:
            return
        self._columns = value

    @property
    def rows(self) -> list['Table.Row']:
        """
        Get the rows of the table.
        
        Returns:
            list: List of rows in the table
        """
        return self._rows
    
    @rows.setter
    def rows(self, value: list['Table.Row']) -> None:
        """
        Set the rows of the table.
        
        Args:
            value: List of rows to set
        """
        if not isinstance(value, list):
            raise TypeError("rows must be a list")
        if not all(isinstance(row, Table.Row) for row in value):
            raise TypeError("All items in rows must be instances of Table.Row")
        if self.rows == value:
            return
        self._rows = value

    @property
    def startColumnIndex(self):
        return self._get('startColumnIndex')

    @startColumnIndex.setter
    def startColumnIndex(self, value: int) -> None:
        """
        Set the starting index for columns in the table.
        Args:
            value (int): New starting index for columns
        """
        if value < 1:
            raise ValueError("startColumnIndex must be greater than or equal to 1")
        if not isinstance(value, int):
            raise TypeError("startColumnIndex must be an integer")
        if self.startColumnIndex == value:
            return
        self._set('startColumnIndex', value)

    @property
    def startRowIndex(self) -> int:
        """
        Get the starting index for rows in the table.
        Returns:
            int: Starting index for rows
        """
        return self._get('startRowIndex')

    @startRowIndex.setter
    def startRowIndex(self, value: int) -> None:
        """
        Set the starting index for rows in the table.
        Args:
            value (int): New starting index for rows
        """
        if value < 1:
            raise ValueError("startRowIndex must be greater than or equal to 1")
        if not isinstance(value, int):
            raise TypeError("startRowIndex must be an integer")
        if self.startRowIndex == value:
            return
        if value < self.startColumnIndex:
            raise ValueError("startRowIndex cannot be less than startColumnIndex")
        self._set('startRowIndex', value)

    @property
    def lastRowIndex(self) -> int:
        """
        Get the index of the last row in the table.
        Returns:
            int: Index of the last row
        """
        if not self.rows:
            return self.startRowIndex
        if not self.columns:
            return self.startRowIndex + len(self.rows) - 1
        return self.startRowIndex + len(self.rows) + self.totalColumnLayers - 1
   
    @property
    def lastColumnIndex(self) -> int:
        """
        Get the index of the last column in the table.
        Returns:
            int: Index of the last column
        """
        if not self.columns:
            return self.startColumnIndex
        return self.startColumnIndex + self.totalColumns - 1

    @property
    def firstDataColumnIndex(self) -> int:
        """
        Get the index of the first data column.
        
        Returns:
            int: Index of the first data column
        """
        if not self.rows:
            return self.startColumnIndex
        return self.startColumnIndex + 1
    
    @property
    def firstDataRowIndex(self) -> int:
        """
        Get the index of the first data row.
        
        Returns:
            int: Index of the first data row
        """
        if not self.columns:
            return self.startRowIndex
        return self.startRowIndex + 1
   
    @property
    def totalColumns(self) -> int:
        """
        Get the total number of columns in the table.

        Returns:
            int: Total number of columns in the table
        """
        total = 0 if not self.rows else 1
        for column in self.columns:
            total += column.totalSize
        return total
    
    @property
    def totalColumnLayers(self) -> int:
        """
        Get the total number of layers in the columns.
        
        Returns:
            int: Total number of layers/rows in the columns
        """
        total = 0
        if not self.columns:
            return total
        def _count_layers(columns: list[Table.Column], total: int) -> int:
            column = columns[0]
            total += 1
            if column.hasSubcolumns:
                total = _count_layers(column.subcolumns, total)
            return total
        return _count_layers(self.columns, total)