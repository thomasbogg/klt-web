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
