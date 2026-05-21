import datetime

from libraries.google.connect import GoogleAPIService
from libraries.dates import dates
from typing import Any
from libraries.utils import (
    Object,
    logtest
)


class GoogleCalendar(Object):
    """
    Represents a Google Calendar with methods for calendar operations.
    
    This class provides functionality to create, retrieve, update, delete,
    and manage Google Calendars and their events.
    
    Attributes:
        connection: Google API service connection
        summary: Title/name of the calendar
        description: Description of the calendar
        colorId: Color identifier for the calendar
        events: Events collection for this calendar
    """

    class Datetime(Object):
        """
        Represents a datetime object for Google Calendar events.
        
        Handles conversion between different datetime formats used by Google Calendar API.
        
        Attributes:
            date: The date component
            time: The time component
            dateTime: Combined date and time in ISO format
            timezone: Timezone string
        """

        def __init__(
                self, 
                load: dict | None = None, 
                isEnd: bool = False) -> None:
            """
            Initialize a Google Calendar Datetime object.
            
            Args:
                load: Dictionary containing datetime data to load
                isEnd: Flag indicating if this is an end datetime (affects defaults)
            """
            super().__init__(load=load)
            self._isEnd = isEnd

        @property
        def date(self) -> datetime.date | None:
            """
            Get the date component.
            
            Returns:
                Date object or None if not set
            """
            if 'date' in self._values:
                return self._get('date')
            if 'dateTime' in self._values:
                return dates.fromIsoFormat(self._get('dateTime')).date()
            return None
        
        @date.setter
        def date(self, value: datetime.date) -> None:
            """
            Set the date component.
            
            Args:
                value: datetime.date object
            """
            self._set('date', value)

        @property
        def time(self) -> datetime.time | None:
            """
            Get the time component.
            
            Returns:
                Time object or None if not set
            """
            if 'time' in self._values:
                return self._get('time')
            if 'dateTime' in self._values:
                return dates.fromIsoFormat(self._get('dateTime')).time()
            return None
        
        @time.setter
        def time(self, value: datetime.time) -> None:
            """
            Set the time component.
            
            Args:
                value: Time object
            """
            self._set('time', value)

        @property
        def dateTime(self) -> str:
            """
            Get the combined date and time in ISO format.
            
            Returns:
                ISO formatted datetime string
            """
            if 'dateTime' in self._values:
                return self._get('dateTime')
            return self._converted_to_cal_datetime()
        
        @dateTime.setter
        def dateTime(self, value: datetime.datetime) -> None:
            """
            Set the combined date and time.
            
            Args:
                value: Datetime object
                
            Raises:
                ValueError: If value is not a datetime object
            """
            if not isinstance(value, datetime):
                raise ValueError(
                    f'Value must be a datetime object. Got {type(value)} instead.')
            self.date = value.date()
            self.time = value.time()
            self._set('dateTime', self._converted_to_cal_datetime())
        
        @property
        def timezone(self) -> str:
            """
            Get the timezone string.
            
            Returns:
                Timezone string
            """
            return self._get('timeZone')
        
        @timezone.setter
        def timezone(self, value: str) -> None:
            """
            Set the timezone string.
            
            Args:
                value: Timezone string
            """
            self._set('timeZone', value)

        def get(self) -> dict:
            """
            Get the datetime values in API-compatible format.
            
            Returns:
                Dictionary containing dateTime and timeZone values
            """
            if 'date' in self._values:
                dateTime = self._converted_to_cal_datetime()
            else:
                dateTime = self.dateTime
        
            return {
                'dateTime': dateTime,
                'timeZone': self.timezone
            }
        
        def _converted_to_cal_datetime(self) -> str:
            """
            Convert internal date and time values to calendar datetime format.
            
            Returns:
                Formatted datetime string with timezone offset
            """
            currentDate = self.date
            currentTime = self.time

            if dates.isDatetimeDatetime(currentDate): 
                currentDate, currentTime = currentDate.date(), currentDate.time()
            elif currentTime is None and self._isEnd:
                currentDate, currentTime = dates.calculate(date=currentDate, days=1), dates.time(0)
            elif currentTime is None:
                currentTime = dates.time(0)

            if 3 < currentDate.month < 11:
                daylightSaving = '+01:00'
            else:
                daylightSaving = '+00:00'
            return f'{currentDate}T{currentTime}{daylightSaving}'


    class Events(Object):
        """
        Collection of events in a Google Calendar.
        
        Provides methods for retrieving and filtering calendar events.
        
        Attributes:
            start: Start datetime for filtering events
            end: End datetime for filtering events
            list: List of events matching the filter criteria
        """

        def __init__(
                self, 
                service, 
                calendarId: str | None = None) -> None:
            """
            Initialize a Google Calendar Events collection.
            
            Args:
                service: Google API service connection
                calendarId: Calendar ID to fetch events from
            """
            super().__init__()
            self._service = service
            self._calId = calendarId
            self._start = GoogleCalendar.Datetime()
            self._end = GoogleCalendar.Datetime(isEnd=True)

        @property
        def start(self) -> 'GoogleCalendar.Datetime':
            """
            Get the start datetime for filtering events.
            
            Returns:
                Datetime object for manipulating the start time
            """
            return self._start
        
        @property
        def end(self) -> 'GoogleCalendar.Datetime':
            """
            Get the end datetime for filtering events.
            
            Returns:
                Datetime object for manipulating the end time
            """
            return self._end
      
        @property
        def list(self) -> list['GoogleCalendar.Event']:
            """
            Get events matching the current filter criteria.
            
            Returns:
                List of Event objects matching the criteria
            """
            results = self._service.events().list(
                                        calendarId=self._calId, 
                                        timeMin=self._start.dateTime,
                                        timeMax=self._end.dateTime, 
                                        singleEvents=True).execute().get('items', [])
            if not results:
                return []
          
            return [GoogleCalendar.Event(
                        self._service,
                        load=x, 
                        calendarId=self._calId) for x in results]
        

    class Event(Object):
        """
        Represents an event in a Google Calendar.
        
        Provides methods for creating, updating, and deleting calendar events.
        
        Attributes:
            summary: Title/name of the event
            description: Description of the event
            start: Start datetime of the event
            end: End datetime of the event
            location: Location of the event
            colorId: Color identifier for the event
            calendarId: ID of the calendar containing this event
        """

        def __init__(
                self, 
                service, 
                load: dict | None = None, 
                calendarId: str | None = None, 
                TEST: bool = False) -> None:
            """
            Initialize a Google Calendar Event object.
            
            Args:
                service: Google API service connection
                load: Dictionary containing event data to load
                calendarId: ID of the calendar containing this event
                TEST: Flag for test mode
            """
            super().__init__(load=load, TEST=TEST)
            self._service = service
            self._start = GoogleCalendar.Datetime(load=self._values.get('start', None))
            self._end = GoogleCalendar.Datetime(load=self._values.get('end', None), isEnd=True)
            if calendarId:
                self.calendarId = calendarId

        @property
        def connection(self) -> GoogleAPIService:
            """
            Get the Google API service connection.
            
            Returns:
                GoogleAPIService object
            """
            return self._connection
        
        @connection.setter
        def connection(self, value: GoogleAPIService) -> None:
            """
            Set the Google API service connection.
            
            Args:
                value: GoogleAPIService connection object
            """
            self._connection = value
            self._service = value.connection if value else None
        
        @property
        def hasConnection(self) -> bool:
            """
            Check if the event has a valid API connection.
            
            Returns:
                True if connected, False otherwise
            """
            return self._connection is not None
        
        @property
        def summary(self) -> str:
            """
            Get the summary (title/name) of the event.
            
            Returns:
                Summary string
            """
            return self._get('summary')
        
        @summary.setter
        def summary(self, value: str) -> None:
            """
            Set the summary (title/name) of the event.
            
            Args:
                value: Summary string
            """
            self._set('summary', value)

        @property
        def description(self) -> str:
            """
            Get the description of the event.
            
            Returns:
                Description string
            """
            return self._get('description')
        
        @description.setter
        def description(self, value: str) -> None:
            """
            Set the description of the event.
            
            Args:
                value: Description string
            """
            self._set('description', value)

        @property
        def start(self) -> 'GoogleCalendar.Datetime':
            """
            Get the start datetime of the event.
            
            Returns:
                Datetime object for the event start
            """
            return self._start

        @property
        def end(self) -> 'GoogleCalendar.Datetime':
            """
            Get the end datetime of the event.
            
            Returns:
                Datetime object for the event end
            """
            return self._end
        
        @property
        def calendarId(self) -> str:
            """
            Get the ID of the calendar containing this event.
            
            Returns:
                Calendar ID string
            """
            return self._get('calendarId')

        @calendarId.setter
        def calendarId(self, value: str) -> None:
            """
            Set the ID of the calendar containing this event.
            
            Args:
                value: Calendar ID string
            """
            self._set('calendarId', value)

        @property
        def location(self) -> str:
            """
            Get the location of the event.
            
            Returns:
                Location string
            """
            return self._get('location')

        @location.setter
        def location(self, value: str) -> None:
            """
            Set the location of the event.
            
            Args:
                value: Location string
            """
            self._set('location', value)

        @property
        def colorId(self) -> str:
            """
            Get the color identifier for the event.
            
            Returns:
                Color ID string
            """
            return self._get('colorId')
        
        @colorId.setter
        def colorId(self, value: str) -> None:
            """
            Set the color identifier for the event.
            
            Args:
                value: Color ID string
            """
            self._set('colorId', value)

        def get(self) -> dict:
            """
            Get the event data in API-compatible format.
            
            Returns:
                Dictionary containing event data
            """
            self._values.update(
                {
                    'start': self._start.get(),
                    'end': self._end.get(),
                }
            )
            return super().get()

        def delete(self) -> 'GoogleCalendar.Event':
            """
            Delete this event from Google Calendar.
            
            Returns:
                Self reference for method chaining
            """
            if not self.TEST: 
                self._service.events().delete(
                    calendarId=self.calendarId, 
                    eventId=self.id
                ).execute()
            else: 
                logtest(f'delete event {self.summary}')
            return self
        
        def update(self) -> 'GoogleCalendar.Event':
            """
            Update this event in Google Calendar.
            
            Returns:
                Self reference for method chaining
            """
            if not self.TEST: 
                self._service.events().update(
                    calendarId=self.calendarId, 
                    eventId=self.id, 
                    body=self.get()
                ).execute()
            else: 
                logtest(f'update event {self.summary}')
                logtest(f'update with body: {self.get()}')
            return self

        def insert(self) -> 'GoogleCalendar.Event':
            """
            Insert this event into Google Calendar.
            
            Returns:
                Self reference for method chaining
            """
            if not self.TEST:
                self._service.events().insert(
                    calendarId=self.calendarId, 
                    body=self.get()
                ).execute()
            else: 
                logtest(f'create event {self.summary}')
                logtest(f'insert with body: {self.get()}')
            return self
        
        def exists(self) -> bool:
            """
            Check if this event exists in Google Calendar.
            
            Returns:
                True if the event exists, False otherwise
            """
            if self.has('id'):
                return True
            
            events = GoogleCalendar.Events(
                service=self._service, 
                calendarId=self.calendarId
            )
            events.start = self.start
            events.end = self.end
            for event in events.list:
                if event.summary == self.summary:
                    if event.start.dateTime == self.start.dateTime:
                        self.id = event.id
                        return True
            return False

        def __getattr__(self, attr: str) -> Any:
            """
            Handle getting special attributes.
            
            Args:
                attr: Name of the attribute to get
                
            Returns:
                Attribute value
            """
            if attr == 'colourId':
                return self._get('colorId')
            elif attr == 'name':
                return self._get('summary')
            return super().__getattr__(attr)
        
        def __setattr__(self, attr: str, value: Any) -> None:
            """
            Handle setting special attributes.
            
            Args:
                attr: Name of the attribute to set
                value: New value for the attribute
            """
            if attr == 'colourId':
                return self._set('colorId', value)
            elif attr == 'name':
                return self._set('summary', value)
            return super().__setattr__(attr, value)

    # =============================================================================
    # GoogleCalendar Main Class Implementation
    # =============================================================================

    def __init__(
            self, 
            connection: GoogleAPIService, 
            load: dict | None = None, 
            TEST: bool = False) -> None:
        """
        Initialize a Google Calendar object.
        
        Args:
            connection: Connection to Google API service
            load: Dictionary containing calendar data to load
            TEST: Flag for test mode
        """
        super().__init__(load=load, TEST=TEST)
        self._connection = connection
        self._service = connection.connection if connection else None

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def connection(self) -> GoogleAPIService:
        """
        Get the Google API service connection.
        
        Returns:
            GoogleAPIService object
        """
        return self._connection
    
    @connection.setter
    def connection(self, value: GoogleAPIService) -> 'GoogleCalendar':
        """
        Set the Google API service connection.
        
        Args:
            value: GoogleAPIService connection object
            
        Returns:
            Self reference for method chaining
        """
        self._connection = value
        self._service = value.connection if value else None
        return self
    
    @property
    def summary(self) -> str:
        """
        Get the summary (title/name) of the calendar.
        
        Returns:
            Summary string
        """
        return self._get('summary')
    
    @summary.setter
    def summary(self, value: str) -> None:
        """
        Set the summary (title/name) of the calendar.
        
        Args:
            value: Summary string
        """
        self._set('summary', value)

    @property
    def description(self) -> str:
        """
        Get the description of the calendar.
        
        Returns:
            Description string
        """
        return self._get('description')
    
    @description.setter
    def description(self, value: str) -> None:
        """
        Set the description of the calendar.
        
        Args:
            value: Description string
        """
        self._set('description', value)

    @property
    def colorId(self) -> str:
        """
        Get the color identifier for the calendar.
        
        Returns:
            Color ID string
        """
        return self._get('colorId')

    @colorId.setter
    def colorId(self, value: str) -> None:
        """
        Set the color identifier for the calendar.
        
        Args:
            value: Color ID string
        """
        self._set('colorId', value)

    @property
    def events(self) -> 'GoogleCalendar.Events':
        """
        Get the events collection for this calendar.
        
        Returns:
            Events object for managing calendar events
        """
        return GoogleCalendar.Events(
            service=self._service, 
            calendarId=self.id
        )
    
    # =============================================================================
    # Calendar Methods
    # =============================================================================

    def delete(self) -> Any:
        """
        Delete this calendar from Google Calendar.
        
        Returns:
            API response or test log message
        """
        if self.TEST: 
            return logtest(f'delete calendar {self.summary}')
        return self._service.calendars().clear(calendarId=self.id).execute()
    
    def update(self) -> Any:
        """
        Update this calendar in Google Calendar.
        
        Returns:
            API response or test log message
        """
        if self.TEST: 
            return logtest(f'update calendar {self.summary}')
        return self._service.calendars().update(calendarId=self.id, body=self.get()).execute()

    def insert(self) -> Any:
        """
        Insert this calendar into Google Calendar.
        
        Returns:
            API response or test log message
        """
        if self.TEST: 
            return logtest(f'insert/create calendar {self.summary}')
        return self._service.calendars().insert(body=self.get()).execute()

    def event(self) -> 'GoogleCalendar.Event':
        """
        Create a new event associated with this calendar.
        
        Returns:
            New Event object
        """
        return GoogleCalendar.Event(
                                service=self._service, 
                                calendarId=self.id, 
                                TEST=self.TEST
        )
  
    # =============================================================================
    # Magic Methods
    # =============================================================================
    
    def __getattr__(self, attr: str) -> Any:
        """
        Handle getting special attributes.
        
        Args:
            attr: Name of the attribute to get
            
        Returns:
            Attribute value
        """
        if attr == 'colourId':
            return self.colorId
        elif attr == 'name':
            return self.summary
        elif attr == 'calendarId':
            return self.id
        return super().__getattr__(attr)
    
    def __setattr__(self, attr: str, value: Any) -> None:
        """
        Handle setting special attributes.
        
        Args:
            attr: Name of the attribute to set
            value: New value for the attribute
        """
        if attr == 'colourId':
            self.colorId = value
            return
        elif attr == 'name':
            self.summary = value
            return
        elif attr == 'calendarId':
            self.id = value
            return
        return super().__setattr__(attr, value)

    def __str__(self) -> str:
        """
        Get string representation of this calendar.
        
        Returns:
            String representation of calendar data
        """
        return str(self.get())