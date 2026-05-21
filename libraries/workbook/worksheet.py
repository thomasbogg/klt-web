from libraries.workbook.cells import Cell
from libraries.workbook.columns import Column
from libraries.workbook.rows import Row
from libraries.workbook.tables import Table
from openpyxl.worksheet.worksheet import Worksheet as OpxlWorksheet

class Worksheet:
    """
    A wrapper class for managing OpenPyXL worksheet objects with enhanced functionality.
    
    Provides simplified access to worksheet elements like rows, columns, and cells through
    integrated iterator objects.
    
    Attributes:
        name: Name of the worksheet
        sheet: Raw OpenPyXL worksheet object
        row: Row iterator for this worksheet
        column: Column iterator for this worksheet
        cell: Cell helper for this worksheet
    """

    def __init__(
            self, 
            name: str, 
            opxlSheet = None, 
            Row = Row, 
            Column = Column, 
            Cell = Cell,
            columnNames: list = None,) -> None:
        """
        Initialize a Worksheet object.
        
        Args:
            name: Name of the worksheet
            opxlSheet: OpenPyXL worksheet object to wrap
            Row: Row class to use for row operations
            Column: Column class to use for column operations
            Cell: Cell class to use for cell operations
        """
        self._name = name
        self._sheet: OpxlWorksheet = opxlSheet
        self._row = Row(opxlSheet=self._sheet)
        self._column = Column(opxlSheet=self._sheet)
        self._cell = Cell(self._sheet, column=self._column, row=self._row)
        self._columnNames = columnNames
        self._tables = list()

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def name(self) -> str:
        """
        Get the name of the worksheet.
        
        Returns:
            Worksheet name as a string
        """
        try:
            return self._sheet.title
        except AttributeError:
            return self._name
    
    @name.setter
    def name(self, value: str) -> None:
        """
        Set the name of the worksheet.
        
        Args:
            value: New worksheet name
        """
        self._sheet.title = value
    
    @property
    def sheet(self) -> OpxlWorksheet:
        """
        Get the raw OpenPyXL worksheet object.
        
        Returns:
            OpenPyXL worksheet object
        """
        return self._sheet
    
    @sheet.setter
    def sheet(self, value: OpxlWorksheet) -> None:
        """
        Set the raw OpenPyXL worksheet object.
        
        Args:
            value: OpenPyXL worksheet object
        """
        self._sheet = value
    
    @property
    def row(self) -> Row:
        """
        Get the row iterator for this worksheet.
        
        Returns:
            Row iterator object
        """
        return self._row
    
    @row.setter
    def row(self, value: Row) -> None:
        """
        Set the row iterator for this worksheet.
        
        Args:
            value: Row iterator object
        """
        self._row = value
    
    @property
    def column(self) -> Column:
        """
        Get the column iterator for this worksheet.
        
        Returns:
            Column iterator object
        """
        return self._column
    
    @column.setter
    def column(self, value: Column) -> None:
        """
        Set the column iterator for this worksheet.
        
        Args:
            value: Column iterator object
        """
        self._column = value

    @property
    def cell(self) -> Cell:
        """
        Get the cell helper for this worksheet.
        
        Returns:
            Cell helper object
        """
        return self._cell
    
    @cell.setter
    def cell(self, value: Cell) -> None:
        """
        Set the cell helper for this worksheet.
        
        Args:
            value: Cell helper object
        """
        self._cell = value
    
    @property
    def columnNames(self) -> list:
        """
        Get the existing column names for this worksheet.
        
        Returns:
            List with column names
        """
        if self._columnNames is None:
            self._columnNames = self._get_column_names()
        return self._columnNames
    
    @property
    def tables(self) -> list[Table]:
        return self._tables

    # =============================================================================
    # Methods
    # =============================================================================
    
    def open(self, opxlSheet: OpxlWorksheet = None) -> 'Worksheet':
        """
        Prepare the worksheet by setting up iterators with the proper sheet reference.
        
        Args:
            opxlSheet: OpenPyXL worksheet object to use, or None to use existing
            
        Returns:
            Self for method chaining
        """
        if opxlSheet is None:
            if self._sheet is None:
                return self
            else:
                opxlSheet = self._sheet
        
        self._sheet = opxlSheet
        self._row.sheet = opxlSheet
        self._column.sheet = opxlSheet
        self._cell.sheet = opxlSheet
        return self
    
    def table(self, table: Table = None, **kwargs) -> Table:
        if not table:
            table = Table(**kwargs)
        self._tables.append(table)
        return table
    
    # =============================================================================
    # Private Methods
    # =============================================================================
    
    def _get_column_names(self) -> list:
        savedRowCount = self._row.count
        savedColumnCount = self._column.count
        self._column.count = self._column.firstDataColumn
        self._row.count = self._row.firstDataRow - 1
      
        values = list()  
        while self._column.emptiesCount < 3:
            value = self._cell.value
           
            if value is not None:
                values.append(value)
                self._column.resetEmptyCount()
            else:
                self._column.increaseEmptyCount()
           
            self._column.increase()

        self._row.count = savedRowCount
        self._column.count = savedColumnCount      
        return values
    
    # =============================================================================
    # String Representation
    # =============================================================================    

    def __repr__(self) -> str:
        """
        String representation of the Worksheet object.
        
        Returns:
            String with worksheet name
        """
        return f"Worksheet(name={self.name})"