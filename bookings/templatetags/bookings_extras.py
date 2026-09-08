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
    is safe to mark_safe as a whole.

    Also converts newlines to <br> (2026-09-08, per Thomas - real bug: LocationRules.pool_rules/
    condominium_rules and every other multi-paragraph field this filter touches are plain
    TextFields staff type blank-line-separated paragraphs into, but every call site here renders
    the result inside an ordinary block element with no white-space:pre-line - HTML collapses that
    whitespace, so multiple paragraphs ran together as one wall of text on the guest side even
    though they were saved correctly). Same behaviour as Django's own linebreaksbr filter, just
    folded into this one so every consumer gets it automatically rather than needing both filters
    chained (linkify already re-escapes and marks safe, which linebreaksbr piped in afterward
    would either double-escape or have to skip)."""
    if not value:
        return value
    escaped = escape(value)
    linked = URL_RE.sub(_linkify_match, escaped)
    linked = linked.replace('\r\n', '\n').replace('\n', '<br>')
    return mark_safe(linked)


SPLIT_LINES_RE = re.compile(r'\n+')


@register.filter
def split_lines(value):
    """Splits a plain-text field into a list on any run of one or more newlines, for a template to
    render one <li>/<p> per line instead of one for the whole field (2026-09-08, per Thomas -
    LocationRules.pool_rules/condominium_rules read as a genuine list of distinct rules, one per
    line, and linkify's own <br> fix above, while correct, still left them all crammed under one
    bullet point). Splitting on \\n+ rather than a literal blank line ('\\n\\n') matters here: the
    real staff-entered data (confirmed via a live screenshot) is single-newline-separated, not
    blank-line-separated, even though it reads with visual spacing in a <textarea> - \\n+ treats
    either shape the same way, one item per line regardless of how many consecutive newlines
    separate them. Mirrors the same split() BookingManageLastDaysView already does in Python for
    after_checkout_paragraphs (see that view's own comment for why {% linebreaks %} isn't used
    instead - it re-escapes and mangles linkify's own <a> tags) - a template filter version of the
    same idea, for call sites that don't already have a view-level context variable to pass a
    pre-split list through."""
    if not value:
        return []
    return [line for line in SPLIT_LINES_RE.split(value.replace('\r\n', '\n')) if line.strip()]
