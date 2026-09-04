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


def can_send_emails(user):
    """Whether `user` may trigger a guest/owner-facing email send (communications app) - shared by
    staff_email_action_required below (the hard gate on the actual send view) and the Next step(s)
    panel (staff/views.py::StaffBookingDetailView, to decide whether to even show a "Send now"
    button), so the two can never disagree about who's allowed to send."""
    if not user.email.lower().endswith('@algarvebeachapartments.com'):
        return False
    profile = getattr(user, 'staff_profile', None)
    return bool(profile and profile.role and profile.role.can_send_emails)


def staff_email_action_required(view_func):
    """Gates any guest/owner-facing email dispatch action (communications app) - deliberately NOT
    staff_page_required, and deliberately does not give superusers a bypass the way every other
    decorator here does. Sending real mail to a guest/owner is a higher-stakes action than viewing
    an internal page, and per Thomas (2026-09-04) the primary gate is identity, not role: only a
    login on the @algarvebeachapartments.com domain may trigger a send at all, and even then only
    with StaffRole.can_send_emails also set. Composes staff_member_required same as
    staff_page_required, so an unauthenticated/non-staff request behaves exactly as it always has."""
    @wraps(view_func)
    def check(request, *args, **kwargs):
        if not can_send_emails(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return staff_member_required(check)


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
