from django.db import models

# Create your models here.

class Guest(models.Model):
    """Guest information."""
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    id_card = models.CharField(max_length=50, blank=True, null=True)
    nif_number = models.CharField(max_length=50, blank=True, null=True)
    nationality = models.CharField(max_length=100, blank=True, null=True)
    preferred_language = models.CharField(max_length=10, default='EN')

    class Meta:
        db_table = 'guests'
        verbose_name = 'Guest'
        verbose_name_plural = 'Guests'

    def __str__(self):
        if self.first_name:
            return f"{self.first_name} {self.last_name}"
        return self.last_name