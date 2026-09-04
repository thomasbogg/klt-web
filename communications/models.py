from django.conf import settings
from django.db import models


class EmailTemplate(models.Model):
    """One staff-editable guest/owner transactional email - subject/body are plain Django template
    syntax, rendered against the flat, explicitly-declared context dict each slug provides (see
    communications/registry.py::EMAIL_TYPES) - never the raw Booking object, so staff can't reach
    into unrelated/sensitive fields via dot-lookup, and the declared placeholder list doubles as
    documentation. offset_days is the only per-template timing knob exposed to staff (signed -
    negative days before the anchor date, positive after) - matches how BookingSettings already
    treats timing as ordinary staff-tunable data. What anchor date, eligibility, and context a slug
    actually uses lives in code (communications/registry.py), not here, since several templates
    need live domain checks (already paid, already submitted, no email on file) that a DB row alone
    can't express.

    Editing lives on a staff-app page (StaffEmailTemplatesView), not Django admin - see
    feedback_klt_web_no_django_admin in project memory: Thomas doesn't use the Django admin for
    anything on this project."""

    class Audience(models.TextChoices):
        GUEST = 'guest', 'Guest'
        OWNER = 'owner', 'Owner'

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    audience = models.CharField(max_length=10, choices=Audience.choices)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    offset_days = models.IntegerField(
        help_text="Days relative to this email's anchor date (arrival, departure, hold expiry) - "
                  "negative sends before it, positive after. Ignored for event-triggered emails "
                  "(booking confirmations, payment-received receipts), which fire immediately.",
    )
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'communications_email_templates'
        verbose_name = 'Email Template'
        verbose_name_plural = 'Email Templates'
        ordering = ('name',)

    def __str__(self):
        return self.name


class ScheduledEmail(models.Model):
    """One scheduled/sent/skipped email for one booking - the row the staff Booking View's "Next
    step(s)" panel lists with a scheduled date and a "Send now" button (staff/views.py::
    StaffBookingDetailView), and the row send_scheduled_email() (communications/services/
    sending.py) actually acts on for both a manual send and the send_due_scheduled_emails cron
    command - there is deliberately only one code path that sends mail, so the two can never
    disagree about whether a row is still actually due (see EMAIL_TYPES[...].eligible(), rechecked
    live inside send_scheduled_email() itself, not just at row-creation time).

    unique_together with (booking, template) is the double-send guard, replacing the legacy
    klt-management-software system's per-booking boolean-flag table - same "record who/when, never
    re-derive" convention as finance.Memo.sent_at/sent_by and bookings.BalancePayment.reminder_sent/
    reminder_sent_at. No resend in v1: once 'sent', the row just displays who/when - mirrors
    StaffFinanceMemoSendView's hard stop on an already-sent Memo.

    scheduled_for is computed once at row-creation time (EMAIL_TYPES[slug].anchor(booking) +
    offset_days) and deliberately NOT recomputed if the booking's dates change later
    (BookingDateAdjustment) - same known, accepted gap BalancePayment's own collapse status already
    has today. sent_by is null for an automated (cron) send - its own absence IS the "this was
    automatic" signal, no separate flag."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        SKIPPED = 'skipped', 'Skipped'
        FAILED = 'failed', 'Failed'

    booking = models.ForeignKey('bookings.Booking', on_delete=models.CASCADE, related_name='scheduled_emails')
    template = models.ForeignKey(EmailTemplate, on_delete=models.PROTECT, related_name='scheduled_emails')
    scheduled_for = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField(blank=True, null=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    rendered_subject = models.TextField(blank=True)
    rendered_body = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'communications_scheduled_emails'
        verbose_name = 'Scheduled Email'
        verbose_name_plural = 'Scheduled Emails'
        ordering = ('scheduled_for',)
        constraints = [
            models.UniqueConstraint(fields=('booking', 'template'), name='unique_scheduled_email_per_booking_template'),
        ]

    def __str__(self):
        return f"{self.template.name} for {self.booking} ({self.get_status_display()})"
