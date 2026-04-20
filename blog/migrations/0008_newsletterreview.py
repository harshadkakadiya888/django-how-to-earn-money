# Generated manually for NewsletterReview

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0007_newslettersubscriber"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsletterReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, default="", max_length=200)),
                ("email", models.EmailField(max_length=254)),
                ("rating", models.PositiveSmallIntegerField(default=5)),
                ("review", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
