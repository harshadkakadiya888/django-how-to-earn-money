# Generated manually for ContactMessage

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0008_newsletterreview"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContactMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("full_name", models.CharField(max_length=200)),
                ("email_address", models.EmailField(max_length=254)),
                ("subject", models.CharField(max_length=500)),
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
