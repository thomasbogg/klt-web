"""Prompt-path trigger for the two event-triggered email types (deposit/balance payment received).
Covers today's actual live path - staff manually flipping Payment.status/BalancePayment.status via
the Django ORM on the staff Booking page (staff/views.py::StaffBookingDetailView, `payment.save(
update_fields=['status'])`) - so a confirmation goes out promptly rather than waiting for the next
cron tick. Does NOT cover klt-hooks writing status='paid' directly via raw SQL against the shared
Postgres (a separate Flask process - Django signals can never observe a write that doesn't go
through the ORM) - that route is currently suspended, but the reconciliation sweep in
communications/services/scheduling.py::sync_event_triggered_emails(), run every time
send_due_scheduled_emails fires, is what covers it once re-enabled. Belt and braces, not
redundant - each half covers a path the other can't see."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


def _create_and_send(booking_id, slug):
    from communications.models import EmailTemplate, ScheduledEmail
    from communications.services.sending import send_scheduled_email

    template = EmailTemplate.objects.filter(slug=slug, active=True).first()
    if template is None:
        return
    scheduled_email, _ = ScheduledEmail.objects.get_or_create(
        booking_id=booking_id, template=template, defaults={'scheduled_for': timezone.now().date()},
    )
    if scheduled_email.status == ScheduledEmail.Status.PENDING:
        send_scheduled_email(scheduled_email, actor=None)


@receiver(post_save, sender='bookings.Payment')
def payment_saved(sender, instance, **kwargs):
    if instance.status == 'paid':
        _create_and_send(instance.booking_id, 'deposit_payment_received')


@receiver(post_save, sender='bookings.BalancePayment')
def balance_payment_saved(sender, instance, **kwargs):
    if instance.status == 'paid':
        _create_and_send(instance.booking_id, 'balance_payment_received')
