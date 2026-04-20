import json

from django.core.management.base import BaseCommand

from blog.models import Category, Post


class Command(BaseCommand):
    help = "Seed dummy categories and posts for testing."

    def handle(self, *args, **options):
        categories = [
            "News",
            "Technology",
            "Fashion",
            "Health",
            "Travel",
        ]

        created_categories = []
        for name in categories:
            category, _ = Category.objects.get_or_create(name=name)
            created_categories.append(category)

        self.stdout.write(self.style.SUCCESS(f"Loaded {len(created_categories)} categories."))

        if not Post.objects.exists():
            dummy_posts = [
                {
                    "title": "Latest industry news and updates",
                    "slug": "latest-industry-news-and-updates",
                    "excerpt": "Stay informed with the latest headlines from our news category.",
                    "content": "This post contains sample content for the news category.",
                    "author": "Admin",
                    "read_time": "4 min",
                    "tags": ["news", "updates", "industry"],
                },
                {
                    "title": "How technology is changing today",
                    "slug": "how-technology-is-changing-today",
                    "excerpt": "A short look at how the tech world continues to evolve.",
                    "content": "Example content on technology and innovation.",
                    "author": "Tech Writer",
                    "read_time": "5 min",
                    "tags": ["technology", "innovation"],
                },
                {
                    "title": "Fashion trends for the season",
                    "slug": "fashion-trends-for-the-season",
                    "excerpt": "Discover the latest fashion trends for this season.",
                    "content": "Sample fashion content and style guidance.",
                    "author": "Style Editor",
                    "read_time": "3 min",
                    "tags": ["fashion", "style"],
                },
            ]

            for index, item in enumerate(dummy_posts):
                Post.objects.create(
                    title=item["title"],
                    slug=item["slug"],
                    excerpt=item["excerpt"],
                    content=item["content"],
                    author=item["author"],
                    read_time=item["read_time"],
                    category=created_categories[index % len(created_categories)],
                    tags=json.dumps(item["tags"]),
                    article_summary=item["excerpt"],
                    faqs_json=json.dumps([]),
                )

            self.stdout.write(self.style.SUCCESS("Created 3 dummy posts."))
        else:
            self.stdout.write(self.style.WARNING("Posts already exist. Skipping dummy post creation."))
