from datetime import date
from libraries.dates import dates


class WorksheetDates(dates):
    """
    Extension of the dates class with worksheet-specific date formatting functionality.
    
    Provides methods for converting between date objects and worksheet-friendly string 
    representations in formats commonly used in spreadsheets.
    """

    def __init__(self) -> None:
        """
        Initialize a WorksheetDates instance.
        """
        super().__init__()
    
    @classmethod
    def toDatetimeDate(cls, dateValue: str | date) -> date:
        """
        Convert a string date or date object to a datetime.date object.
        
        If the input is already a date object, it is returned unchanged.
        If the input is a string, it's expected to be in 'DD MMM YY' format.
        
        Args:
            dateValue: Date string in 'DD MMM YY' format or a date object
            
        Returns:
            date: Converted datetime.date object
        """
        if cls.isDatetimeDate(dateValue):
            return dateValue

        day, month, year = dateValue.split()
        if len(year) == 2:
            year = f'20{year}'
  
        yearInt = int(year)
        monthInt = cls.intMonth(month)
        dayInt = int(day)
        return cls.date(year=yearInt, month=monthInt, day=dayInt)
    
    @classmethod
    def toStringDate(cls, dateValue: date) -> str:
        """
        Convert a date object to a string in 'DD MMM YY' format.
        
        Args:
            dateValue: Date object to convert
            
        Returns:
            str: Date string in 'DD MMM YY' format
        """
        dayStr = str(dateValue.day)
        monthStr = cls.stringMonths()[dateValue.month - 1][:3]
        yearStr = str(dateValue.year)[-2:]
        return f'{dayStr} {monthStr} {yearStr}'