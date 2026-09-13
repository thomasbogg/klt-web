from functools import wraps
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse


def accountant_login_required(view_func):
    """Gates an Accountants Suite page behind a real login, mirroring
    owners/permissions.py::owner_login_required exactly - the request user must be authenticated
    AND have a properties.models.Accountant linked via Accountant.user. No per-page permission
    fields, same as the Owner Suite - v1 has exactly three pages, all open to any linked
    accountant."""
    @wraps(view_func)
    def check_accountant(request, *args, **kwargs):
        accountant = getattr(request.user, 'accountant_profile', None) if request.user.is_authenticated else None
        if accountant is None:
            login_url = reverse('accountants:login')
            return redirect(f"{login_url}?{urlencode({'next': request.get_full_path()})}")
        return view_func(request, *args, **kwargs)
    return check_accountant
