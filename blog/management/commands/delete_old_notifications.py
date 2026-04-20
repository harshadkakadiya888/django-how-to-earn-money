from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from blog.models import Notification


class Command(BaseCommand):
    help = "Delete notifications whose created_at is older than 30 days."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        deleted, breakdown = Notification.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted} row(s) (breakdown: {breakdown}). Cutoff: {cutoff.isoformat()}."
            )
        )
