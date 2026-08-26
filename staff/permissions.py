from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied


def staff_page_required(field_name):
    """Gates a staff view behind one of StaffRole's page-access booleans (see
    staff/utils.py::STAFF_PAGE_PERMISSION_FIELDS). Composes the existing staff_member_required
    (is_active and is_staff, redirects to admin:login) rather than re-deriving that check, so an
    unauthenticated/non-staff request still behaves exactly as it always has. Superusers always
    pass regardless of role. A staff user with no StaffProfile, no role, or a role missing this
    specific field gets PermissionDenied (Django's stock 403) - expected for a fresh account
    until a superuser assigns it a role, not an error condition to work around."""
    def decorator(view_func):
        @wraps(view_func)
        def check_role(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            profile = getattr(request.user, 'staff_profile', None)
            if profile and profile.role and getattr(profile.role, field_name, False):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied
        return staff_member_required(check_role)
    return decorator


def superuser_required(view_func):
    """Gates a view to superusers only - no StaffRole field involved at all, unlike
    staff_page_required. For pages/actions that are deliberately not exposed to any role, however
    permissioned (currently just the cleaning calendar's drag-to-reschedule view and its JSON
    endpoints - see staff/views.py::StaffCleaningCalendarView)."""
    @wraps(view_func)
    def check_superuser(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return staff_member_required(check_superuser)
