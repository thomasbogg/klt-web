import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

URL_RE = re.compile(r'(https?://[^\s<>"]+)')


@register.filter
def linkify(value):
    """Turns any http(s):// URL inside plain text into a clickable link that opens in a new tab -
    Django's own built-in `urlize` filter has no way to add target="_blank", and Location's own
    free-text fields (directions, nearest_corner_shop, etc.) sometimes have a raw maps link pasted
    straight into the text. Escapes first so nothing else in the text can inject markup - the
    regex only ever matches inside that already-escaped string, so the href it builds is safe to
    mark_safe as a whole."""
    if not value:
        return value
    escaped = escape(value)
    linked = URL_RE.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    return mark_safe(linked)
