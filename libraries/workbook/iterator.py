from openpyxl.worksheet.worksheet import Worksheet as Ws
from typing import Any

from libraries.utils import toList

class WorksheetIterator:
    """
    Base iterator class for traversing worksheet elements.
    Provides common functionality for row and column iterators.
    """

    def __init__(self, opxlSheet: Ws) -> None:
        """
        Initialize a worksheet iterator.
        
        Args:
            opxlSheet: OpenPyXL worksheet object to iterate over
        """
        self._sheet: Ws = opxlSheet
        self._name: str | None = None
        self._count: int = 1
        self._all: list[Any] = list()
        self._empties: int = 0
        self._maxEmptiesAllowed: int = 1
        self._defaultDelete: int = 1
        self._defaultInsert: int = 1
        self._defaultIncrease: int = 1
        self._defaultDecrease: int = 1

    # =============================================================================
    # Navigation Methods
    # =============================================================================

    def increase(self, tot: int | None = None) -> 'WorksheetIterator':
        """
        Increment the iterator counter.
        
        Args:
            tot: Number of positions to increment by
            
        Returns:
            Self for method chaining
        """
        if tot is None:
            tot = self._defaultIncrease
        if tot < 1:
            raise ValueError("Increase count must be greater than 0.")
        self._count += tot
        return self
    
    def decrease(self, tot: int | None = None) -> 'WorksheetIterator':
        """
        Decrement the iterator counter.
        
        Args:
            tot: Number of positions to decrement by
            
        Returns:
            Self for method chaining
        """
        if tot is None:
            tot = self._defaultDecrease
        if tot < 1:
            raise ValueError("Decrease count must be greater than 0.")
        self._count -= tot
        return self
    
    def reset(self) -> 'WorksheetIterator':
        """
        Reset the iterator counter to starting position (1).
        
        Returns:
            Self for method chaining
        """
        self._count = 1
        return self
    
    # =============================================================================
    # Empty Cell Tracking Methods
    # =============================================================================
    
    def increaseEmptyCount(self, tot: int = 1) -> 'WorksheetIterator':
        """
        Increment the empty cells counter.
        
        Args:
            tot: Number to increment by
            
        Returns:
            Self for method chaining
        """
        self._empties += tot
        return self
    
    def decreaseEmptyCount(self, tot: int = 1) -> 'WorksheetIterator':
        """
        Decrement the empty cells counter.
        
        Args:
            tot: Number to decrement by
            
        Returns:
            Self for method chaining
        """
        self._empties -= tot
        return self
    
    def resetEmptyCount(self) -> 'WorksheetIterator':
        """
        Reset the empty cells counter to zero.
        
        Returns:
            Self for method chaining
        """
        self._empties = 0
        return self

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def sheet(self) -> Any:
        """Get the associated OpenPyXL worksheet."""
        return self._sheet
    
    @sheet.setter
    def sheet(self, value: Any) -> None:
        """
        Set the associated OpenPyXL worksheet.
        
        Args:
            value: OpenPyXL worksheet object
        """
        self._sheet = value

    @property
    def count(self) -> int:
        """Get the current iterator position."""
        return self._count
    
    @count.setter
    def count(self, value: int) -> None:
        """
        Set the current iterator position.
        
        Args:
            value: Integer position value
        """
        self._count = value

    @property
    def number(self) -> int:
        """Get the current iterator position (alias for count)."""
        return self._count
    
    @number.setter
    def number(self, value: int) -> None:
        """
        Set the current iterator position (alias for count).
        
        Args:
            value: Integer position value
        """
        self._count = value

    @property
    def all(self) -> list[Any]:
        """Get all collected elements during iteration."""
        return self._all
    
    @all.setter
    def all(self, value: Any | list[Any]) -> None:
        """
        Add elements to the collection.
        
        Args:
            value: Element or list of elements to add
        """
        self._all += toList(value)

    @property
    def name(self) -> str | None:
        """Get the name associated with this iterator."""
        return self._name
    
    @name.setter
    def name(self, value: str) -> None:
        """
        Set the name associated with this iterator.
        
        Args:
            value: Name string
        """
        self._name = value

    @property
    def title(self) -> str | None:
        """Get the title (alias for name)."""
        return self._name
    
    @title.setter
    def title(self, value: str) -> None:
        """
        Set the title (alias for name).
        
        Args:
            value: Title string
        """
        self._name = value
    
    @property
    def emptyCount(self) -> int:
        """Get the number of empty cells encountered."""
        return self._empties
    
    @property
    def emptiesCount(self) -> int:
        """Get the number of empty cells encountered (alias for emptyCount)."""
        return self._empties
    
    @property
    def atMaxEmptyCount(self) -> bool:
        """
        Check if the maximum number of empty cells has been reached.
        
        Returns:
            True if empty count exceeds 1
        """
        return self._empties > self._maxEmptiesAllowed
    
    @property
    def atMaxEmpties(self) -> bool:
        """
        Check if the maximum number of empty cells allowed has been reached.
        
        Returns:
            True if empty count exceeds the maximum allowed
        """
        return self._empties > self._maxEmptiesAllowed
    
    @property
    def areSet(self) -> bool:
        """
        Check if any elements have been collected.
        
        Returns:
            True if elements exist in the collection
        """
        return len(self._all) > 0
    
    @property
    def maxEmptiesAllowed(self) -> int:
        """Get the maximum number of empty cells allowed."""
        return self._maxEmptiesAllowed
    
    @maxEmptiesAllowed.setter
    def maxEmptiesAllowed(self, value: int) -> None:
        """
        Set the maximum number of empty cells allowed.
        
        Args:
            value: Maximum empty cell count
        """
        self._maxEmptiesAllowed = value

    @property
    def maxEmpties(self) -> int:
        """Get the maximum number of empty cells allowed (alias for maxEmptiesAllowed)."""
        return self._maxEmptiesAllowed
    
    @maxEmpties.setter
    def maxEmpties(self, value: int) -> None:
        """
        Set the maximum number of empty cells allowed (alias for maxEmptiesAllowed).
        
        Args:
            value: Maximum empty cell count
        """
        self._maxEmptiesAllowed = value

    # =============================================================================
    # Default Value Properties
    # =============================================================================

    @property
    def defaultDelete(self) -> int:
        """Get the default delete value."""
        return self._defaultDelete

    @defaultDelete.setter
    def defaultDelete(self, value: int) -> None:
        """
        Set the default delete value.
        
        Args:
            value: Default delete value
        """
        self._defaultDelete = value

    @property
    def defaultInsert(self) -> int:
        """Get the default insert value."""
        return self._defaultInsert
 
    @defaultInsert.setter
    def defaultInsert(self, value: int) -> None:
        """
        Set the default insert value.
        
        Args:
            value: Default insert value
        """
        self._defaultInsert = value
  
    @property
    def defaultIncrease(self) -> int:
        """Get the default increase value."""
        return self._defaultIncrease
    
    @defaultIncrease.setter
    def defaultIncrease(self, value: int) -> None:
        """
        Set the default increase value.
        
        Args:
            value: Default increase value
        """
        self._defaultIncrease = value

    @property
    def defaultDecrease(self) -> int:
        """Get the default decrease value."""
        return self._defaultDecrease
    
    @defaultDecrease.setter
    def defaultDecrease(self, value: int) -> None:
        """
        Set the default decrease value.
        
        Args:
            value: Default decrease value
        """
        self._defaultDecrease = value