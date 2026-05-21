from libraries.workbook.worksheet import Worksheet
from libraries.workbook.tables import Table
from libraries.workbook.stylesheet import Stylesheet
from libraries.workbook.cells import Cell


def set_tables_in_worksheet(
        worksheet: Worksheet, 
        tables: list[Table] = None, 
        **kwargs) -> None:
    """
    Set tables in a worksheet.

    :param worksheet: The worksheet to set the tables in.
    :param tables: A list of tables to set in the worksheet.
    :param kwargs: Additional keyword arguments for table settings.
    :return: None
    """
    if tables is None:
        tables = worksheet.tables

    # Set the starting row for the worksheet
    worksheet.row.number = kwargs.get('startRow', 1)

    columnWidths = {}

    # Start setting tables in the worksheet
    for table in tables:
        # Set/Reset the starting column for the worksheet
        worksheet.column.number = kwargs.get('startColumn', 1)
        # Set the title for the table
        if table.has('name'):
            _set_title(worksheet, table)
            # Move to the next row after the title
            worksheet.row.increase()
        else:
            # If no title, increase row height
            worksheet.row.height = 30
        # Set the first column row for the table
        worksheet.row.firstColumnRow = worksheet.row.number
       
        # Total rows of columns in the table
        columnLayers = table.totalColumnLayers
        # Set the first data row after the title and columns
        worksheet.row.firstDataRow = worksheet.row.number + columnLayers 
        # Set the rows for the table
        hasTotal = _set_rows(worksheet, table)

        # Move to next column after setting rows
        worksheet.column.increase()
        # Set the first data column for the table
        worksheet.column.firstDataColumn = worksheet.column.number
        # Determine the soft border columns for the table
        softBorderColumns = _determine_soft_border_columns(worksheet, table)
        # Start section count for background color
        section = 1

        # Set the primary and sub columns and data for the table
        # Each primary column will count as a section
        # The section is used to set the background color for each column
        for column in table.columns:
            # Set the column start position
            columnStart = worksheet.column.number
            # Set the first column row for the worksheet
            worksheet.row.number = worksheet.row.firstColumnRow
            # Set the columns in the worksheet
            # if the column has subcolumns, use the section number to determine the background color
            _section = 0 if columnLayers < 2 else section
            _set_column(worksheet, column, columnLayers, _section, softBorderColumns)
            # Get the size of the column and set the right chunk of the data
            size = column.totalSize
            dataStart = (section - 1) * size
            dataEnd = dataStart + size
            data = table.data[dataStart:dataEnd]
            # Set the column number back to the current column start position
            worksheet.column.number = columnStart
            # Set the data for the column section in the worksheet
            _set_data(worksheet, data, _section, softBorderColumns, columnWidths, hasTotal)
            # Increase the section number for correct background color setting
            section += 1

        worksheet.row.increase(3)  # Add space after each table

    if kwargs.get('adjustColumnWidths', True):
        for columnNumber, width in columnWidths.items():
            if width > 0:
                worksheet.column.number = columnNumber
                worksheet.column.width = width + 2


def _set_title(worksheet: Worksheet, table: Table) -> None:
    """
    Set the title for the table in the worksheet.

    :param worksheet: The worksheet to set the title in.
    :param table: The table to set the title for.

    :return: None
    """
    style = Stylesheet()
    style.bold = True
    style.border = 'thin'
    style.fontSize = 15
    style.fontName = 'Arial'

    totalCols = table.totalColumns
    cell = worksheet.cell
    cell.value = table.name
    cell.rows = 1
    cell.columns = totalCols
    cell.styles = style
    cell.merge()

    worksheet.row.height = 30


def _set_column(
        worksheet: Worksheet, 
        column: Table.Column, 
        columnLayers: int, 
        section: int = 1, 
        softBorderColumns: list[int] = None) -> None:
    """
    Set the columns for the table in the worksheet.

    :param worksheet: The worksheet to set the columns in.
    :param table: The table to set the columns for.
    :return: None
    """
    def __set_style(cell: Cell, section: int, layer: int, totalLayers: int, needsRightBorder: bool) -> None:
        style = Stylesheet()
        style.bold = True
        style.fontSize = 10 if layer == totalLayers else 11
        style.fontName = 'Noto Mono'
        style.backgroundColour = _section_colour(section)
        style.borderBottom = 'thin' if layer == 1 else None
        style.borderRight = 'thin' if needsRightBorder else None
        cell.styles = style

    if columnLayers > 1:
        worksheet.row.height = 22

    if column.has('width'):
        worksheet.column.width = column.width
    
    size = column.totalSize
    cell = worksheet.cell
    cell.value = column.name
    
    columnLimit = worksheet.column.number + size - 1
    needsRightBorder = softBorderColumns and columnLimit in softBorderColumns
    __set_style(cell, section, column.layer, columnLayers, needsRightBorder)
    
    cell.columns = size
    cell.merge()

    if column.hasSubcolumns:
        worksheet.row.increase()
        for subcolumn in column.subcolumns:
            _set_column(worksheet, subcolumn, columnLayers, section, softBorderColumns)
            worksheet.column.increase()
        
        worksheet.row.decrease()
        worksheet.column.decrease()
   

def _section_colour(section: int) -> str:
    """
    Get the color for a section based on its index.

    :param section: The section index.
    :return: Color code as a string.
    """
    if section < 1:
        return None
    colors = ['b6d3de', 'fff5ce', 'f4cccc'] # Light colors for sections
    return colors[section % len(colors)]


def _set_rows(worksheet: Worksheet, table: Table) -> None:
    """
    Set the rows for the table in the worksheet.

    :param worksheet: The worksheet to set the rows in.
    :param table: The table to set the rows for.
    :param columnLayers: The number of column layers in the table.
    :return: None
    """
    style = Stylesheet()
    style.fontSize = 10
    style.bold = True
    if table.totalColumnLayers > 1:
        style.backgroundColour = 'efe3e6'

    hasTotal = False
    widest = 0

    worksheet.row.number = worksheet.row.firstDataRow
    for row in table.rows:
        cell = worksheet.cell
        if row.name.lower() == 'total':
            hasTotal = True
            style.borderTop = 'thin'
        else:
            style.borderTop = None
        cell.value = row.name
        cell.styles = style

        if len(row.name) > widest:
            widest = len(row.name)
        worksheet.row.height = 20
        worksheet.row.increase()

    if widest > 0 and widest + 2 > worksheet.column.width:
        # If the widest row name is greater than the current column width, update it
        worksheet.column.width = widest + 2
    
    return hasTotal


def _set_data(
        worksheet: Worksheet, 
        data: list[list[int | str]] | list[list[dict[str, int | str]]], 
        section: int, 
        softBorderColumns: list[int] = None,
        columnWidths: dict[int:int] = None,
        hasTotal: bool = False) -> None:
    """
    Set the data for the table in the worksheet.

    :param worksheet: The worksheet to set the data in.
    :param data: The data to set in the worksheet.
    :param columnLayers: The number of column layers in the table.
    :param section: The section index for styling.

    :return: None
    """
    def __set_style(cell: Cell, section: int, hasBorder: bool, isTotal: bool, **kwargs) -> None:
        """
        Set the style for a cell based on the section and border requirement.

        :param cell: The cell to set the style for.
        :param section: The section index for styling.
        :param hasBorder: Whether the cell should have a right border.
        :param isTotal: Whether the cell is a total row.
        :param kwargs: Additional style properties to set.
        """
        style = Stylesheet()
        style.update(**kwargs)
        style.backgroundColour = _section_colour(section)
        style.borderRight = 'thin' if hasBorder else None
        style.borderTop = 'thin' if isTotal else None
        cell.styles = style

    for columnOfData in data:
        worksheet.row.number = worksheet.row.firstDataRow
        hasBorder = softBorderColumns and worksheet.column.number in softBorderColumns
        counter = 0

        for dataPoint in columnOfData:
            counter += 1
            styles = {}
            cell = worksheet.cell

            if isinstance(dataPoint, dict):
                # If dataPoint is a dictionary, extract the value
                styles = dataPoint.get('styles', {})
                value = dataPoint.get('value', '')
            else:
                value = dataPoint

            cell.value = value
            isTotal = hasTotal and counter == len(columnOfData)

            __set_style(cell, section, hasBorder, isTotal, **styles)
            _check_column_width(worksheet, value, columnWidths)
            worksheet.row.increase()

        worksheet.column.increase()


def _determine_soft_border_columns(worksheet: Worksheet, table: Table) -> None:
    """
    Determine the soft border cells for the table in the worksheet.

    :param worksheet: The worksheet to set the soft border cells in.
    :param table: The table to set the soft border cells for.
    :return: None
    """
    def __get_soft_border_cells(
            columns: list[Table.Column], 
            totalColumns: int, 
            offset: int,
            borderPositions: list[int]) -> list[int]:
        """
        Get the number of soft border cells in the table.

        :param columns: The columns of the table.
        :param borderPositions: The list to store the positions of soft border cells.

        :return: List of positions of soft border cells.
        """
        for column in columns:
            
            if column.layer % 2 == 0:    
                if columns.index(column) < len(columns) - 1:
                    borderPositions.append(column.totalSize + offset)
            
            if column.hasSubcolumns:
                __get_soft_border_cells(
                                    column.subcolumns, 
                                    totalColumns,
                                    offset,
                                    borderPositions
                                )

            offset += column.totalSize
        return borderPositions

    layers = table.totalColumnLayers
    if layers < 3:
        return []
    totalColumns = table.totalColumns
    return __get_soft_border_cells(
                                table.columns,
                                totalColumns,
                                worksheet.column.firstDataColumn - 1,       
                                borderPositions=list())


def _check_column_width(worksheet: Worksheet, value: str | int, columnWidths: dict[int:int]) -> None:
    """
    Check and set the column width based on the value length.

    :param worksheet: The worksheet to check the column width in.
    :param value: The value to check the length of.
    :param columnWidths: The dictionary of column widths to update.
    """
    if worksheet.column.number not in columnWidths:
        columnWidths[worksheet.column.number] = 0

    value = str(value) if value is not None else ''
    length = len(value)
    if length > columnWidths[worksheet.column.number]:
        columnWidths[worksheet.column.number] = length