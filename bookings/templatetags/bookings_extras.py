import re
from urllib.parse import urlparse

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

URL_RE = re.compile(r'(https?://[^\s<>"]+)')
TRAILING_PUNCTUATION = '.,;:!?)]}\'"'


def _split_trailing_punctuation(url):
    """Peels off punctuation a human would read as sentence structure rather than part of the
    address itself - e.g. the closing paren and full stop in "(https://example.com)." - without
    touching a ')' that closes a paren actually inside the URL (Wikipedia-style links)."""
    end = len(url)
    while end > 0 and url[end - 1] in TRAILING_PUNCTUATION:
        if url[end - 1] == ')' and url.count('(', 0, end - 1) > url.count(')', 0, end - 1):
            break
        end -= 1
    return url[:end], url[end:]


def _linkify_match(match):
    url, trailing = _split_trailing_punctuation(match.group(1))
    host = urlparse(url).netloc.removeprefix('www.')
    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'class="inline-text-link">{host}</a>{trailing}'
    )


@register.filter
def linkify(value):
    """Turns any http(s):// URL inside plain text into a clickable link that opens in a new tab,
    displayed as just its domain (e.g. "flytap.com") rather than the full address - Django's own
    built-in `urlize` filter has no way to add target="_blank" or shorten the link text, and
    Location's own free-text fields (directions, nearest_corner_shop, etc.) sometimes have a raw
    maps link pasted straight into the text. Escapes first so nothing else in the text can inject
    markup - the regex only ever matches inside that already-escaped string, so the href it builds
    is safe to mark_safe as a whole."""
    if not value:
        return value
    escaped = escape(value)
    linked = URL_RE.sub(_linkify_match, escaped)
    return mark_safe(linked)
