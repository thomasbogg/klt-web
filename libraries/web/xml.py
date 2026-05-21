from libraries.utils import Object


class XMLTreeBuilder(Object):
    """
    XML tree builder class for creating XML documents programmatically.
    
    This class provides functionality to build XML trees with nested elements,
    attributes, and values in a structured manner.
    
    Args:
        name: The name of the root XML element.
        **kwargs: Attributes to be added to the root element.
    """

    def __init__(self, name: str | None = None, **kwargs: str) -> None:
        """
        Initialize a new XMLTreeBuilder instance.
        
        Args:
            name: The name of the root XML element.
            **kwargs: Attributes to be added to the root element.
        """
        super().__init__()
        self._name: str | None = name
        self._kwargs: dict[str, str] = kwargs
        self._branches: int = 0

    @property
    def name(self) -> str | None:
        """
        Get the name of the root XML element.
        
        Returns:
            The name of the root XML element.
        """
        return self._name
    
    @name.setter
    def name(self, value: str | None) -> None:
        """
        Set the name of the root XML element.
        
        Args:
            value: The new name for the root XML element.
        """
        self._name = value

    def newBranch(self, name: str | None = None, 
                  value: str | None = None) -> 'XMLTreeBuilder':
        """
        Create a new branch in the XML tree.
        
        Args:
            name: The name of the new branch element.
            value: If provided, creates a simple key-value pair instead of 
                   a new branch.
        
        Returns:
            Either self if creating a simple value, or the new branch 
            XMLTreeBuilder instance.
        """
        if value is not None:
            self._values.update({name: value})
            return self
        
        self._branches += 1
        branch: str = f'{name}_{self._branches}'
        self._values[branch] = XMLTreeBuilder(name=name)
        return self._values[branch]        

    def get(self, elem: str | None = None) -> str:
        """
        Generate the complete XML string representation.
        
        Args:
            elem: Optional existing XML string to append to.
        
        Returns:
            The complete XML string with all elements and attributes.
        """
        if elem is None:
            elem = '<?xml version="1.0" encoding="utf-8" ?>'
        
        elem += self._open_param(self._name, **self._kwargs)
            
        for key, val in self._values.items():
            if isinstance(val, XMLTreeBuilder):
                elem = val.get(elem)
                continue
            elem += self._open_param(key)
            elem += self._set_value(val)
            elem += self._close_param(key)
    
        elem += self._close_param(self._name)
        return elem
    
    def _open_param(self, key: str, **kwargs: str) -> str:
        """
        Create an opening XML tag with optional attributes.
        
        Args:
            key: The tag name.
            **kwargs: Attributes to be added to the tag.
        
        Returns:
            The formatted opening XML tag string.
        """
        param: str = f'<{key}'
        if not kwargs:
            return param + '>'
        kwargs_str: str = ' '.join([f'{k}="{v}"' for k, v in kwargs.items()])
        return f'{param} {kwargs_str}>'

    def _close_param(self, key: str) -> str:
        """
        Create a closing XML tag.
        
        Args:
            key: The tag name.
        
        Returns:
            The formatted closing XML tag string.
        """
        return f'</{key}>'
    
    def _set_value(self, val: str) -> str:
        """
        Format a value for XML content.
        
        Args:
            val: The value to be formatted.
        
        Returns:
            The formatted value string.
        """
        return f'{val}'