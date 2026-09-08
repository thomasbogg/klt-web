import env_settings


def send_owner_invite_email(request, user):
    """Sends `user` the link to set their own password for the Owner Suite - mirrors
    staff.utils.send_staff_invite_email (same Django password-reset token machinery: uidb64 +
    default_token_generator; same PasswordResetConfirmView-based landing view, here
    owners.views.OwnerAcceptInviteView). Triggered by staff.views.StaffSettingsView._invite_owner,
    which creates the account with set_unusable_password() rather than a staff-chosen one;
    _resend_owner_invite calls this again for an account that hasn't set one yet.

    English only, unlike the staff invite - properties.models.Owner has no per-account language
    preference the way staff.models.StaffProfile does, and the rest of the Owner Suite is
    English-only too.

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
    link = request.build_absolute_uri(reverse('owners:accept_invite', kwargs={'uidb64': uid, 'token': token}))

    subject = "You're invited to the Algarve Beach Apartments Owner Suite"
    body = (
        f"You've been invited to your own Owner Suite account, where you can view your bookings, "
        f"check availability, see reports and payouts, and keep your contact details up to date. "
        f"Set your password to get started: {link} "
        f"This link will expire in a few days - if it does, ask staff to resend your invite."
    )
    try:
        # user.username is the login itself (their email address, per _invite_owner) rather than
        # a human name - greeting_name is the salutation, so use Owner.name instead (unlike
        # staff.utils.send_staff_invite_email, where the chosen username already reads as a name).
        greeting_name = user.owner_profile.name if getattr(user, 'owner_profile', None) else user.username
        send_plain_email(
            from_email=env_settings.COMMS_AUTOMATED_SENDER_EMAIL, from_display_name='Algarve Beach Apartments',
            greeting_name=greeting_name, to_email=user.email, subject=subject, body=body,
        )
    except Exception as error:
        from libraries.utils import logerror
        logerror(f"owners: could not send invite email to {user.email}: {error}")
        return False
    return True
