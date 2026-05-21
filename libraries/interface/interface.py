import datetime
from typing import Any

from libraries.dates import dates
from libraries.utils import logerror, prettyPrinter


class Interface:
    """
    A console-based user interface class that handles formatting, input collection,
    and display of information with proper indentation and sectioning.
    
    Args:
        indent: The initial indentation level
        title: The title to display when opening the interface
        divider: The length of divider lines
    """

    def __init__(self, indent: int = 0, title: str | None = None, divider: int = 120) -> None:
        self._indent = indent
        self._divider = divider
        self._title = None if not title else self.open(title)
        self._count = 1

    # =============================================================================
    # Core UI Methods
    # =============================================================================

    def open(self, title: str | None = None) -> 'Interface':
        """
        Open the interface with a title and divider.
        
        Args:
            title: The title to display. If None, uses the stored title.
            
        Returns:
            The title string or self for method chaining
            
        Raises:
            Error if no title is provided and no stored title exists
        """
        self.divide() 
    
        if not title:
            if not self.title:
                return logerror(
                    'Tried to open() Interface class without a TITLE')
           
            self.log(f'{self.title.upper()}', newlines=2)
            return self
        self.log(f'{title.upper()}', newlines=2)
        return title
    
    def subsections(self) -> 'Interface':
        """
        Create a new Interface with increased indentation for subsections.
        
        Returns:
            A new Interface instance with indent level increased by 1
        """
        return Interface(self._indent + 1)

    def section(self, name: str | None = None) -> 'Interface':
        """
        Create a new numbered section with the given name.
        
        Args:
            name: The name of the section
            
        Returns:
            Self for method chaining
        """
        self.log(f'{self.count}) {name}', newlines=1)
        return self._increase()
   
    def close(self) -> 'Interface':
        """
        Close the interface with a closing message and divider.
        
        Returns:
            Self for method chaining
        """
        self.log('CLOSING GUI......\n', newlines=2)
        self.divide()
        return self

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def title(self) -> str | None:
        """Get the interface title."""
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        """Set the interface title."""
        self._title = value
    
    @property
    def indent(self) -> int:
        """Get the current indentation level."""
        return self._indent
    
    @indent.setter
    def indent(self, value: int) -> None:
        """Set the indentation level."""
        self._indent = value
    
    @property
    def count(self) -> int:
        """Get the current section counter."""
        return self._count
    
    @count.setter
    def count(self, value: int) -> None:
        """Set the section counter."""
        self._count = value

    # =============================================================================
    # Output Methods
    # =============================================================================

    def log(self, content: str, newlines: int = 1) -> str:
        """
        Log content to the console with proper indentation.
        
        Args:
            content: The content to log
            newlines: Number of newlines to append
            
        Returns:
            The formatted and printed content
        """
        tabs = self._section_content_indentation()
        return self._print(content, newlines=newlines, tabs=tabs)        

    def sublog(self, content: str) -> str:
        """
        Sublog content to the console with proper indentation.
        
        Args:
            content: The content to log
            
        Returns:
            The formatted and printed content
        """
        tabs = self._section_content_indentation()
        return self._print(f'- {content}', newlines=0, tabs=tabs)
        
    def logList(self, content: list[str], newlines: int = 0) -> 'Interface':
        """
        Log a list of strings with proper indentation.
        
        Args:
            content: List of strings to log
            newlines: Number of newlines to append to each item
            
        Returns:
            Self for method chaining
        """
        for item in content:
            self.log(item, newlines=newlines)
        return self

    def divide(self) -> 'Interface':
        """
        Print a divider line across the console.
        
        Returns:
            Self for method chaining
        """
        return self.log('-' * self._divider_size(), newlines=1)

    def smallDivide(self) -> 'Interface':
        """
        Print a shorter divider line across the console.
        
        Returns:
            Self for method chaining
        """
        return self.log('-' * (int(self._divider_size() // 1.3)), newlines=1)

    # =============================================================================
    # Input Methods
    # =============================================================================

    def input(self, content: str | None, newlines: int = 1) -> str | None:
        """
        Display prompt and collect user input with proper formatting.
        
        Args:
            content: The prompt text to display
            newlines: Number of newlines to append
            
        Returns:
            User input or None if user skips/exits
        """
        if content:
            self.log(content, newlines=newlines)
        tabs = self._section_content_indentation() * '\t'
        answer = input(f'\n{tabs}(ENTER to skip/exit): ')
        if answer in (None, ''):
            return None
        return answer

    def option(self, options: list[str], newlines: int = 0) -> int | None:
        """
        Display a list of options and prompt for user selection.
        
        Args:
            options: List of option strings to display
            newlines: Number of newlines to append to each option
            
        Returns:
            The selected option index (as integer) or None
        """
        for i, option in enumerate(options):
            self.log(f'{i + 1}. {option}', newlines=newlines)
        return self.integer()
    
    def date(self, prompt: str = 'TYPE date as DD-MM-YYYY') -> datetime.date | None:
        """
        Prompt for and parse a date input.
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            A date object or None if skipped
        """
        date = self.input(prompt)
        try: 
            if date is None:
                return None
            day, month, year = date.split('-')
            return self._intended_type_none_or_restart(
                *(year, month, day), _type=dates.date, 
                func=self.date, originalPrompt=prompt)
        except:
            return self._sorry(date).date()

    def time(self, prompt: str = 'TYPE time as HH.MM') -> datetime.time | None:
        """
        Prompt for and parse a time input.
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            A time object or None if skipped
        """
        time = self.input(prompt)
        try:
            if time is None:
                return None
            return self._intended_type_none_or_restart(
                *tuple(time.split('.')), _type=dates.time, 
                func=self.time, originalPrompt=prompt)
        except:
            return self._sorry(time).time()

    def text(self, prompt: str = 'TYPE here') -> str | None:
        """
        Prompt for and return text input.
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            The text input or None if skipped
        """
        answer = self.input(prompt)
        if answer is None: 
            return None
        return answer

    def integer(self, prompt: str = 'TYPE whole number without non-numeric characters') -> int | None:
        """
        Prompt for and parse an integer input.
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            The integer value or None if skipped
        """
        num = self.input(prompt)
        return self._intended_type_none_or_restart(
            num, _type=int, func=self.integer, originalPrompt=prompt)

    def float(self, prompt: str = 'TYPE float number as 0.0') -> float | None:
        """
        Prompt for and parse a float input.
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            The float value or None if skipped
        """
        num = self.input(prompt)
        return self._intended_type_none_or_restart(
            num, _type=float, func=self.float, originalPrompt=prompt)

    def bool(self, prompt: str = 'TYPE 1 for TRUE, 0 for FALSE') -> int | None:
        """
        Prompt for and parse a boolean input as an integer (1/0).
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            1 for True, 0 for False, or None if skipped
        """
        answer = self.input(prompt)
        if answer is not None:
            try:
                return int(answer)
            except:
                return self._sorry(answer).bool()
        return self._intended_type_none_or_restart(
            answer, _type=bool, func=self.bool, originalPrompt=prompt)
        
    def next(self, prompt: str = 'CONTINUE (1) or RETURN (0)?') -> int | None:
        """
        Prompt for continue/return decision.
        
        Args:
            prompt: The prompt text to display
            
        Returns:
            1 to continue, 0 to return, or None if skipped
        """
        return self.bool(prompt)

    # =============================================================================
    # Helper Methods
    # =============================================================================
    
    @prettyPrinter
    def _print(self, content: str, newlines: int = 0, tabs: int = 0) -> str:
        """
        Format and print content with proper indentation.
        
        Args:
            content: The content to print
            newlines: Number of newlines to append
            tabs: Number of tab indentations to prepend
            
        Returns:
            The formatted content
        """
        return content

    def _section_indentation(self) -> int:
        """
        Get the current section indentation level.
        
        Returns:
            The indentation level
        """
        return self._indent
    
    def _section_content_indentation(self) -> int:
        """
        Get the current content indentation level (section + 1).
        
        Returns:
            The content indentation level
        """
        return self._indent + 1
    
    def _divider_size(self) -> int:
        """
        Calculate the appropriate divider size based on indentation.
        
        Returns:
            The calculated divider size
        """
        return self._divider - (self._indent * 8)
    
    def _sorry(self, answer: str | None = None) -> 'Interface':
        """
        Display an error message for invalid input.
        
        Args:
            answer: The invalid user input
            
        Returns:
            Self for method chaining
        """
        self.log(f'Sorry couldn\'t read answer {answer}. Please try again.')
        return self
    
    def _increase(self) -> 'Interface':
        """
        Increment the section counter.
        
        Returns:
            Self for method chaining
        """
        self._count += 1
        return self
    
    def _intended_type_none_or_restart(self, *values, _type: type = None, 
                                      func: callable = None, 
                                      originalPrompt: str = '') -> Any | None:
        """
        Try to convert input to the intended type or restart the input process.
        
        Args:
            values: The values to convert
            _type: The target type to convert to
            func: The function to call if conversion fails
            originalPrompt: The original prompt to display if restarting
            
        Returns:
            The converted value or None
        """
        if not values or values[0] is None:
            return None
        try:
            return _type(*values)
        except:
            self._sorry(*values)
            return func(originalPrompt)
        
    def __str__(self) -> str:
        """
        Get a string representation of the Interface object.
        
        Returns:
            String representation with object details
        """
        return (f'INTERFACE OBJECT, count: {self._count}, indent: {self._indent}, '
                f'divider: {self._divider}, title: {self._title}')