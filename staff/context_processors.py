from staff.utils import STAFF_PAGE_PERMISSION_FIELDS


def staff_nav_permissions(request):
    """Which top-level staff pages the current user can see, for _nav.html's conditional links -
    UX only, NOT the real security boundary (staff.permissions.staff_page_required on each view
    is). klt_web/settings.py's TEMPLATES block is shared with the public guest-facing site, so
    this must stay cheap and side-effect-free for the overwhelming majority of requests (anonymous
    public visitors) - bail out before touching request.user.staff_profile (a DB-backed reverse
    OneToOne) unless the request is already an authenticated staff user."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return {}
    if request.user.is_superuser:
        return {'staff_nav_permissions': {field: True for field, _label in STAFF_PAGE_PERMISSION_FIELDS}}
    profile = getattr(request.user, 'staff_profile', None)
    role = profile.role if profile else None
    return {
        'staff_nav_permissions': {
            field: bool(role and getattr(role, field, False))
            for field, _label in STAFF_PAGE_PERMISSION_FIELDS
        }
    }
