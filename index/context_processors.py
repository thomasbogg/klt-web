from django.core.cache import cache
from properties.models import ManagementCompany

SITE_HEAD_EMAIL_CACHE_KEY = 'site_head_email'
SITE_HEAD_EMAIL_CACHE_TIMEOUT = 300


def site_contact(request):
    """Algarve Beach Apartments' own head_email (per Thomas 2026-09-08), for the site-wide footer
    "get in touch" link - klt_web/settings.py's TEMPLATES block is shared with every page on the
    public guest-facing site, so this runs on every anonymous request and stays cheap via a short
    cache rather than a ManagementCompany query per page load (same one-row lookup as
    backdate_klt_cleaning_fees_2026.py's ManagementCompany.objects.get(name='KLT Property
    Services Lda') - that's the business's own company row, not a client's)."""
    email = cache.get(SITE_HEAD_EMAIL_CACHE_KEY)
    if email is None:
        company = ManagementCompany.objects.filter(name='KLT Property Services Lda').first()
        email = company.head_email if company else ''
        cache.set(SITE_HEAD_EMAIL_CACHE_KEY, email, SITE_HEAD_EMAIL_CACHE_TIMEOUT)
    return {'site_head_email': email}
