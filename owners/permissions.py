from functools import wraps
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse


def owner_login_required(view_func):
    """Gates an Owner Suite page behind a real login (unlike the guest Manage Booking hub's
    reference+email model - see [[project_klt_web_manage_hub]] in memory) - the request user must
    be authenticated AND have a properties.models.Owner linked via Owner.user. No per-page
    permission fields yet (unlike staff.permissions.staff_page_required) - v1 has exactly two
    pages, both open to any linked owner."""
    @wraps(view_func)
    def check_owner(request, *args, **kwargs):
        owner = getattr(request.user, 'owner_profile', None) if request.user.is_authenticated else None
        if owner is None:
            login_url = reverse('owners:login')
            return redirect(f"{login_url}?{urlencode({'next': request.get_full_path()})}")
        return view_func(request, *args, **kwargs)
    return check_owner
