import regex as re
from typing import Any, Literal

from libraries.workbook.stylesheet import Stylesheet
from libraries.workbook.rows import Row
from libraries.workbook.columns import Column

# Valid cell format types
FormatType = Literal['@', '0', '0%', 'D MMM YY', 'HH:MM', 
                     '[$€-83C]#,##0.00;[RED]-[$€-83C]#,##0.00', 
                     '[$£-809]#,##0.00;-[$£-809]#,##0.00']
CurrencyType = Literal['EUR', 'GBP']

class Cell:
    """
    A wrapper class for managing OpenPyXL cell objects with enhanced functionality.
    Provides simplified access to cell properties and formatting options.
    """

    def __init__(
            self, 
            opxlSheet: Any = None, 
            value: Any = None, 
            format: FormatType | None = None, 
            column: Column | int = Column, 
            row: Row | int = Row, 
            columns: int = 1, 
            rows: int = 1, 
            hyperlink: str | None = None, 
            styles: Stylesheet | None = None) -> None:
        """
        Initialize a Cell object.
        
        Args:
            opxlSheet: The OpenPyXL worksheet this cell belongs to
            value: Initial value for the cell
            format: Number format string
            column: Column index or Column object
            row: Row index or Row object
            columns: Number of columns to span if merged
            rows: Number of rows to span if merged
            hyperlink: URL for hyperlink
            styles: Stylesheet class to use for styling
        """
        self._sheet: Any = opxlSheet
        self._column: Column | int = column
        self._row: Row | int = row
        self._rows: int = rows
        self._columns: int = columns
    
        if value:
            self.value = value    
        if format:
            self.format = format
        if styles:
            self.styles = styles()
        if hyperlink:
            self.hyperlink = hyperlink

    # =============================================================================
    # Cell Operations
    # =============================================================================

    def merge(self) -> 'Cell':
        """
        Merge cells according to the defined rows and columns span.
        
        Returns:
            Self for method chaining
        """
        startColumn = self.column.number
        startRow = self.row.number
        endRow = startRow + self.rows - 1
        endColumn = startColumn + self.columns - 1
        self._sheet.merge_cells(
            start_column=startColumn, start_row=startRow, end_column=endColumn, end_row=endRow)
        self._columns = 1
        self._rows = 1
        return self
    
    def unmerge(self) -> 'Cell':
        """
        Unmerge the cell if it is merged.
        
        Returns:
            Self for method chaining
        """
        startColumn = self.column.number
        startRow = self.row.number
        endRow = startRow + self.rows - 1
        endColumn = startColumn + self.columns - 1
        self._sheet.unmerge_cells(
            start_column=startColumn, start_row=startRow, end_column=endColumn, end_row=endRow)
        return self
    
    def empty(self) -> 'Cell':
        """
        Reset all cell properties to default values.
        
        Returns:
            Self for method chaining
        """
        self.value = str()
        self.format = '@'
        self.styles = Stylesheet()
        self.hyperlink = None
        return self
    
    # =============================================================================
    # Formula Setting Methods
    # =============================================================================

    def setTotal(self, start: str | None = None, end: str | None = None) -> 'Cell':
        """
        Create a SUM formula for a range of cells.
        
        If start and end cell references are not provided, they will be automatically 
        calculated based on the current cell position:
        - start: Two columns to the left of current position
        - end: One column to the left of current position
        
        The method temporarily modifies the column position during calculation
        and restores it before returning.
        
        Args:
            start: Starting cell reference (e.g., 'A1')
            end: Ending cell reference (e.g., 'B1')
            
        Returns:
            Self for method chaining
        """
        if start is None or end is None:
            if start is None:
                start = f'{self.column.decrease().decrease().letter}{self.row.number}'
                self.column.increase().increase()
          
            if end is None:
                end = f'{self.column.decrease().letter}{self.row.number}'
                self.column.increase()

        self.value = f'= {start} + {end}'
        return self

    def setRunningTotal(self) -> 'Cell':
        """
        Create a formula for a running total that accumulates values.
        
        This method generates different formulas based on position and context:
        - For the first data row: Uses the value from the previous column or 0
        - For other rows: Adds the value from the previous column to the running 
          total from the previous row
        
        The method temporarily modifies row and column positions during calculation
        and restores them before returning.
        
        Returns:
            Self for method chaining
        """
        prevColumn = f'{self.column.decrease().letter}{self.row.number}'
        prevColumnVal = self.value
        self.column.increase()

        if self.row.isFirstDataRow:
            self.value = f'= {prevColumn}'
            #if isinstance(prevColumnVal, (int, float)):
            #    self.value = f'= {prevColumn}'
            #else:
            #    self.value = f'= 0'
            return self

        prevRow = f'{self.column.letter}{self.row.decrease().number}'
        self.row.increase()

        # Check if the previous column value is None or not a number
        ## use regex to check if value is a formula
        ### check for a formula pattern like '=A1 + B1'
        formulaStr = r'=[ ]*[A-Z]+[0-9]+[ \+\-\:\*]+[A-Z]+[0-9]+'
        if (
            prevColumnVal is None or not 
            (
                isinstance(prevColumnVal, (int, float)) or 
                re.search(formulaStr, str(prevColumnVal))
            )
        ):
            self.value = f'= {prevRow}'
        else:
            self.value = f'= {prevRow} + {prevColumn}'
        return self
    
    # =============================================================================
    # Format Setting Methods
    # =============================================================================

    def setToTextFormat(self) -> 'Cell':
        """
        Set the cell format to text.
        
        Returns:
            Self for method chaining
        """
        self.format = self._text_format()
        return self

    def setToPercentageFormat(self) -> 'Cell':
        """
        Set the cell format to percentage.
        
        Returns:
            Self for method chaining
        """
        self.format = self._percentage_format()
        return self
    
    def setToNumberFormat(self) -> 'Cell':
        """
        Set the cell format to number.
        
        Returns:
            Self for method chaining
        """
        self.format = self._number_format()
        return self

    def setToDateFormat(self) -> 'Cell':
        """
        Set the cell format to date (D MMM YY).
        
        Returns:
            Self for method chaining
        """
        self.format = self._date_format()
        return self
    
    def setToTimeFormat(self) -> 'Cell':
        """
        Set the cell format to time (HH:MM).
        
        Returns:
            Self for method chaining
        """
        self.format = self._time_format()
        return self

    def setToEurosFormat(self) -> 'Cell':
        """
        Set the cell format to Euros currency.
        
        Returns:
            Self for method chaining
        """
        self.format = self._euros_format()
        return self

    def setToPoundsFormat(self) -> 'Cell':
        """
        Set the cell format to Pounds Sterling currency.
        
        Returns:
            Self for method chaining
        """
        self.format = self._pounds_format()
        return self

    def setToCurrencyFormat(self, currency: CurrencyType) -> 'Cell':
        """
        Set the cell format to a specific currency.
        
        Args:
            currency: Currency code ('EUR', 'GBP', or others)
            
        Returns:
            Self for method chaining
        """
        if currency == 'EUR': 
            self.setToEurosFormat()
        elif currency == 'GBP': 
            self.setToPoundsFormat()
        else: 
            self.setToNumberFormat()
        return self

    # =============================================================================
    # Properties
    # =============================================================================
    
    @property
    def sheet(self) -> Any:
        """Get the OpenPyXL worksheet object."""
        return self._sheet
    
    @sheet.setter
    def sheet(self, value: Any) -> None:
        """Set the OpenPyXL worksheet object."""
        self._sheet = value

    @property
    def column(self) -> Column | int:
        """Get the column index of this cell."""
        return self._column
    
    @column.setter
    def column(self, value: Column | int) -> None:
        """Set the column index of this cell."""
        self._column = value

    @property
    def row(self) -> Row | int:
        """Get the row index of this cell."""
        return self._row
    
    @row.setter
    def row(self, value: Row | int) -> None:
        """Set the row index of this cell."""
        self._row = value

    @property
    def rows(self) -> int:
        """Get the number of rows this cell spans if merged."""
        return self._rows
    
    @rows.setter
    def rows(self, value: int) -> None:
        """Set the number of rows this cell spans if merged."""
        self._rows = value

    @property
    def columns(self) -> int:
        """Get the number of columns this cell spans if merged."""
        return self._columns
    
    @columns.setter
    def columns(self, value: int) -> None:
        """Set the number of columns this cell spans if merged."""
        self._columns = value

    @property
    def value(self) -> Any:
        """Get the current value of the cell."""
        return self._get().value
    
    @value.setter
    def value(self, value: Any) -> None:
        """Set the value of the cell."""
        self._get().value = value
    
    @property
    def format(self) -> str:
        """Get the number format string of the cell."""
        return self._get().number_format
    
    @format.setter
    def format(self, value: str) -> None:
        """
        Set the number format of the cell.
        
        Args:
            value: A valid Excel number format string
            
        Raises:
            ValueError: If the format is not recognized as valid
        """
        if not self._valid_format(value):
            raise ValueError(f"Invalid format: {value}")
        self._get().number_format = value

    @property
    def hyperlink(self) -> Any:
        """Get the hyperlink of the cell."""
        return self._get().hyperlink
    
    @hyperlink.setter
    def hyperlink(self, value: str | None) -> None:
        """Set the hyperlink of the cell."""
        self._get().hyperlink = value

    @property
    def styles(self) -> Any:
        """Get the style object of the cell."""
        return self._get().style
    
    @styles.setter
    def styles(self, value: Stylesheet) -> None:
        """
        Set the style of the cell.
        
        Args:
            value: A Stylesheet object
        """
        cell = self._get()
        if value.font:
            cell.font = value.font
        if value.border:
            cell.border = value.border
        if value.alignment:
            cell.alignment = value.alignment
        if value.fill:
            cell.fill = value.fill

    @property
    def border(self) -> Any:
        """Get the border object of the cell."""
        return self._get().border
    
    @border.setter
    def border(self, value: Any) -> None:
        """
        Set the border of the cell.
        
        Args:
            value: An OpenPyXL Border object
        """
        self._get().border = value

    @property
    def font(self) -> Any:
        """Get the font object of the cell."""
        return self._get().font
    
    @font.setter
    def font(self, value: Any) -> None:
        """
        Set the font of the cell.
        
        Args:
            value: An OpenPyXL Font object
        """
        self._get().font = value

    @property
    def alignment(self) -> Any:
        """Get the alignment object of the cell."""
        return self._get().alignment
    
    @alignment.setter
    def alignment(self, value: Any) -> None:
        """
        Set the alignment of the cell.
        
        Args:
            value: An OpenPyXL Alignment object
        """
        self._get().alignment = value

    @property
    def fill(self) -> Any:
        """Get the fill object of the cell."""
        return self._get().fill
    
    @fill.setter
    def fill(self, value: Any) -> None:
        """
        Set the fill of the cell.
        
        Args:
            value: An OpenPyXL Fill object
        """
        self._get().fill = value

    # =============================================================================
    # Cell State Properties
    # =============================================================================

    @property
    def isNotEmpty(self) -> bool:
        """Check if the cell has a non-None value."""
        return self.value is not None

    @property
    def isEmpty(self) -> bool:
        """Check if the cell has a None value."""
        return self.value is None
    
    @property
    def hasBorder(self) -> str | None:        
        """
        Check if the cell has a border.
        
        Returns:
            The border style if it exists, None otherwise
        """
        try: 
            return self._get().border.bottom.style
        except: 
            return None
        
    @property
    def isStruck(self) -> bool:
        """Check if the cell text has strikethrough formatting."""
        return self._get().font.strike

    # =============================================================================
    # Private Methods
    # =============================================================================

    def _get(self) -> Any:
        """
        Get the underlying OpenPyXL cell object.

        Returns:
            The OpenPyXL cell at the current row and column
        """
        return self._sheet.cell(row=self.row.number, column=self.column.number)

    def _valid_format(self, value: str) -> bool:
        """
        Check if a format string is valid.
        
        Args:
            value: Format string to validate
            
        Returns:
            True if the format is valid, False otherwise
        """
        return value in (
                    '@', 
                    '0', 
                    '0%', 
                    'D MMM YY', 
                    'HH:MM', 
                    '[$€-83C]#,##0.00;[RED]-[$€-83C]#,##0.00', 
                    '[$£-809]#,##0.00;-[$£-809]#,##0.00'
        )
    
    def _text_format(self) -> FormatType:
        """Return the text format string."""
        return '@'

    def _number_format(self) -> FormatType:
        """Return the number format string."""
        return '0'

    def _percentage_format(self) -> FormatType:
        """Return the percentage format string."""
        return '0%'
    
    def _date_format(self) -> FormatType:
        """Return the date format string."""
        return 'D MMM YY'
    
    def _time_format(self) -> FormatType:
        """Return the time format string."""
        return 'HH:MM'
    
    def _euros_format(self) -> FormatType:
        """Return the Euros currency format string."""
        return '[$€-83C]#,##0.00;[RED]-[$€-83C]#,##0.00'
    
    def _pounds_format(self) -> FormatType:
        """Return the Pounds Sterling currency format string."""
        return '[$£-809]#,##0.00;-[$£-809]#,##0.00'