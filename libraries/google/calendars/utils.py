from libraries.google.account import GoogleAccount
from libraries.google.calendars.calendar import GoogleCalendar
from libraries.google.connect import GoogleAPIService
from libraries.google.utils import (
    get_google_api_connection,
    sort_connection
)
from datetime import (
    date,
    datetime,
    time
)

# =============================================================================
# API Connection Functions
# =============================================================================

def get_google_calendars_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a connection to Google Calendar API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Calendar API service instance
    """
    apiKwargs = {
        'api': 'calendar', 
        'version': 'v3', 
        'scopes': ['https://www.googleapis.com/auth/calendar']
    }
    return get_google_api_connection(account, **apiKwargs)


def get_refreshed_google_calendars_connection(account: GoogleAccount | None = None) -> GoogleAPIService:
    """
    Creates and returns a fresh connection to Google Calendar API.
    
    Args:
        account: GoogleAccount instance containing authentication details
        
    Returns:
        GoogleAPIService: Connected Google Calendar API service instance
    """
    return get_google_calendars_connection(account)


# =============================================================================
# Calendar Management Functions
# =============================================================================

def get_google_calendars(
        name: str | None = None,
        account: GoogleAccount | None = None,
        connection: GoogleAPIService | None = None,
        TEST: bool = False) -> list[GoogleCalendar] | GoogleCalendar:
    """
    Retrieves Google calendars, either a specific calendar by name or a list of calendars.
    
    Args:
        name: Name of the calendar to retrieve, or None to list all calendars
        account: GoogleAccount instance for authentication
        connection: Existing GoogleAPIService connection to use
        TEST: Flag to enable test mode
        
    Returns:
        GoogleCalendar: Single calendar if name is provided and found
        list[GoogleCalendar]: List of calendars if name is not provided
    """
    connection = sort_connection(account, connection, connectionCall=get_google_calendars_connection)
    calendars = connection.connection.calendarList().list().execute().get('items', [])
    result = []
    
    for calendar in calendars:
        if name:
            if calendar['summary'] == name:
                return GoogleCalendar(connection, load=calendar, TEST=TEST)
        else:
            result.append(GoogleCalendar(connection, load=calendar, TEST=TEST))

    return result


# =============================================================================
# Event Management Functions
# =============================================================================

def get_google_calendar_events(
        calendar: GoogleCalendar | None = None,
        start: date | datetime | None = None, 
        end: date | datetime | None = None, 
        startDate: date | datetime | None = None, 
        startTime: time | None = None, 
        endDate: date | datetime | None = None, 
        endTime: time | None = None) -> list[GoogleCalendar.Event]:
    """
    Retrieves events from a Google calendar within specified time parameters.
    
    Args:
        calendar: GoogleCalendar instance to get events from
        start: Start date/time for filtering events (combined date and time)
        end: End date/time for filtering events (combined date and time)
        startDate: Start date for filtering events (date only)
        startTime: Start time for filtering events (time only)
        endDate: End date for filtering events (date only)
        endTime: End time for filtering events (time only)
        
    Returns:
        list: List of calendar events matching the specified criteria
        
    Raises:
        Exception: If calendar is not provided, not a GoogleCalendar instance,
                  or has no connection
    """
    if not calendar:
        raise Exception('No calendar provided: cannot get events.')
    if not isinstance(calendar, GoogleCalendar):
        raise Exception('Calendar is not a GoogleCalendar instance.')
    if not calendar.connection:
        raise Exception('Calendar connection is not set.')
    
    events = calendar.events
    if start:
        events.start.dateTime = start
    if end:
        events.end.dateTime = end
    if startDate:
        events.start.date = startDate
    if startTime:
        events.start.time = startTime
    if endDate:
        events.end.date = endDate
    if endTime:
        events.end.time = endTime
    return events.list