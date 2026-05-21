from typing import Any, Self

from bs4 import BeautifulSoup, SoupStrainer, Tag, ResultSet
from bs4.element import NavigableString
from libraries.utils import ObjectWithDefaults, toList, log
from libraries.web.constants import SELF_CLOSING_TAGS
from libraries.web.css import CSS, Style, parse_css_to_styles


class Element(ObjectWithDefaults):
    """
    Represents a generic HTML element.
    
    Provides methods to access and manipulate element attributes and content.
    """

    def __init__(self, name: str, attrs: dict[str, str] | None = None) -> None:
        """
        Initialize an HTML element with a name and optional attributes.
        
        Args:
            name: Name of the HTML element
            attrs: Dictionary of attributes for the element
        """
        super().__init__(load={'name': name, 'attrs': attrs or {}, 'inner': []})

    @property
    def tag(self) -> str | None:
        """
        Get the name of the HTML element.
        """
        return self._get('name')
    
    @tag.setter
    def tag(self, value: str) -> None:
        self._set('name', value)

    @tag.deleter
    def tag(self) -> None:
        """
        Remove the tag from the element.
        """
        if self._get('name'):
            del self._values['name']

    @property
    def inner(self) -> list[str | Self]:
        """
        Get the inner content of the element.
        
        Returns:
            Text content of the element
        """
        return self._get('inner', default=[])

    @inner.setter
    def inner(self, value: list | str | Self) -> None:
        """
        Set the inner content of the element.
        
        Args:
            value: Text content to set
        
        Returns:
            Self for method chaining
        """
        if not isinstance(value, list):
            value = [value]
        if not self._get('inner'):
            self._set('inner', [])
        self._get('inner').extend(value)

    @inner.deleter
    def inner(self) -> None:
        """
        Delete the inner content of the element.
        """
        if self._get('inner'):
            del self._values['inner']

    @property
    def text(self) -> str | None:
        """
        Get the text content of the element.
        """
        return self._get_text()
  
    @text.setter
    def text(self, value: str) -> None:
        """
        Set the text content of the element.
        """
        if not self._get('inner'):
            self._set('inner', [])
        self._get('inner').extend(value)

    @property
    def html(self) -> str:
        """
        Get the HTML representation of the element.
        
        Returns:
            HTML representation of the element
        """
        return self._get_html()

    @property
    def attrs(self) -> dict[str, str]:
        """
        Get the attributes of the element.
        
        Returns:
            Dictionary of attributes
        """
        return self._get('attrs', {})
    
    @attrs.setter
    def attrs(self, value: dict[str, str]) -> None:
        """
        Set the attributes of the element.
        
        Args:
            value: Dictionary of attributes to set
        
        Returns:
            Self for method chaining
        """
        self._set('attrs', value)

    @attrs.deleter
    def attrs(self) -> None:
        """
        Delete the attributes of the element.
        
        Returns:
            None
        """
        if self._get('attrs'):
            del self._values['attrs']

    @property
    def style(self) -> dict | None:
        """
        Get the style attribute of the element.
        
        Returns:
            Style attribute value or None if not set
        """
        return self._get('attrs', {}).get('style', None)
    
    @style.setter
    def style(self, value: Style) -> None:
        """
        Set the style attribute of the element.
        
        Args:
            value: Style attribute value to set
        
        Returns:
            Self for method chaining
        """
        if not self._get('attrs'):
            self._set('attrs', {})
   
        if 'style' not in self._get('attrs'):
            self._get('attrs')['style'] = ''

        self._get('attrs')['style'] = value

    @style.deleter
    def style(self) -> None:
        """
        Delete the style attribute from the element.
        
        Returns:
            None
        """
        if 'style' in self._get('attrs', {}):
            del self._get('attrs')['style']

    @property
    def classes(self) -> list[str]:
        """
        Get the class attribute of the element.
        
        Returns:
            Class attribute value or None if not set
        """
        return self._get('attrs', {}).get('class', [])

    @classes.setter
    def classes(self, value: str | list[str]) -> None:
        """
        Set the class attribute of the element.
        
        Args:
            value: Class attribute value to set
        
        Returns:
            Self for method chaining
        """
        if 'class' not in self._get('attrs', {}):
            self._get('attrs')['class'] = []
        if isinstance(value, str):
            self._get('attrs')['class'].append(value)
        else:
            self._get('attrs')['class'].extend(value)

    @classes.deleter
    def classes(self) -> None:
        """
        Delete the class attribute from the element.
        
        Returns:
            None
        """
        if 'class' in self._get('attrs', {}):
            del self._get('attrs')['class']

    @property
    def subelements(self) -> list['Element']:
        """
        Get the sub-elements of this element.
        
        Returns:
            List of sub-elements
        """
        return [sub for sub in self._get('inner', []) if isinstance(sub, Element)]
    
    @property
    def hasSubelements(self) -> bool:
        """
        Check if the element has any sub-elements.
        
        Returns:
            True if there are sub-elements, False otherwise
        """
        return len(self.subelements) > 0

    def subelement(
            self, 
            name: str, 
            attrs: dict[str, str] | None = None) -> 'Element':
        """
        Create a sub-element with the specified name and attributes.
        
        Args:
            name: Name of the sub-element
            attrs: Dictionary of attributes for the sub-element
        
        Returns:
            New Element instance representing the sub-element
        """
        sub_element = Element(name, attrs)
        self.inner = [sub_element]
        return sub_element
    
    def remove_tag(self):
        """
        Remove the tag from the element.
        """
        self._set('name', None)
        return self        

    def _get_html(self):
        """
        Write the element and its children to HTML.
        """
        html = ''
        name = self._get('name')
        
        if name:
            html += f'<{name}'
          
            for k, v in self._get('attrs', {}).items():
                if isinstance(v, Style):
                    v = v.asHTMLAttribute
                elif isinstance(v, list):
                    v = ', '.join(v)
                elif isinstance(v, dict):
                    v = '; '.join(f'{_k}: {_v}' for _k, _v in v.items())
                html += f' {k}="{v}"'
            
            html += '>'

        if name and name in SELF_CLOSING_TAGS:
            return html

        for inner in self._get('inner', []):
            html += f' {str(inner)}'
        
        if name:
            html += f'</{name}>\n'
        return html

    def _get_text(self) -> str:
        """
        Get the text content of the element.
        """
        result = ''
        for item in self._get('inner', []):
            if isinstance(item, str):
                result += item + ' '
            elif isinstance(item, Element):
                result += item.text + ' '
        return result.strip()
    
    def __str__(self):
        return self._get_html()
    

class Section(ObjectWithDefaults):
    """
    Represents a section of content within an HTML document.
    """
    def __init__(
            self, 
            name: str = None, 
            elements: list[Element] | None = None) -> None:
        super().__init__(load={'elements': elements}, name=name)

    @property
    def name(self) -> str | None:
        """
        Get the name of the section.
        """
        name = self._get('name')
        if name:
            return name
        elements = self._get('elements')
        if elements:
            return elements[0].tag
        return None
    
    @property
    def elements(self) -> list[Element]:
        """
        Get the list of elements in the section.
        """
        return self._get('elements', [])
    
    @property
    def html(self) -> str:
        """
        Get the HTML representation of the section.
        """
        return self._get_html()

    def _get_html(self) -> str:
        """Get the HTML representation of the section."""
        html = ''
        for element in self._get('elements', []):
            html += element.html
        return html

    def __str__(self) -> str:
        return self._get_html()


class HTML(ObjectWithDefaults):
    """
    HTML parsing utility class using BeautifulSoup.
    Provides methods for extracting and manipulating HTML content.
    """
        
    def __init__(
            self, 
            html: str | None = None, 
            strainElement: str | None = None, 
            strainAttribute: str | None = None, 
            strainAttributeValue: str | None = None) -> None:
        """
        Initialize an HTML parser instance.
        
        Args:
            html: HTML content to parse
            strainElement: Element type to strain parsing to (e.g., 'div', 'table')
            strainAttribute: Attribute name to match in strained elements
            strainAttributeValue: Attribute value to match in strained elements
        """
        super().__init__()
        self._html: str | None = html
        self._strainElement: str | None = strainElement
        self._strainAttribute: str | None = strainAttribute
        self._strainAttributeValue: str | None = strainAttributeValue
        self._parsed: list[Any] = list()
        self._soup: BeautifulSoup | None = self._strain(
                                                    self._strainElement,
                                                    self._strainAttribute,
                                                    self._strainAttributeValue)

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def parsed(self) -> list[Any]:
        """Get the list of parsed elements."""
        return self._parsed
    
    @parsed.setter
    def parsed(self, value: Any) -> 'HTML':
        """
        Add parsed elements to the collection.
        
        Args:
            value: Element or list of elements to add
            
        Returns:
            Self for method chaining
        """
        self._parsed += toList(value)
        return self
    
    @property
    def html(self) -> str | None:
        """Get the HTML content."""
        return self._html
    
    @html.setter
    def html(self, value: str) -> 'HTML':
        """
        Set the HTML content and update the soup.
        
        Args:
            value: HTML string content
            
        Returns:
            Self for method chaining
        """
        self._html = value
        self._soup = self._strain()
        return self
    
    @property
    def soup(self) -> BeautifulSoup | None:
        """Get the BeautifulSoup parser object."""
        return self._soup
    
    @property
    def strainElement(self) -> str | None:
        """Get the element type being strained for."""
        return self._strainElement
    
    @strainElement.setter
    def strainElement(self, value: str | None) -> 'HTML':
        """
        Set the element type to strain for and update the soup.
        
        Args:
            value: Element type (e.g., 'div', 'table')
            
        Returns:
            Self for method chaining
        """
        self._strainElement = value
        self._soup = self._strain()
        return self
    
    @property
    def strainAttribute(self) -> str | None:
        """Get the attribute name being strained for."""
        return self._strainAttribute
    
    @strainAttribute.setter
    def strainAttribute(self, value: str | None) -> 'HTML':
        """
        Set the attribute name to strain for and update the soup.
        
        Args:
            value: Attribute name to match
            
        Returns:
            Self for method chaining
        """
        self._strainAttribute = value
        self._soup = self._strain()
        return self
    
    @property
    def strainAttributeValue(self) -> str | None:
        """Get the attribute value being strained for."""
        return self._strainAttributeValue
    
    @strainAttributeValue.setter
    def strainAttributeValue(self, value: str | None) -> 'HTML':
        """
        Set the attribute value to strain for and update the soup.
        
        Args:
            value: Attribute value to match
            
        Returns:
            Self for method chaining
        """
        self._strainAttributeValue = value
        self._soup = self._strain()
        return self
    
    @property
    def styles(self) -> CSS:
        """
        Get all styles from the HTML.
        
        Returns:
            List of style strings
        """
        if not self._get('styles'):
            self._set_styles()
        return self._get('styles')
    
    @property
    def elements(self) -> list[Element]:
        """
        Get all elements from the HTML.
        """
        return parse_html_to_elements(self._soup) if self._soup else []
    
    @property
    def body(self) -> list[Element]:
        """
        Get the body of the HTML document.
        
        Returns:
            List of elements representing the body content
        """
        return self._get_section('body')
    
    @property
    def head(self) -> Section | None:
        """
        Get the head of the HTML document.
        
        Returns:
            Section representing the head content
        """
        return self._get_section('head')

    @property
    def paragraphs(self) -> ResultSet:
        """
        Get all paragraph elements from the HTML.

        Returns:
            ResultSet of paragraph elements
        """
        if self._soup:
            p_elements = self._soup.find_all('p')
            return _parse_elements(p_elements)

    @property
    def text(self) -> str:
        """
        Get all text from the HTML with whitespace normalized.
        
        Returns:
            Extracted text string
        """
        if self._soup:
            return self._soup.get_text(strip=True, separator=' ')
        return ""

    # =============================================================================
    # Parsing Methods
    # =============================================================================

    def findAll(
            self, element: str | None = None, 
            attrs: dict[str, str] | None = None) -> ResultSet:
        """
        Find all elements matching the specified criteria.
        
        Args:
            element: Element type to find
            attrs: Attributes to match
            
        Returns:
            ResultSet of matching elements
        """
        return self._soup.find_all(element, attrs=attrs)
    
    def find(
            self, 
            element: str | None = None, 
            attrs: dict[str, str] | None = None) -> Tag | None:
        """
        Find first element matching the specified criteria.
        
        Args:
            element: Element type to find
            attrs: Attributes to match
            
        Returns:
            First matching element or None
        """
        return self._soup.find(element, attrs=attrs)
    
    def tableRows(self) -> ResultSet:
        """
        Find all table rows in the HTML.
        
        Returns:
            ResultSet of table row elements
        """
        return self._soup.find_all('tr')
    
    def rowData(self, row: Tag, includeHeader: bool = True) -> list[Tag]:
        """
        Extract data cells from a table row.
        
        Args:
            row: Table row element
            includeHeader: Whether to include header cells (th)
            
        Returns:
            List of cell elements (th and/or td)
        """
        if includeHeader:
            return row.find_all('th') + row.find_all('td')
        return row.find_all('td')

    def soupValue(self, item: str) -> str:
        """
        Get the value of an input element by name.
        
        Args:
            item: Name attribute value to find
            
        Returns:
            Value attribute of the matching element
        """
        found = self._soup.find(attrs={'name': item})
        return found['value'] if found else ""

    def soupSelectedOption(self, item: str) -> Tag | None:
        """
        Find the selected option in a select element.
        
        Args:
            item: Name attribute of the select element
            
        Returns:
            Selected option element or None
        """
        select = self._soup.find('select', attrs={'name': item})
        return select.find('option', selected=True) if select else None

    def soupLink(self, text: str) -> str | None:
        """
        Find a link by its text content.
        
        Args:
            text: Text content of the link
            
        Returns:
            Href attribute of the matching link or None
        """
        link = self._soup.find('a', string=text)
        return link.get('href') if link else None

    def strain(
            self, 
            element: str | None = None, 
            attribute: str | None = None, 
            value: str | None = None) -> 'HTML':
        """
        Update straining parameters and re-parse the HTML.
        
        Args:
            element: Element type to strain for
            attribute: Attribute name to match
            value: Attribute value to match
            
        Returns:
            Self for method chaining
        """
        strainElement = element if element else self._strainElement
        strainAttribute = attribute if attribute else self._strainAttribute
        strainAttributeValue = value if value else self._strainAttributeValue
        self._soup = self._strain(
                                strainElement, 
                                strainAttribute, 
                                strainAttributeValue)
        return self
    
    def prettify(self) -> str:
        """
        Get a prettified string representation of the HTML.
        
        Returns:
            Prettified HTML string
        """
        return self._soup.prettify() if self._soup else ""
    
    # =============================================================================
    # Helper Methods
    # =============================================================================
    
    def _strain(
            self,
            element: str | None = None,
            attribute: str | None = None,
            value: str | None = None) -> BeautifulSoup | None:
        """
        Create a BeautifulSoup object with optional straining.
        
        Returns:
            BeautifulSoup object or None if HTML is empty
        """
        if not self._html:
            return None
        
        if not element:
            return BeautifulSoup(self._html, 'html.parser')
        
        strainer = SoupStrainer(element, attrs={attribute: value})
        return BeautifulSoup(self._html, 'html.parser', parse_only=strainer)

    def _set_styles(self) -> None:
        """ 
        Set the styles property by extracting style elements from the soup.
        This method finds all <style> elements in the HTML and extracts their content.
        It splits the content by new lines and strips whitespace from each line.

        Returns:
            None
        """
        result = []
        if not self._soup:
            return result
       
        # Find all style elements and extract their content
        styleElements = self._soup.find_all('style')
        styles = [element.get_text(strip=True) for element in styleElements]
        self._set('styles', parse_css_to_styles('\n'.join(styles)))

    def _get_section(self, section: str) -> Section | None:
        """
        Get a specific section of the HTML document.
        
        Args:
            section: Section name (e.g., 'head', 'body')
            
        Returns:
            Section representing the requested content
        """
        if not self._html:
            raise ValueError("HTML content is not set")
        if not self._soup:
            raise ValueError("BeautifulSoup object is not created")
        if not self._get(section):
            html = self._strain(section)
            elements = parse_html_to_elements(html)
            self._set(section, Section(section, elements))
        return self._get(section)

    def __str__(self) -> str:
        """
        Get a prettified string representation of the HTML.
        
        Returns:
            Prettified HTML string
        """
        return self._soup.prettify() if self._soup else ""
    

# =================================================================
# Parsing
# =================================================================

def parse_html_to_elements(
        html: str | HTML | BeautifulSoup) -> list[Element]:
    """
    Parse HTML string or HTML object into a list of HTML elements.

    Args:
        html: HTML string or HTML object to parse

    Returns:
        List of HTML elements
    """
    if  isinstance(html, str):
        html = HTML(html).soup
    elif isinstance(html, HTML):
        html = html.soup
   
    allText = html.get_text(strip=True, separator=' ')
    return _parse_elements(html.children, [allText])[0]


def _parse_elements(
        elements: list[Tag], 
        text: list[str] = []) -> tuple[list[Element], list[str]]:
    """
    Parse sub-elements of an HTML element.

    Args:
        element: The HTML element to parse.
        text: The list of text content to parse.

    Returns:
        A tuple containing a list of parsed HTML elements and 
        a list of remaining text content.
    """
    parsed = []

    for element in elements:
        if isinstance(element, NavigableString):
            continue

        _Element = Element(element.name, element.attrs)
        parsed.append(_Element)
        _text = element.get_text(strip=True, separator=" ")

        if _text:
            text = _split_text(text, _text)

        if element.children:
            _set_inner_element(
                            _Element,
                            *_parse_elements(element.children, [_text]))
        else:
            _Element.inner = _text

    return parsed, text


def _set_inner_element(
        Element: Element, 
        subElements, 
        elementTextList) -> None:
    """
    Set the inner content of an HTML element.

    Args:
        Element: The HTML element to set the inner content for.
        subElements: The list of sub-elements to append.
        elementTextList: The list of text content to append.

    Returns:
        None
    """
    for text in elementTextList:
        if text:
            Element.inner.append(text)
        elif subElements:
            Element.inner.append(subElements.pop(0))
    if subElements:
        Element.inner.extend(subElements)


def _split_text(text,_text):
    """
    Split the last element of the text list by the given _text.

    Args:
        text: The list of text content to modify.
        _text: The text to split the last element by.

    Returns:
        The modified list of text content.
    """
    start, end = text[-1].split(_text, maxsplit=1)
    if len(start) > 0 and len(end) > 0:
        return text[:-1] + [start.strip(), '', end.strip()]
    return text[:-1] + [start.strip(), end.strip()]


def print_elements(elements: list[Element], layer=0) -> None:
    """
    Print the names and attributes of HTML elements.

    Args:
        elements: List of HTML elements to print
    """
    for element in elements:
        log(
            f'Element: {element.name}, ' \
            f'Attributes: {element.attrs},\n' \
            f'Inner: {element.inner}', tabs=layer + 2)
        if element.hasSubelements:
            print_elements(element.subelements, layer + 1)