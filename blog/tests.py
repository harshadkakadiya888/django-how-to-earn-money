from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from rest_framework.test import APIClient

from blog.models import Category, Post


class BlogApiTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_create_category_json(self):
        response = self.client.post(
            "/api/categories/",
            {"name": "Dummy Category"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("category", response.json())
        self.assertEqual(response.json()["category"]["name"], "Dummy Category")

    def test_seed_blog_data_command(self):
        call_command("seed_blog_data")

        self.assertTrue(Category.objects.exists())
        self.assertTrue(Post.objects.exists())

    def test_anonymous_post_list_excludes_drafts(self):
        cat = Category.objects.create(name="DraftFilterCat", slug="draft-filter-cat")
        Post.objects.create(
            title="Draft Post",
            slug="draft-post-x",
            content="c",
            category=cat,
            status=Post.STATUS_DRAFT,
        )
        Post.objects.create(
            title="Published Post",
            slug="published-post-x",
            content="c",
            category=cat,
            status=Post.STATUS_PUBLISHED,
        )
        res = self.client.get("/api/posts/")
        self.assertEqual(res.status_code, 200)
        titles = {p["title"] for p in res.json()["posts"]}
        self.assertIn("Published Post", titles)
        self.assertNotIn("Draft Post", titles)

    def test_staff_post_list_includes_drafts(self):
        cat = Category.objects.create(name="StaffCat", slug="staff-cat")
        Post.objects.create(
            title="Staff Draft",
            slug="staff-draft-x",
            content="c",
            category=cat,
            status=Post.STATUS_DRAFT,
        )
        user = get_user_model().objects.create_user(
            username="staffer",
            password="pass12345",
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get("/api/posts/")
        self.assertEqual(res.status_code, 200)
        titles = {p["title"] for p in res.json()["posts"]}
        self.assertIn("Staff Draft", titles)
