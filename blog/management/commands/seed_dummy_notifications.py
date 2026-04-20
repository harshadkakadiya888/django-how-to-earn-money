import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from blog.models import Category, Notification, Post
from blog.notifications import upsert_post_activity_notification

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Ensure the dashboard admin user exists and create sample notifications "
        "(likes/comments on seed posts authored with that username)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default="darshilthummar")
        parser.add_argument("--email", default="darshilthummar@gmail.com")
        parser.add_argument("--password", default="darshil01")

    def handle(self, *args, **options):
        username = options["username"]
        email = options["email"]
        password = options["password"]

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email},
        )
        if not created and user.email != email:
            user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"User ready: {username} ({email})"))

        category, _ = Category.objects.get_or_create(
            name="Dashboard seed",
            defaults={},
        )

        seed_defs = [
            ("dashboard-seed-post-a", "Seed post A (notifications demo)"),
            ("dashboard-seed-post-b", "Seed post B (notifications demo)"),
        ]

        posts = []
        for slug, title in seed_defs:
            post, p_created = Post.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "excerpt": "Auto-created for admin notification samples.",
                    "content": "<p>Sample content.</p>",
                    "author": username,
                    "read_time": "1 min",
                    "category": category,
                    "tags": json.dumps([]),
                    "article_summary": "",
                    "faqs_json": json.dumps([]),
                    "status": Post.STATUS_PUBLISHED,
                },
            )
            if post.author.strip().lower() != username.lower():
                post.author = username
                post.save(update_fields=["author"])
            posts.append(post)
            self.stdout.write(f"  Post: {post.slug} ({'created' if p_created else 'exists'})")

        upsert_post_activity_notification(
            recipient=user,
            kind=Notification.KIND_LIKE,
            post=posts[0],
            message="Demo: Someone liked your post.",
        )
        upsert_post_activity_notification(
            recipient=user,
            kind=Notification.KIND_COMMENT,
            post=posts[0],
            message="Demo: New comment on your post — “Great write-up, thanks for sharing!”",
        )
        upsert_post_activity_notification(
            recipient=user,
            kind=Notification.KIND_LIKE,
            post=posts[1],
            message="Demo: Your post received another like.",
        )

        unread = Notification.objects.filter(user=user, is_read=False).count()
        self.stdout.write(self.style.SUCCESS(f"Notifications in place for {username} (unread-ish rows: {unread})."))
