import env_settings


def send_owner_invite_email(request, user):
    """Sends `user` the link to set their own password for the Owner Suite - mirrors
    staff.utils.send_staff_invite_email (same Django password-reset token machinery: uidb64 +
    default_token_generator; same PasswordResetConfirmView-based landing view, here
    owners.views.OwnerAcceptInviteView). Triggered by staff.views.StaffSettingsView._invite_owner,
    which creates the account with set_unusable_password() rather than a staff-chosen one;
    _resend_owner_invite calls this again for an account that hasn't set one yet.

    Sent in Portuguese when Owner.preferred_language is 'pt' (added 2026-09-11, per Thomas -
    mirrors staff.utils.send_staff_invite_email's own language branching, same two-value choice).
    The rest of the Owner Suite is still English-only - like the staff side, this is currently the
    only place the setting is consumed.

    Returns True/False for whether the send succeeded, so callers can flash an accurate message -
    does not raise, since a failed invite send shouldn't block the account from having been
    created (staff can always retry via "Resend invite")."""
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from communications.services.sending import send_plain_email
    from properties.models import Owner

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = request.build_absolute_uri(reverse('owners:accept_invite', kwargs={'uidb64': uid, 'token': token}))

    owner = getattr(user, 'owner_profile', None)
    if owner is not None and owner.preferred_language == Owner.Language.PORTUGUESE:
        subject = "Foi convidado(a) para o Owner Suite da Algarve Beach Apartments"
        body = (
            f"Foi convidado(a) para a sua própria conta do Owner Suite, onde pode ver as suas "
            f"reservas, consultar a disponibilidade, ver relatórios e pagamentos, e manter os "
            f"seus dados de contacto atualizados. Defina a sua palavra-passe para começar: {link} "
            f"Este link expira dentro de alguns dias - se isso acontecer, peça à equipa para "
            f"reenviar o convite."
        )
    else:
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
        greeting_name = owner.name if owner is not None else user.username
        send_plain_email(
            from_email=env_settings.COMMS_AUTOMATED_SENDER_EMAIL, from_display_name='Algarve Beach Apartments',
            greeting_name=greeting_name, to_email=user.email, subject=subject, body=body,
        )
    except Exception as error:
        from libraries.utils import logerror
        logerror(f"owners: could not send invite email to {user.email}: {error}")
        return False
    return True
