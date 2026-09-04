from django.template import Context, Template
from django.utils import timezone

import env_settings
from communications.models import ScheduledEmail
from communications.registry import EMAIL_TYPES
from libraries.utils import logerror


def send_scheduled_email(scheduled_email, actor=None):
    """The single function that ever actually sends a ScheduledEmail - both the manual "Send now"
    view (staff/views.py::StaffBookingEmailSendView) and the send_due_scheduled_emails cron command
    call this and only this, so the two can never disagree about whether a row is still actually
    due (see ScheduledEmail's own docstring for why this matters - an earlier draft only checked
    row status in the manual view, which would have let a staff member jump-fire a now-stale
    email). actor=None means an automated/cron send; actor=<User> means a staff member clicked
    Send now - the caller is responsible for having already checked staff_email_action_required's
    permission gate before calling with a real actor."""
    booking = scheduled_email.booking
    template = scheduled_email.template
    definition = EMAIL_TYPES.get(template.slug)

    if definition is None or not definition.eligible(booking):
        _mark(scheduled_email, ScheduledEmail.Status.SKIPPED,
              error_message=f"'{template.name}' is no longer needed for this booking")
        return scheduled_email

    recipient = definition.recipient_email(booking)
    if not recipient:
        _mark(scheduled_email, ScheduledEmail.Status.SKIPPED, error_message="No recipient email on file")
        return scheduled_email

    context = definition.context(booking)
    subject = Template(template.subject).render(Context(context))
    body = Template(template.body).render(Context(context))
    greeting_name = context.get('owner_name') if template.audience == 'owner' else context.get('guest_first_name', '')
    from_email = actor.email if actor is not None else env_settings.COMMS_AUTOMATED_SENDER_EMAIL
    from_display_name = actor.get_full_name() or actor.email if actor is not None else 'Algarve Beach Apartments'

    try:
        if env_settings.COMMS_DRY_RUN:
            print(
                f"[DRY RUN] communications: '{template.slug}' for {booking.reference} - "
                f"from {from_email} to {recipient}\nSubject: {subject}\n{body}\n"
            )
        else:
            _send_via_gmail(from_email, from_display_name, greeting_name, recipient, subject, body)
    except Exception as error:
        _mark(scheduled_email, ScheduledEmail.Status.FAILED, error_message=str(error),
              rendered_subject=subject, rendered_body=body)
        logerror(f"communications: could not send '{template.slug}' for {booking.reference}: {error}")
        return scheduled_email

    rendered_body = f"[DRY RUN]\n{body}" if env_settings.COMMS_DRY_RUN else body
    scheduled_email.status = ScheduledEmail.Status.SENT
    scheduled_email.sent_at = timezone.now()
    scheduled_email.sent_by = actor
    scheduled_email.rendered_subject = subject
    scheduled_email.rendered_body = rendered_body
    scheduled_email.error_message = ''
    scheduled_email.save(update_fields=['status', 'sent_at', 'sent_by', 'rendered_subject', 'rendered_body', 'error_message'])

    if definition.on_sent is not None:
        definition.on_sent(booking)

    return scheduled_email


def _mark(scheduled_email, status, error_message='', rendered_subject='', rendered_body=''):
    scheduled_email.status = status
    scheduled_email.error_message = error_message
    if rendered_subject:
        scheduled_email.rendered_subject = rendered_subject
    if rendered_body:
        scheduled_email.rendered_body = rendered_body
    scheduled_email.save(update_fields=['status', 'error_message', 'rendered_subject', 'rendered_body'])


def _send_via_gmail(from_email, from_display_name, greeting_name, to_email, subject, body):
    """Real send via the vendored libraries/google Gmail wrapper - the same library
    klt-management-software already uses for its own real sends, previously unwired in klt-web
    (not in INSTALLED_APPS, nothing imported it). GoogleAPIService.connect() picks OAuth
    "installed app" vs service-account domain-wide-delegation based on env_settings.LOCAL, exactly
    the split that lets `from_email` legitimately vary per send once Workspace delegation is
    confirmed (see communications app's project-memory open question)."""
    from libraries.google.connect import GoogleAPIService
    from libraries.google.mail.message import GoogleMailMessage

    service = GoogleAPIService(
        username=from_email, api='gmail', version='v1',
        scopes=['https://www.googleapis.com/auth/gmail.send'],
        credentials=env_settings.GOOGLE_API_CREDENTIALS, LOCAL=env_settings.LOCAL,
    ).connect()

    message = GoogleMailMessage(service)
    message.sender = from_email
    message.to = to_email
    message.subject = subject
    message.greeting.name = greeting_name
    message.signature.name = from_display_name
    message.body.paragraph(body)
    message.send()
