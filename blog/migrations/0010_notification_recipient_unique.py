# Generated manually for per-user notifications

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def clear_notifications(apps, schema_editor):
    Notification = apps.get_model("blog", "Notification")
    Notification.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0009_contactmessage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(clear_notifications, migrations.RunPython.noop),
        migrations.AddField(
            model_name="notification",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[("like", "Like"), ("comment", "Comment")],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(
                fields=("user", "kind", "post"),
                name="blog_notification_user_kind_post_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(
                fields=["user", "-created_at"],
                name="blog_notif_user_created",
            ),
        ),
    ]
