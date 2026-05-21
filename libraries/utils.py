import base64
import regex as re
from typing import Any, Callable, TypeVar

# Type variables for better type hinting
T = TypeVar('T')  # Can represent any type
R = TypeVar('R')  # Used for return types

# =============================================================================
# Decorator Functions
# =============================================================================

def isString(func: Callable[[str], R]) -> Callable[[Any], R | Any]:
    """
    Decorator that ensures the input is a string before passing to the function.
    
    Args:
        func: Function that expects a string argument
        
    Returns:
        Wrapped function that converts input to string if possible
    """
    def wrapper(value: Any, *args, **kwargs) -> R | Any:
        if not isinstance(value, str):
            try:
                value = str(value)
            except:
                return value
        
        return func(value.strip(), *args, **kwargs)
    return wrapper


def isStringError(func: Callable[[str], R]) -> Callable[[Any], R | None]:
    """
    Decorator that ensures the input is a string or logs an error.
    
    Args:
        func: Function that expects a string argument
        
    Returns:
        Wrapped function that logs error if input isn't a string
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        if not isinstance(value, str):
            return logerror(
                f"Expected string, got {type(value)}")

        return func(value.strip(), *args, **kwargs)
    return wrapper


def isInt(func: Callable[[int], R]) -> Callable[[Any], R | None]:
    """
    Decorator that converts input to int before passing to the function.
    
    Args:
        func: Function that expects an integer argument
        
    Returns:
        Wrapped function that returns None if conversion fails
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        try:
            return func(int(value), *args, **kwargs)
        except:
            return None
    return wrapper


def isIntError(func: Callable[[int], R]) -> Callable[[Any], R | None]:
    """
    Decorator that converts input to int or logs an error.
    
    Args:
        func: Function that expects an integer argument
        
    Returns:
        Wrapped function that logs error if conversion fails
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        try:
            return func(int(value), *args, **kwargs)
        except:
            return logerror(
                f"Expected integer, got {type(value)}")
    return wrapper


def isFloat(func: Callable[[float], R]) -> Callable[[Any], R | None]:
    """
    Decorator that converts input to float before passing to the function.
    
    Args:
        func: Function that expects a float argument
        
    Returns:
        Wrapped function that returns None if conversion fails
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        try:
            return func(float(value), *args, **kwargs)
        except:
            return None
    return wrapper


def isFloatError(func: Callable[[float], R]) -> Callable[[Any], R | None]:
    """
    Decorator that converts input to float or logs an error.
    
    Args:
        func: Function that expects a float argument
        
    Returns:
        Wrapped function that logs error if conversion fails
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        try:
            return func(float(value), *args, **kwargs)
        except:
            return logerror(
                f"Expected float, got {type(value)}")
    return wrapper


def isBool(func: Callable[[bool], R]) -> Callable[[Any], R | None]:
    """
    Decorator that converts input to bool before passing to the function.
    
    Args:
        func: Function that expects a boolean argument
        
    Returns:
        Wrapped function that returns None if conversion fails
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        try:
            return func(bool(int(value)), *args, **kwargs)
        except:
            return None
    return wrapper


def isBoolError(func: Callable[[bool], R]) -> Callable[[Any], R | None]:
    """
    Decorator that converts input to bool or logs an error.
    
    Args:
        func: Function that expects a boolean argument
        
    Returns:
        Wrapped function that logs error if conversion fails
    """
    def wrapper(value: Any, *args, **kwargs) -> R | None:
        try:
            return func(bool(int(value)), *args, **kwargs)
        except:
            return logerror(
                f"Expected boolean, got {type(value)}")
    return wrapper


def isList(func: Callable[[list[Any]], R]) -> Callable[[Any], R]:
    """
    Decorator that ensures the input is a list before passing to the function.
    
    Args:
        func: Function that expects a list argument
        
    Returns:
        Wrapped function that converts input to list if necessary
    """
    def wrapper(value: Any, *args, **kwargs) -> R:
        if value is None:
            return func([], *args, **kwargs)
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, list):
            value = [value]
        return func(value, *args, **kwargs)
    return wrapper


def isListError(func: Callable[[list[Any]], R]) -> Callable[[Any], R]:
    """
    Decorator that ensures the input is a list or raises an error.
    
    Args:
        func: Function that expects a list argument
        
    Returns:
        Wrapped function that raises exception if input isn't a list
    """
    def wrapper(value: Any, *args: Any, **kwargs: Any) -> R:
        if not isinstance(value, list):
            raise Exception(
                f'Gave non-list object {type(value)} to {func.__name__}')
        
        return func(value, *args, **kwargs)
    return wrapper


def prettyPrinter(func: Callable[[str], str]) -> Callable[[str], None]:
    """
    Decorator that pretty prints the result of a function.
    
    Args:
        func: Function that returns a string
        
    Returns:
        Wrapped function that prints the result and returns None
    """
    def wrapper(value: str, *args: Any, **kwargs: Any) -> None:
        newlines = kwargs.get('newlines', 0)
        tabs = kwargs.get('tabs', 0)
        content = func(value, *args, **kwargs)
        pretty_print(content, newlines=newlines, tabs=tabs)
        return None
    return wrapper

# =============================================================================
# Printing Functions
# =============================================================================

def pretty_print(content: str, newlines: int = 0, tabs: int = 0) -> None:
    """
    Prints content with optional newlines and tabs.
    
    Args:
        content: Text to print
        newlines: Number of newlines to prepend
        tabs: Number of tabs to prepend
        
    Returns:
        None
    """
    newlines_str = '\n' * newlines
    tabs_str = '\t' * tabs
    print(newlines_str + tabs_str + content)
    return None


def superlog(message: str, newlines: int = 2, tabs: int = 2) -> None:
    """
    Logs a message with extra emphasis.
    
    Args:
        message: Message to log
        newlines: Number of newlines to prepend
        tabs: Number of tabs to prepend
        
    Returns:
        None
    """
    return pretty_print(message + '\n', newlines=newlines, tabs=tabs)


def log(*messages: Any, newlines: int = 1, tabs: int = 2) -> None:
    """
    Logs multiple messages separated by a delimiter.
    
    Args:
        *messages: Messages to log
        newlines: Number of newlines to prepend
        tabs: Number of tabs to prepend
        
    Returns:
        None
    """
    message_str = " // ".join(list(map(str, messages)))
    return pretty_print(message_str, newlines=newlines, tabs=tabs)


def loginput(*messages: Any, tabs: int = 2) -> None:
    """
    Logs multiple messages separated by a delimiter.
    
    Args:
        *messages: Messages to log
        tabs: Number of tabs to prepend
        
    Returns:
        None
    """
    message_str = " // ".join(list(map(str, messages)))
    input(f'{"\t" * tabs}{message_str}\n')


def sublog(message: Any, tabs: int = 2) -> None:
    """
    Logs a message as a sub-item with a dash prefix.
    
    Args:
        message: Message to log
        tabs: Number of tabs to prepend
        
    Returns:
        None
    """
    return pretty_print(f'- {message}', tabs=tabs)


def logdivider(size: int = 30) -> None:
    """
    Prints a divider line of equal signs.
    
    Args:
        size: Length of the divider
        
    Returns:
        None
    """
    return log("=" * size)


def logtest(message: str) -> None:
    """
    Logs a test mode message.
    
    Args:
        message: Message to log
        
    Returns:
        None
    """
    return log('TEST MODE: Would ' + message)


def logerror(message: str) -> None:
    """
    Logs an error message.
    
    Args:
        message: Error message to log
        
    Returns:
        None
    """
    return log('ERROR: ' + message)


def logwarning(message: str) -> None:
    """
    Logs a warning message.
    
    Args:
        message: Warning message to log
        
    Returns:
        None
    """
    return log('WARNING: ' + message)

# =============================================================================
# String Utility Functions
# =============================================================================

@isList
def toList(value: Any) -> list[Any]:
    """
    Ensures a value is returned as a list.
    
    Args:
        value: Value to convert to list
        
    Returns:
        Value as a list
    """
    return value


@isStringError
def to_string_or_error(value: Any) -> str:
    """
    Converts value to string or returns an error.
    
    Args:
        value: Value to convert to string
        
    Returns:
        String value or error message
    """
    return value


@isString
def is_time_string(value: Any) -> bool:
    """
    Checks if a string matches the time format HH:MM:SS.
    
    Args:
        value: String to check
        
    Returns:
        True if string matches time format, False otherwise
    """
    return bool(re.match(r'^\d{2}:\d{2}:\d{2}$', value))


@isString
def is_date_string(value: Any) -> bool:
    """
    Checks if a string matches the date format YYYY-MM-DD.
    
    Args:
        value: String to check
        
    Returns:
        True if string matches date format, False otherwise
    """
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', value))


@isString
def only_digits_in_string(value: Any) -> str:
    """
    Extracts only the digits from a string.
    
    Args:
        value: String to process
        
    Returns:
        String containing only digits
    """
    return ''.join([char for char in value if char.isdigit()])


@isString
def string_to_int(value: Any) -> int | None:
    """
    Extracts the first integer number from a string.
    
    Args:
        value: String to process
        
    Returns:
        First integer found or None if no integer found
    """
    search = re.search(r'([0-9]+)', value)
    if search: 
        return int(search.group(1))
    return None


@isString
def string_to_float(value: Any) -> float | None:
    """
    Converts a string to float by keeping only decimal digits and dots.
    
    Args:
        value: String to convert
        
    Returns:
        Float value or None if no decimal found
    """
    string = ''.join([char for char in value if char.isdecimal() or char == '.'])
    if not string: 
        return None
    return float(string)


@isString
def string_is_affirmative(value: Any) -> bool:
    """
    Checks if a string represents an affirmative response.
    
    Args:
        value: String to check
        
    Returns:
        True if string is affirmative, False otherwise
    """
    try:
        return bool(int(value))
    except:
        return bool(re.search(r'^y[e]?[s]?', value.lower()))


@isStringError
def break_up_person_names(value: Any) -> tuple[str, str]:
    """
    Separates a full name into first names and last name.
    
    Args:
        value: Full name string
        
    Returns:
        Tuple of (first names, last name)
    """
    names = value.title().split()
    first_names = names[:-1]
    last_name = names[-1]
    return ' '.join(first_names).title(), last_name.title()


def gen_hex(first: int | str, last: int | str) -> str:
    """
    Generates a hexadecimal string from two integers.
    
    Args:
        first: First integer
        last: Last integer
        
    Returns:
        Hexadecimal string in the format '{000}{first}{000}{last}'
    """
    gen = lambda x: ''.join(['0' for _ in range(len(str(x)), 4)]) + str(x)
    return f'{gen(first)}{gen(last)}'


@isString
def check_hex(value: str) -> bool:
    """
    Checks if a string is a valid hexadecimal number.
    
    Args:
        value: String to check
        
    Returns:
        True if string is a valid hex number, False otherwise
    """
    if not len(value) == 8:
        return False
    return bool(re.match(r'^[0-9a-fA-F]+$', value))


@isString
def convert_to_base_64(string: str) -> str:
    """    
    Converts a string to Base64 encoding.

    Args:
        string: String to convert   
    
    Returns:
        Base64 encoded string
    """
    asBytes = string.encode('ascii')
    asB64Bytes = base64.b64encode(asBytes)
    asB64String = asB64Bytes.decode('ascii')
    return asB64String


@isString
def set_euro_currency(value: str, decimalPlaces: int = 2) -> str:
    """
    Formats a string to represent a Euro currency value.
    
    Args:
        value: String to format
        decimalPlaces: Number of decimal places to include

    Returns:
        Formatted string with Euro symbol and specified decimal places
    """
    if not value:
        if decimalPlaces == 0:
            return '€0'
        return f'€0.{"0" * decimalPlaces}'

    try:
        value = float(value)
    except ValueError:
        if decimalPlaces == 0:
            return '€0'
        return f'€0.{"0" * decimalPlaces}'

    return f'€{value:,.{decimalPlaces}f}'


# =============================================================================
# Object Base Class
# =============================================================================

class Object:
    """
    Base object class with dictionary-like behavior for attribute storage.
    
    Args:
        load: Dictionary of initial values
        id: Object identifier
        name: Object name
        TEST: Test mode flag
        
    Attributes:
        _values: Dictionary storing object attributes
        TEST: Test mode flag
    """

    def __init__(
            self,
            load: dict[str, Any] = None,
            id: str | int = None,
            name: str = None,
            TEST: bool = False) -> None:
        """
        Initialize the object with optional values.
        
        """
        self._values: dict[str, Any] = load if load else {}
    
        if id and id not in self._values:
            self._values['id'] = id
    
        if name and name not in self._values:
            self._values['name'] = name
    
        self.TEST: bool = TEST

    def get(self) -> dict[str, Any]:
        """
        Get all stored values as dictionary.
        
        Returns:
            Dictionary of all values
        """
        return self._values
    
    def set(self, value: dict[str, Any] = None) -> 'Object':
        """
        Set all values from dictionary.
        
        Args:
            value: Dictionary of values to set
            
        Returns:
            Self for method chaining
        """
        self._values = value
        return self

    def has(self, key: str) -> bool:
        """
        Check if a key exists in the values.
        
        Args:
            key: Key to check
            
        Returns:
            True if key exists, False otherwise
            
        Raises:
            ValueError: If values dictionary is not initialized
        """
        if self._values is None:
            raise ValueError(
                f'Cannot check key. No values dictionary found.')
    
        if key not in self._values:
            return False
    
        return True

    def _get(self, key: str) -> Any:
        """
        Get a value by key.
        
        Args:
            key: Key to retrieve
            
        Returns:
            Value for the key
            
        Raises:
            ValueError: If values dictionary is not initialized
            KeyError: If key not found in values
        """
        if self._values is None:
            raise ValueError(
                f'Cannot get value. No values dictionary found.')
   
        if key not in self._values:
            raise KeyError(
                f'Key "{key}" not found in values: {self._values}')
     
        return self._values.get(key)
    
    def _set(self, key: str, value: Any) -> None:
        """
        Set a value by key.
        
        Args:
            key: Key to set
            value: Value to set
            
        Raises:
            ValueError: If values dictionary is not initialized
        """
        if self._values is None:
            raise ValueError(
                f'Cannot set value. No values dictionary found.')
     
        self._values[key] = value

    def _delete(self, key: str) -> None:
        """
        Delete a value by key.
        
        Args:
            key: Key to delete
            
        Raises:
            ValueError: If values dictionary is not initialized
            KeyError: If key not found in values
        """
        if self._values is None:
            raise ValueError(
                f'Cannot delete value. No values dictionary found.')

        if key not in self._values:
            raise KeyError(
                f'Key "{key}" not found in values: {self._values}')

        del self._values[key]

    @property
    def id(self) -> str | int:
        """
        Get the object identifier.
        
        Returns:
            Object identifier
        """
        return self._get('id')

    @id.setter
    def id(self, value: str | int) -> None:
        """
        Set the object identifier.
        
        Args:
            value: New identifier value
        """
        return self._set('id', value)
    
    @id.deleter
    def id(self) -> None:
        """
        Delete the object identifier.
        
        Raises:
            ValueError: If values dictionary is not initialized
            KeyError: If 'id' key not found in values
        """
        return self._delete('id')

    @property
    def name(self) -> str:
        """
        Get the object name.
        
        Returns:
            Object name
        """
        return self._get('name')

    @name.setter
    def name(self, value: str) -> None:
        """
        Set the object name.
        
        Args:
            value: New name value
        """
        return self._set('name', value)
    
    @property
    def values(self) -> dict[str, Any]:
        """
        Get the values dictionary.
        
        Returns:
            Dictionary of all values
        """
        return self._values
    
    def reset(self) -> None:
        """
        Reset the object values to an empty dictionary.
        
        Returns:
            None
        """
        self._values = {}
        return self
    
    def __str__(self) -> str:
        """
        String representation of the object.
        
        Returns:
            String representing the object class and values
        """
        return f'{self.__class__.__name__}({self._values})'
    
    def __iter__(self):
        """
        Get an iterator over the object values.
        """
        return iter(self._values.items())


class ObjectWithDefaults(Object):
    """
    Object with default values for certain attributes.
    """

    def __init__(
            self, 
            load: dict[str, Any] | None = None,
            id: str | int | None = None,
            name: str | None = None,
            TEST: str | None = None) -> None:
        """
        Initialize the Object Class with values.

        Args:
            load: Dictionary of values to load
            id: Object identifier
            name: Object name
            TEST: Test flag
        """
        super().__init__(load=load, id=id, name=name, TEST=TEST)

    def _get(self, key, default=None):
        """
        Get a value by key, returning a default if not found.

        Args:
            key: Key to retrieve
            default: Default value to return if key is not found

        Returns:
            Value for the key or default if not found
        """
        return self._values.get(key, default)
    
    def _delete(self, key):
        """
        Delete a value by key.

        Args:
            key: Key to delete

        Raises:
            ValueError: If values dictionary is not initialized
            KeyError: If key not found in values
        """
        if self._values is None:
            raise ValueError(
                f'Cannot delete value. No values dictionary found.')

        if key in self._values:
            del self._values[key]


# =============================================================================
# Requests and API Utilities
# =============================================================================

def generate_request_headers(secretKey: str = None, **kwargs) -> dict:
  """
    Generate headers for Revolut API requests.
  
    Args:
        secretKey: The API secret key for authentication. If None, the default from settings will be used.
        apiVersion: The version of the Revolut API to use.
    Returns:
        A dictionary of headers to include in the API request.
  """
  return {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Authorization': f'Bearer {secretKey}',
    **kwargs
  }