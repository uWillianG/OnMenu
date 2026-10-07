from django.db import models
from django.utils import timezone


class OperationStatus(models.Model):
    name = models.CharField(max_length=80, unique=True)
    next_run_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']
