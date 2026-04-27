# Generated manually: sync DB with models (updated_at, PostLike.email)

import django.utils.timezone
from django.db import migrations, models


def copy_created_to_updated(apps, schema_editor):
    Notification = apps.get_model("blog", "Notification")
    for row in Notification.objects.all().only("id", "created_at"):
        Notification.objects.filter(pk=row.pk).update(updated_at=row.created_at)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0002_notification_structured_data"),
    ]

    operations = [
        migrations.RenameField(
            model_name="postlike",
            old_name="liker_email",
            new_name="email",
        ),
        migrations.AddField(
            model_name="notification",
            name="updated_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(copy_created_to_updated, noop_reverse),
        migrations.AlterField(
            model_name="notification",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
