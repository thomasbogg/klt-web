import env_settings


def send_accountant_invite_email(request, user):
    """Sends `user` the link to set their own password for the Accountants Suite - mirrors
    owners/utils.py::send_owner_invite_email exactly (same uidb64/token machinery, same
    PasswordResetConfirmView-based landing view, here accountants.views.
    AccountantAcceptInviteView). Triggered by staff.views.StaffSettingsView._invite_accountant,
    which creates the account with set_unusable_password() rather than a staff-chosen one;
    _resend_accountant_invite calls this again for an account that hasn't set one yet.

    English-only, unlike send_owner_invite_email - Accountant has no preferred_language field and
    none is being added for this scope.

    Returns True/False for whether the send succeeded, so callers can flash an accurate message -
    does not raise, since a failed invite send shouldn't block the account from having been
    created (staff can always retry via "Resend invite")."""
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from communications.services.sending import send_plain_email

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = request.build_absolute_uri(reverse('accountants:accept_invite', kwargs={'uidb64': uid, 'token': token}))

    accountant = getattr(user, 'accountant_profile', None)
    greeting_name = accountant.name if accountant is not None else user.username
    subject = "You're invited to the Algarve Beach Apartments Accountants Suite"
    body = (
        f"You've been invited to your own Accountants Suite account, where you can see booking "
        f"reports and payouts/memos for the owners and properties you look after. Set your "
        f"password to get started: {link} "
        f"This link will expire in a few days - if it does, ask staff to resend your invite."
    )
    try:
        send_plain_email(
            from_email=env_settings.COMMS_AUTOMATED_SENDER_EMAIL, from_display_name='Algarve Beach Apartments',
            greeting_name=greeting_name, to_email=user.email, subject=subject, body=body,
        )
    except Exception as error:
        from libraries.utils import logerror
        logerror(f"accountants: could not send invite email to {user.email}: {error}")
        return False
    return True
