import calendar
import datetime
from dateutil import parser
from dateutil.relativedelta import relativedelta
from typing import Any

from libraries.utils import logerror, logwarning

class dates:
    """
    Utility class for date and time operations.
    
    Provides methods for creating, manipulating, formatting, and converting
    datetime objects with a focus on practical application.
    """

    def __init__(self) -> None:
        """Initialize a dates instance (not typically needed as most methods are class or static methods)."""
        pass

    # =============================================================================
    # Core Date/Time Creation Methods
    # =============================================================================

    @staticmethod
    def now() -> datetime.datetime:
        """
        Get the current date and time.
        
        Returns:
            Current datetime
        """
        return datetime.datetime.now()

    @classmethod
    def date(cls, year: int | str | datetime.date | None = None, 
             month: int | str | None = None, 
             day: int | None = None) -> datetime.date:
        """
        Create a date object with the given year, month, and day.
        
        Args:
            year: Year value (int, str, or date object)
            month: Month value (int, str, or month name)
            day: Day value
            
        Returns:
            Date object with the specified components
        """
        if year is None: 
            year = cls.now().year
        if month is None: 
            month = cls.now().month
        if day is None: 
            day = cls.now().day
        
        year = int(year)
        month = cls.intMonth(month)
        day = int(day)
        
        return datetime.date(year, month, day)

    @classmethod
    def time(cls, hour: int | None = None, 
             minute: int | None = None, 
             second: int | None = None) -> datetime.time:
        """
        Create a time object with the given hour, minute, and second.
        
        Args:
            hour: Hour value (0-23)
            minute: Minute value (0-59)
            second: Second value (0-59)
            
        Returns:
            Time object with the specified components or current time if hour is None
        """
        if hour is None: 
            return cls.now().time()
        if minute is None: 
            minute = 0
        if second is None:
            second = 0
        return datetime.time(int(hour), int(minute), int(second))

    @classmethod
    def makeDatetime(cls, date: datetime.date | None = None, 
                     time: datetime.time | None = None) -> datetime.datetime:
        """
        Combine date and time objects into a datetime object.
        
        Args:
            date: Date component (defaults to today)
            time: Time component (defaults to midnight)
            
        Returns:
            Combined datetime object
        """
        if date is None: 
            date = cls.date()
        if time is None: 
            time = cls.time(0)
        return datetime.datetime.combine(date, time)

    @staticmethod
    def fromIsoFormat(value: str) -> datetime.datetime:
        """
        Parse ISO format string including timezone information.
        
        Args:
            value: ISO formatted datetime string
            
        Returns:
            Parsed datetime object
        """
        return parser.isoparse(value)

    @staticmethod
    def toIsoFormat(time: datetime.time) -> str:
        """
        Convert a time object to ISO format string.
        
        Args:
            time: Time object to convert
            
        Returns:
            ISO formatted time string
        """
        if not isinstance(time, datetime.time): 
            return time
        return time.isoformat()

    @classmethod
    def calculate(cls, date: datetime.date | None = None, 
                 time: datetime.time | None = None, **kwargs) -> datetime.date | datetime.time:
        """
        Calculate a new date or time by adding relativedelta components.
        
        Args:
            date: Starting date
            time: Starting time
            **kwargs: Components to add (days, months, years, etc.)
            
        Returns:
            New date or time object after calculation
        """
        value = cls.makeDatetime(date, time)
        result = value + relativedelta(**kwargs)
        if date or not time:
            return result.date()
        return result.time()

    # =============================================================================
    # Date Component Getters
    # =============================================================================

    @classmethod
    def year(cls, value: int = 0) -> int:
        """
        Get the current year plus offset.
        
        Args:
            value: Year offset
            
        Returns:
            Year value
        """
        return cls.now().year + value

    @classmethod
    def month(cls, value: int = 0) -> int:
        """
        Get the current month plus offset.
        
        Args:
            value: Month offset
            
        Returns:
            Month value (1-12)
        """
        value = cls.now().month + value
        month = value % 12
        return 12 if month == 0 else month

    @classmethod
    def day(cls) -> int:
        """
        Get the current day of month.
        
        Returns:
            Day of month
        """
        return cls.now().day

    @classmethod
    def dayInYear(cls, date: datetime.date | None = None) -> int:
        """
        Get the day of year (1-366).
        
        Args:
            date: Date object or None for today
            
        Returns:
            Day of year
        """
        if not date: 
            date = cls.now().date()
        return int(date.strftime('%j'))

    @classmethod
    def weekday(cls) -> int:
        """
        Get the current weekday (0=Monday, 6=Sunday).
        
        Returns:
            Weekday number
        """
        return cls.now().weekday()

    @classmethod
    def hour(cls) -> int:
        """
        Get the current hour.
        
        Returns:
            Hour value
        """
        return cls.now().hour

    @classmethod
    def minute(cls) -> int:
        """
        Get the current minute.
        
        Returns:
            Minute value
        """
        return cls.now().minute

    @classmethod
    def breakUpDate(cls, date: datetime.date | None = None) -> tuple[int, int, int]:
        """
        Break a date into its components.
        
        Args:
            date: Date object or None for today
            
        Returns:
            Tuple of (year, month, day)
        """
        if date: 
            return date.year, date.month, date.day
        return cls.year(), cls.month(), cls.day()

    # =============================================================================
    # Calendar Navigation Methods
    # =============================================================================

    @classmethod
    def tomorrow(cls) -> datetime.date:
        """
        Get tomorrow's date.
        
        Returns:
            Tomorrow's date
        """
        return cls.calculate(days=1)

    @classmethod
    def firstOfMonth(cls, value: int = 0) -> datetime.date:
        """
        Get the first day of the month with optional offset.
        
        Args:
            value: Month offset
            
        Returns:
            First day of the specified month
        """
        date = cls.calculate(months=value)
        return datetime.date(date.year, date.month, 1)

    @classmethod
    def lastOfMonth(cls, value: int = 0) -> datetime.date:
        """
        Get the last day of the month with optional offset.
        
        Args:
            value: Month offset
            
        Returns:
            Last day of the specified month
        """
        date = cls.calculate(months=value)
        day = cls.daysInMonth(date.year, date.month)
        return datetime.date(date.year, date.month, day)

    @classmethod
    def isLastOfMonth(cls) -> bool:
        """
        Check if today is the last day of the month.
        
        Returns:
            True if today is the last day of the month
        """
        return cls.date() == cls.lastOfMonth()

    @classmethod
    def firstOfYear(cls, value: int = 0) -> datetime.date:
        """
        Get the first day of the year with optional offset.
        
        Args:
            value: Year offset
            
        Returns:
            First day of the specified year
        """
        return datetime.date(cls.year() + value, 1, 1)

    @classmethod
    def lastOfYear(cls, value: int = 0) -> datetime.date:
        """
        Get the last day of the year with optional offset.
        
        Args:
            value: Year offset
            
        Returns:
            Last day of the specified year
        """
        return datetime.date(cls.year() + value, 12, 31)

    @classmethod
    def daysInMonth(cls, year: int | None = None, month: int | None = None) -> int:
        """
        Get the number of days in the specified month.
        
        Args:
            year: Year
            month: Month (1-12)
            
        Returns:
            Number of days in the month
        """
        year = year or cls.year()
        month = month or cls.month()
        return calendar.monthrange(year, month)[1]
    
    @classmethod
    def future(cls) -> datetime.date:
        """
        Get a date in the future by a specified number of days.
        
        Args:
            value: Number of days in the future
        Returns:
            Future date object
        """
        return cls.date(year=cls.year(10))

    # =============================================================================
    # Date/Time Math Operations
    # =============================================================================

    @staticmethod
    def genericDatetimeForTimeCalculation(time: datetime.time) -> datetime.datetime:
        """
        Create a datetime with a generic date for time calculations.
        
        Args:
            time: Time object to use
            
        Returns:
            Datetime with generic date and specified time
        """
        return datetime.datetime(2000, 1, 1, time.hour, time.minute)

    @staticmethod
    def subtractTimes(x: datetime.time, y: datetime.time) -> int:
        """
        Calculate minutes between two times.
        
        Args:
            x: First time
            y: Second time
            
        Returns:
            Minutes between times (always positive)
        """
        if x > y:
            x, y = y, x
        return (y.hour * 60 + y.minute) - (x.hour * 60 + x.minute)

    @staticmethod
    def subtractDates(x: datetime.date, y: datetime.date) -> int:
        """
        Calculate days between two dates.
        
        Args:
            x: First date
            y: Second date
            
        Returns:
            Days between dates (always positive)
        """
        if x > y:
            x, y = y, x
        return (y - x).days

    # =============================================================================
    # Formatting/Display Methods
    # =============================================================================

    @classmethod
    def prettify(cls, obj: datetime.date | datetime.time | None = None) -> str:
        """
        Format a date or time object as a pretty string.
        
        Args:
            obj: Date or time object to format
            
        Returns:
            Formatted string
        """
        if obj is None: 
            return cls.prettyDate()
        if cls.isDatetimeDate(obj): 
            return cls.prettyDate(obj)
        if cls.isDatetimeTime(obj):
            return cls.prettyTime(obj)
        return str(obj)

    @classmethod
    def prettyDate(cls, date: datetime.date | None = None, 
                   lenWeekday: int = 3, lenMonth: int = 3) -> str:
        """
        Format a date as a pretty string.
        
        Args:
            date: Date to format
            lenWeekday: Length to truncate weekday name
            lenMonth: Length to truncate month name
            
        Returns:
            Formatted date string
        """
        if date is None: 
            date = cls.date()
        
        string = ''

        weekday = cls.stringWeekdays()[date.weekday()]
        if lenWeekday:
            weekday = weekday[:lenWeekday]  
            string += f'{weekday}, '
        
        string += cls.prettyDay(date.day)
        
        month = cls.stringMonths()[date.month - 1]
        if lenMonth:
            month = month[:lenMonth]
        string += f' {month}, '        
        
        string += str(date.year)
        
        return string

    @classmethod
    def euDate(cls, date: datetime.date | None = None, separator: str = '-') -> str:
        """
        Format a date in European format (DD-MM-YYYY).
        
        Args:
            date: Date to format
            
        Returns:
            European format date string
        """
        if date is None: 
            date = cls.date()
        day = date.day if date.day > 9 else f'0{date.day}'
        month = date.month if date.month > 9 else f'0{date.month}'
        return f'{day}{separator}{month}{separator}{date.year}'

    @classmethod
    def prettyTime(cls, time: datetime.time | None = None, 
                   twentyFourHour: bool = True) -> str:
        """
        Format a time as a pretty string.
        
        Args:
            time: Time to format
            twentyFourHour: Whether to use 24-hour format
            
        Returns:
            Formatted time string
        """
        if time is None: 
            time = cls.time()
        
        hour = time.hour
        minute = time.minute
        prettyHour = cls.prettyHour(hour, twentyFourHour)
        prettyMinute = cls.prettyMinute(minute)
        prettyTime = f'{prettyHour}.{prettyMinute}'
        
        period = ''
        if not twentyFourHour:
            period = ' PM' if hour > 12 else ' AM'
        
        return f'{prettyTime}{period}'

    @classmethod
    def prettyHour(cls, hour: int | datetime.time | None = None, 
                   twentyFourHour: bool = True) -> str:
        """
        Format an hour value as a pretty string.
        
        Args:
            hour: Hour value or time object
            twentyFourHour: Whether to use 24-hour format
            
        Returns:
            Formatted hour string
        """
        if cls.isDatetimeTime(hour): 
            hour = hour.hour
        elif hour is None: 
            hour = cls.hour()
        
        if hour > 12 and not twentyFourHour: 
            hour = hour - 12
        
        return f'0{hour}' if hour < 10 else str(hour)

    @classmethod
    def prettyMinute(cls, minute: int | datetime.time | None = None) -> str:
        """
        Format a minute value as a pretty string.
        
        Args:
            minute: Minute value or time object
            
        Returns:
            Formatted minute string with leading zero if needed
        """
        if cls.isDatetimeTime(minute): 
            minute = minute.minute
        elif minute is None: 
            minute = cls.minute()
        
        return f'0{minute}' if minute < 10 else str(minute)

    @classmethod
    def prettyMonth(cls, month: int | datetime.date | None = None) -> str:
        """
        Get the month name for a given month value.
        
        Args:
            month: Month value, date object, or None for current month
            
        Returns:
            Month name
        """
        if cls.isDatetimeDate(month): 
            month = month.month
        elif month is None: 
            month = cls.month()
        return cls.stringMonths()[month - 1]

    @staticmethod
    def prettyDay(day: int) -> str:
        """
        Format a day value with the appropriate ordinal suffix.
        
        Args:
            day: Day value
            
        Returns:
            Day with ordinal suffix (1st, 2nd, 3rd, etc.)
        """
        day = str(day)
        if day[-1] == '1' and day != '11': 
            return day + 'st'
        if day[-1] == '2' and day != '12': 
            return day + 'nd'
        if day[-1] == '3' and day != '13': 
            return day + 'rd'
        return day + 'th'

    # =============================================================================
    # Type Conversion Utilities
    # =============================================================================

    @classmethod
    def intYear(cls, year: int | str | datetime.date | datetime.datetime) -> int | None:
        """
        Convert various year formats to integer.
        
        Args:
            year: Year value to convert
            
        Returns:
            Integer year value or None if conversion fails
        """
        if isinstance(year, int): 
            if year > 1900:
                return year
            year = str(year)

        if isinstance(year, str):
            if len(year) == 4:
                return int(year)
            if len(year) == 2:
                return int(f'20{year}')
            if len(year) == 3:
                logwarning('Returning a 3-length year as Int for date-making in dates.date()')
                return int(year)

        if isinstance(year, datetime.date) or isinstance(year, datetime.datetime):
            return int(year.year)
        
        return logerror(f'Could not convert year {year} to int for dates.date()')

    @classmethod
    def intMonth(cls, month: int | str | None) -> int | None:
        """
        Convert various month formats to integer.
        
        Args:
            month: Month value to convert (number or name)
            
        Returns:
            Integer month value (1-12) or None if conversion fails
        """
        if isinstance(month, int): 
            return month
        try: 
            return int(month)
        except (ValueError, TypeError):
            pass
        
        if isinstance(month, str):
            for string in cls.stringMonths():
                if string.lower().startswith(month.lower()):
                    return cls.stringMonths().index(string) + 1
        return None

    # =============================================================================
    # Conversion Methods
    # =============================================================================
    
    @staticmethod
    def convertToDateTime(date: datetime.date) -> datetime.datetime:
        """
        Convert a date object to a datetime object at midnight.
        
        Args:
            date: Date object to convert
            
        Returns:
            Datetime object with time set to midnight
        """
        if not isinstance(date, datetime.date):
            return logerror(f'Cannot convert {date} to datetime')
        return datetime.datetime.combine(date, datetime.time(0))

    # =============================================================================
    # Type Checking Methods
    # =============================================================================

    @staticmethod
    def isDatetimeDatetime(value: Any) -> bool:
        """
        Check if value is a datetime object.
        
        Args:
            value: Value to check
            
        Returns:
            True if value is a datetime object
        """
        return isinstance(value, datetime.datetime)

    @staticmethod
    def isDatetimeDate(value: Any) -> bool:
        """
        Check if value is a date object.
        
        Args:
            value: Value to check
            
        Returns:
            True if value is a date object
        """
        return isinstance(value, datetime.date)

    @staticmethod
    def isDatetimeTime(value: Any) -> bool:
        """
        Check if value is a time object.
        
        Args:
            value: Value to check
            
        Returns:
            True if value is a time object
        """
        return isinstance(value, datetime.time)

    # =============================================================================
    # Constants and Static Data
    # =============================================================================

    @staticmethod
    def stringMonths() -> tuple[str, ...]:
        """
        Get the list of month names.
        
        Returns:
            Tuple of month names
        """
        return (
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        )

    @staticmethod
    def stringWeekdays() -> tuple[str, ...]:
        """
        Get the list of weekday names.
        
        Returns:
            Tuple of weekday names (Monday first)
        """
        return (
            'Monday', 'Tuesday', 'Wednesday', 'Thursday', 
            'Friday', 'Saturday', 'Sunday',
        )

    def __str__(self) -> str:
        """
        Get string representation of the dates class.
        
        Returns:
            String description
        """
        return 'dates module dates class object'