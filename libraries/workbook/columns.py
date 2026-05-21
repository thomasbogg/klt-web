from openpyxl.worksheet.worksheet import Worksheet as Ws


from libraries.workbook.iterator import WorksheetIterator

class Column(WorksheetIterator):
    """
    Iterator for worksheet columns with column-specific operations.
    Extends the WorksheetIterator with functionality for column manipulation.
    """

    def __init__(self, opxlSheet: Ws = None) -> None:
        """
        Initialize a column iterator.
        
        Args:
            opxlSheet: OpenPyXL worksheet object to iterate over
        """
        super().__init__(opxlSheet)
        self._firstDataColumn: int = 1

    # =============================================================================
    # Column Manipulation Methods
    # =============================================================================

    def delete(self, tot: int = 1) -> 'Column':
        """
        Delete columns from the worksheet.
        
        Args:
            tot: Number of columns to delete (default: 1)
            
        Returns:
            Self for method chaining
        """
        self._sheet.delete_cols(self._count, tot)
        return self
    
    def insert(self, tot: int = 1) -> 'Column':
        """
        Insert new columns into the worksheet.
        
        Args:
            tot: Number of columns to insert (default: 1)
            
        Returns:
            Self for method chaining
        """
        self._sheet.insert_cols(self._count, tot)
        return self
    
    # =============================================================================
    # Column Properties
    # =============================================================================
    
    @property
    def width(self) -> float | None:
        """Get the width of the current column."""
        return self._sheet.column_dimensions[self.letter].width
    
    @width.setter
    def width(self, value: float) -> None:
        """
        Set the width of the current column.
        
        Args:
            value: Column width value
        """
        self._sheet.column_dimensions[self.letter].width = value
    
    @property
    def firstDataColumn(self) -> int:
        """Get the defined first data column index."""
        return self._firstDataColumn

    @firstDataColumn.setter
    def firstDataColumn(self, value: int) -> None:
        """
        Set the first data column index.
        
        Args:
            value: Column index where data starts
        """
        self._firstDataColumn = value

    @property
    def isLessThanFirstDataColumn(self) -> bool:
        """
        Check if current position is before the first data column.
        
        Returns:
            True if current column is before the first data column
        """
        return self.count < self._firstDataColumn

    @property
    def letter(self) -> str:
        """
        Get the Excel column letter for the current column position.
        
        Returns:
            Column letter (A, B, C, etc.)
        """
        return self._get_letter()

    # =============================================================================
    # Helper Methods
    # =============================================================================

    def _get_letter(self, column: int | None = None, row: int = 100) -> str:
        """
        Convert a column number to Excel column letter.
        
        Args:
            column: Column index (default: current iterator position)
            row: Row index to use for cell reference (default: 100)
            
        Returns:
            Column letter (A, B, C, etc.)
        """
        if column is None: 
            column = self.count
        return self._sheet.cell(column=column, row=row).column_letter