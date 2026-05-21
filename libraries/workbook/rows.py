from openpyxl.worksheet.worksheet import Worksheet as Ws

from libraries.workbook.iterator import WorksheetIterator

class Row(WorksheetIterator):
    """
    Iterator for worksheet rows with row-specific operations.
    Extends the WorksheetIterator with functionality for row manipulation.
    """

    def __init__(self, opxlSheet: Ws = None) -> None:
        """
        Initialize a row iterator.
        
        Args:
            opxlSheet: OpenPyXL worksheet object to iterate over
        """
        super().__init__(opxlSheet)
        self._headerRow: int = 1
        self._firstDataRow: int = 2
        self._defaultFreeze: int = 2
        self._defaultHeight: float = 20

    # =============================================================================
    # Row Manipulation Methods
    # =============================================================================

    def delete(self, tot: int | None = None) -> 'Row':
        """
        Delete rows from the worksheet.
        
        Args:
            tot: Number of rows to delete (default: 1)
            
        Returns:
            Self for method chaining
        """
        if tot is None:
            tot = self._defaultDelete
        if tot < 1:
            raise ValueError("Delete count must be greater than 0.")
        self._sheet.delete_rows(self._count, tot)
        return self

    def insert(self, tot: int | None = None) -> 'Row':
        """
        Insert new rows into the worksheet.
        
        Args:
            tot: Number of rows to insert (default: 1)
            
        Returns:
            Self for method chaining
        """
        if tot is None:
            tot = self._defaultInsert
        if tot < 1:
            raise ValueError("Insert count must be greater than 0.")
        self._sheet.insert_rows(self._count, tot)
        return self

    def freeze(self, value: int | None = None) -> 'Row':
        """
        Freeze rows above the specified position.
        
        Args:
            value: Row number to freeze above (default: 2)
            
        Returns:
            Self for method chaining
        """
        if value is None:
            value = self._defaultFreeze
        if value < 1:
            raise ValueError("Freeze row number must be greater than 0.")
        if value < self._firstDataRow:
            raise ValueError("Freeze row number must be same or greater than first data row.")
        self._sheet.freeze_panes = f'A{value}'
        return self
    
    def setHeight(self, value: float | None = None) -> 'Row':
        """
        Set the height of the current row.
        
        Args:
            value: Row height value (default: 20)
            
        Returns:
            Self for method chaining
        """
        if value is None:
            value = self._defaultHeight
        if value < 0:
            raise ValueError("Row height must be a positive number.")
        self._sheet.row_dimensions[self._count].height = value
        return self
    
    # =============================================================================
    # Row Properties
    # =============================================================================

    @property
    def defaultHeight(self) -> float:
        """Get the default height value."""
        return self._defaultHeight
  
    @defaultHeight.setter
    def defaultHeight(self, value: float) -> None:
        """
        Set the default height value.
        
        Args:
            value: Default height value
        """
        if value < 0:
            raise ValueError("Row height must be a positive number.")
        self._defaultHeight = value
        return self

    @property
    def height(self) -> float | None:
        """Get the height of the current row."""
        return self._sheet.row_dimensions[self._count].height
    
    @height.setter
    def height(self, value: float = 20) -> None:
        """
        Set the height of the current row.
        
        Args:
            value: Row height value (default: 20)
        """
        self._sheet.row_dimensions[self._count].height = value

    @property
    def firstDataRow(self) -> int:
        """Get the defined first data row index."""
        return self._firstDataRow
    
    @firstDataRow.setter
    def firstDataRow(self, value: int) -> None:
        """
        Set the first data row index.
        
        Args:
            value: Row index where data starts
        """
        self._firstDataRow = value

    @property
    def headerRow(self) -> int:
        """Get the defined header row index."""
        return self._headerRow
    
    @headerRow.setter
    def headerRow(self, value: int) -> None:
        """
        Set the header row index.
        
        Args:
            value: Row index where headers are located
        """
        self._headerRow = value

    @property
    def isLessThanFirstDataRow(self) -> bool:
        """
        Check if current position is before the first data row.
        
        Returns:
            True if current row is before the first data row
        """
        return self.count < self._firstDataRow

    @property
    def isGreaterThanFirstDataRow(self) -> bool:
        """
        Check if current position is after the first data row.
        
        Returns:
            True if current row is after the first data row
        """
        return self.count > self._firstDataRow
    
    @property
    def isFirstDataRow(self) -> bool:
        """
        Check if current position is at the first data row.
        
        Returns:
            True if current row is the first data row
        """
        return self.count == self._firstDataRow

    @property
    def defaultFreeze(self) -> int:
        """Get the default freeze value."""
        return self._defaultFreeze
   
    @defaultFreeze.setter
    def defaultFreeze(self, value: int) -> None:
        """
        Set the default freeze value.
        
        Args:
            value: Default freeze value
        """
        self._defaultFreeze = value