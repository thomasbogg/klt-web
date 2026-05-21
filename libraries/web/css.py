from libraries.utils import ObjectWithDefaults
from libraries.web.constants import TAG_NAMES


class Style(ObjectWithDefaults):

    class Sides(ObjectWithDefaults):
        """
        Represents the four sides of a box model (top, right, bottom, left).
        """

        def __init__(self, load: dict = None, name: str = None):
            super().__init__(load=load, name=name)

        @property
        def all(self) -> str | None:
            """Get the all style."""
            return self._get('all')

        @all.setter
        def all(self, value: str) -> None:
            self._set('all', value)

        @all.deleter
        def all(self) -> None:
            self._delete('all')

        @property
        def top(self) -> str | None:
            """Get the top style."""
            return self._get('top')

        @top.setter
        def top(self, value: str) -> None:
            self._set('top', value)

        @top.deleter
        def top(self) -> None:
            self._delete('top')

        @property
        def right(self) -> str | None:
            """Get the right style."""
            return self._get('right')

        @right.setter
        def right(self, value: str) -> None:
            self._set('right', value)

        @right.deleter
        def right(self) -> None:
            self._delete('right')

        @property
        def bottom(self) -> str | None:
            """Get the bottom style."""
            return self._get('bottom')

        @bottom.setter
        def bottom(self, value: str) -> None:
            self._set('bottom', value)

        @bottom.deleter
        def bottom(self) -> None:
            self._delete('bottom')

        @property
        def left(self) -> str | None:
            """Get the left style."""
            return self._get('left')

        @left.setter
        def left(self, value: str) -> None:
            self._set('left', value)

        @left.deleter
        def left(self) -> None:
            self._delete('left')

        def __str__(self) -> str | None:
            name = self._get('name')
          
            if self._get('all'):
                return f'{name}:{self._get("all")}'

            result = []
            for k, v in self:
                if k == 'name':
                    continue
                if not v:
                    continue
                result.append(f'{name}-{k}:{v};')

            return ' '.join(result)

    def __init__(
            self, 
            name: str | None = None, 
            styles: dict[str, str] | None = None) -> None:
        """
        Initialize a style with a dictionary of style attributes.
        
        Args:
            styles: Dictionary of style attributes to initialize with
        """
        super().__init__(load=styles or {}, name=name)

    @property
    def asHTMLAttribute(self) -> str | None:
        """
        Get the HTML representation of the style.
        """
        return self._get_as_html_attribute()
        
    @property
    def asCSSSelector(self) -> str | None:
        """
        Get the CSS representation of the style.
        """
        return self._get_as_css_selector()

    @property
    def type(self) -> str | None:
        """
        Get the type of the style (e.g., 'class', 'id', 'element').
        
        Returns:
            Type of the style or None if not found
        """
        name = self._get('name')
        if not name:
            return None
        if name.startswith('.'):
            return 'class'
        if name.startswith('#'):
            return 'id'
        if name in TAG_NAMES:
            return 'element'
        return None

    @property
    def fontSize(self) -> str | None:
        """Get the font-size style."""
        return self._get('font-size')

    @fontSize.setter
    def fontSize(self, value: str) -> None:
        self._set('font-size', value)

    @fontSize.deleter
    def fontSize(self) -> None:
        self._delete('font-size')

    @property
    def fontFamily(self) -> str | None:
        """Get the font-family style."""
        return self._get('font-family')

    @fontFamily.setter
    def fontFamily(self, value: str) -> None:
        self._set('font-family', value)#f'\'{value}\'')

    @fontFamily.deleter
    def fontFamily(self) -> None:
        self._delete('font-family')

    @property
    def color(self) -> str | None:
        """Get the color style."""
        return self._get('color')

    @color.setter
    def color(self, value: str) -> None:
        self._set('color', value)

    @color.deleter
    def color(self) -> None:
        self._delete('color')

    @property
    def backgroundColor(self) -> str | None:
        """Get the background-color style."""
        return self._get('background-color')

    @backgroundColor.setter
    def backgroundColor(self, value: str) -> None:
        self._set('background-color', value)

    @backgroundColor.deleter
    def backgroundColor(self) -> None:
        self._delete('background-color')

    @property
    def fontWeight(self) -> str | None:
        """Get the font-weight style."""
        return self._get('font-weight')

    @fontWeight.setter
    def fontWeight(self, value: str) -> None:
        self._set('font-weight', value)

    @fontWeight.deleter
    def fontWeight(self) -> None:
        self._delete('font-weight')

    @property
    def fontStyle(self) -> str | None:
        """Get the font-style style."""
        return self._get('font-style')

    @fontStyle.setter
    def fontStyle(self, value: str) -> None:
        self._set('font-style', value)

    @fontStyle.deleter
    def fontStyle(self) -> None:
        self._delete('font-style')

    @property
    def textAlign(self) -> str | None:
        """Get the text-align style."""
        return self._get('text-align')

    @textAlign.setter
    def textAlign(self, value: str) -> None:
        self._set('text-align', value)

    @textAlign.deleter
    def textAlign(self) -> None:
        self._delete('text-align')

    @property
    def lineHeight(self) -> str | None:
        """Get the line-height style."""
        return self._get('line-height')

    @lineHeight.setter
    def lineHeight(self, value: str) -> None:
        self._set('line-height', value)

    @lineHeight.deleter
    def lineHeight(self) -> None:
        self._delete('line-height')

    @property
    def letterSpacing(self) -> str | None:
        """Get the letter-spacing style."""
        return self._get('letter-spacing')

    @letterSpacing.setter
    def letterSpacing(self, value: str) -> None:
        self._set('letter-spacing', value)

    @letterSpacing.deleter
    def letterSpacing(self) -> None:
        self._delete('letter-spacing')

    @property
    def textDecoration(self) -> str | None:
        """Get the text-decoration style."""
        return self._get('text-decoration')

    @textDecoration.setter
    def textDecoration(self, value: str) -> None:
        self._set('text-decoration', value)

    @textDecoration.deleter
    def textDecoration(self) -> None:
        self._delete('text-decoration')

    @property
    def border(self) -> str | None:
        """Get the border style."""
        return self._get_sides_object('border')

    @border.setter
    def border(self, value: str) -> None:
        self._get_sides_object('border').all = value

    @border.deleter
    def border(self) -> None:
        self._delete('border')

    @property
    def borderRadius(self) -> str | None:
        """Get the border-radius style."""
        return self._get('border-radius')

    @borderRadius.setter
    def borderRadius(self, value: str) -> None:
        self._set('border-radius', value)

    @borderRadius.deleter
    def borderRadius(self) -> None:
        self._delete('border-radius')

    @property
    def width(self) -> str | None:
        """Get the width style."""
        return self._get('width')

    @width.setter
    def width(self, value: str) -> None:
        self._set('width', value)

    @width.deleter
    def width(self) -> None:
        self._delete('width')

    @property
    def height(self) -> str | None:
        """Get the height style."""
        return self._get('height')

    @height.setter
    def height(self, value: str) -> None:
        self._set('height', value)

    @height.deleter
    def height(self) -> None:
        self._delete('height')

    @property
    def margin(self) -> str | None:
        """Get the margin style."""
        return self._get_sides_object('margin')

    @margin.setter
    def margin(self, value: str) -> None:
        self._get_sides_object('margin').all = value

    @margin.deleter
    def margin(self) -> None:
        self._delete('margin')

    @property
    def padding(self) -> str | None:
        """Get the padding style."""
        return self._get_sides_object('padding')

    @padding.setter
    def padding(self, value: str) -> None:
        self._get_sides_object('padding').all = value

    @padding.deleter
    def padding(self) -> None:
        self._delete('padding')

    @property
    def display(self) -> str | None:
        """Get the display style."""
        return self._get('display')

    @display.setter
    def display(self, value: str) -> None:
        self._set('display', value)

    @display.deleter
    def display(self) -> None:
        self._delete('display')

    @property
    def position(self) -> str | None:
        """Get the position style."""
        return self._get('position')

    @position.setter
    def position(self, value: str) -> None:
        self._set('position', value)

    @position.deleter
    def position(self) -> None:
        self._delete('position')

    @property
    def overflow(self) -> str | None:
        """Get the overflow style."""
        return self._get('overflow')

    @overflow.setter
    def overflow(self, value: str) -> None:
        self._set('overflow', value)

    @overflow.deleter
    def overflow(self) -> None:
        self._delete('overflow')

    @property
    def opacity(self) -> str | None:
        """Get the opacity style."""
        return self._get('opacity')

    @opacity.setter
    def opacity(self, value: str) -> None:
        self._set('opacity', value)

    @opacity.deleter
    def opacity(self) -> None:
        self._delete('opacity')

    def _get_sides_object(self, attr: str) -> 'Style.Sides':
        """
        Get the sides (top, right, bottom, left) of a CSS property.
        If the property is not set, it initializes it with default sides.
        """
        exists = self._get(attr)
        if not exists or not isinstance(exists, Style.Sides):
            _dict = {}
            _toDelete = []
            for k, v in self:
        
                if k.startswith(f'{attr}'):
                    _k = k.split('-')
                    if len(_k) == 1:
                        side = 'all'
                    else:
                        side = _k[1]
                    _dict[side] = v
                    _toDelete.append(k)

            for k in _toDelete:
                del self._values[k]
            self._set(attr, Style.Sides(_dict, attr))
       
        return self._get(attr)
    
    def _get_as_html_attribute(self) -> str:
        """
        Get the CSS styles as an HTML attribute string.
        
        """
        _styles = []

        for k, v in  self:
            if not v or k == 'name':
                continue
     
            if isinstance(v, Style.Sides):
                _styles.append(str(v))
                continue
            _styles.append(f"{k}:{v};")
     
        return " ".join(_styles).strip()

    def _get_as_css_selector(self) -> str:
        """
        Get the CSS styles as a CSS selector string.
        """
        css = f"{self._get('name')} {{\n"

        for k, v in  self:
            if not v or k == 'name':
                continue

            if isinstance(v, Style.Sides):
                css += '\t' + '\n\t'.join(str(v).split())
                continue
           
            css += f"\t{k}:{v};\n"
        return f'{css}}}'

    def __str__(self) -> str:
        """
        Get a string representation of the CSS.
        """
        return f'{self._get("name")}="{self._get_as_html_attribute()}"'


class CSS(ObjectWithDefaults):

    def __init__(self, styles: dict[str, Style] | None = None) -> None:
        """
        Initialize styles with a list of style strings.
        
        Args:
            styles: List of style strings to initialize with
        """
        super().__init__(styles or {})

    @property
    def classes(self) -> list[str]:
        """
        Get the list of class names for the element.
        
        Returns:
            List of class names
        """
        return [style for style in self._values.values() if style.type == 'class']

    @property
    def ids(self) -> list[str]:
        """
        Get the list of IDs for the element.

        Returns:
            List of IDs
        """
        return [style for style in self._values.values() if style.type == 'id']

    @property
    def elements(self) -> list[str]:
        """
        Get the list of tags for the element.

        Returns:
            List of tags
        """
        return [style for style in self._values.values() if style.type == 'element']
    
    @property
    def isEmpty(self) -> bool:
        """
        Check if the CSS has no styles.
        """
        return len(self._values) == 0

    def append(self, style: Style) -> None:
        """
        Append a new style to the CSS.

        Args:
            style: The style to append
        """
        self._set(style.name, style)

    def get(self, name: str) -> Style | None:
        """
        Get a style by its name.

        Args:
            name: The name of the style to retrieve

        Returns:
            The style if found, None otherwise
        """
        return self._values.get(name)

    def __iter__(self) -> list[Style]:
        """
        Get an iterator over the styles.
        """
        return iter(self._values.values())

    def __str__(self) -> str:
        """
        Get a string representation of the CSS.
        """
        return '\n'.join(str(style) for style in self._values)


def parse_css_to_styles(text: str) -> CSS:
    """
    Parse a list of style strings into a dictionary.
    
    Args:
        styles: List of style strings
    
    Returns:
        Dictionary mapping style attributes to their values
    """
    css = CSS()
    if not text:
        return css

    allSelectorsAttributes = text.split('}')
    for selectorsAttributes in allSelectorsAttributes:
        selectors_attributes = []
        for _sA in selectorsAttributes.split('{', 1):
            _sA == _sA.strip()
            if _sA:
                selectors_attributes.append(_sA)
        if len(selectors_attributes) < 2:
            continue

        selectors = selectors_attributes[0].split(',')
        attrs = selectors_attributes[1].split(';')
        attrsKeyValues = [attr.split(':') for attr in attrs if ':' in attr]
        attrsDict = {k.strip(): v.strip() for k, v in attrsKeyValues}

        for selector in selectors:
            selector = selector.strip()
            if selector[:2] == '/*':
                continue
            css.append(Style(selector, attrsDict.copy()))
    return css