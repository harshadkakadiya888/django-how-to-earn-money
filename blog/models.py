from django.conf import settings
from django.db import models

from .utils import ensure_unique_slug


class Category(models.Model):
    name = models.CharField(max_length=200)
    slug = models.CharField(max_length=220, unique=True)
    image = models.ImageField(upload_to="categories/", blank=True, null=True)

    class Meta:
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if (not self.slug) and self.name:
            self.slug = ensure_unique_slug(Category, self.name, self)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Post(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
    )

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=400, unique=True)
    excerpt = models.TextField(blank=True)
    content = models.TextField()
    featured_image = models.ImageField(upload_to="posts/", blank=True, null=True)
    author = models.CharField(max_length=200, blank=True)
    read_time = models.CharField(max_length=50, blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="posts",
    )
    tags = models.TextField(blank=True)  # JSON array string
    article_summary = models.TextField(blank=True)
    faqs_json = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PUBLISHED,
        db_index=True,
    )
    views_count = models.PositiveIntegerField(default=0)
    liked_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="blog_posts_liked",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if (not self.slug) and self.title:
            self.slug = ensure_unique_slug(Post, self.title, self)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def total_likes_count(self) -> int:
        """Anonymous (PostLike) + authenticated (M2M) likes."""
        return self.likes.count() + self.liked_users.count()


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name}: {self.comment[:30]}"


class PostLike(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    client_id = models.CharField(max_length=200)
    liker_name = models.CharField(max_length=150, blank=True, default="")
    liker_email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("post", "client_id")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.post_id}:{self.client_id}"


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email


class ContactMessage(models.Model):
    full_name = models.CharField(max_length=200)
    email_address = models.EmailField()
    subject = models.CharField(max_length=500)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email_address}: {self.subject[:40]}"


class NewsletterReview(models.Model):
    name = models.CharField(max_length=200, blank=True, default="")
    email = models.EmailField()
    rating = models.PositiveSmallIntegerField(default=5)
    review = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email}: {self.review[:40]}"


class Notification(models.Model):
    KIND_LIKE = "like"
    KIND_COMMENT = "comment"
    KIND_CHOICES = (
        (KIND_LIKE, "Like"),
        (KIND_COMMENT, "Comment"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    message = models.TextField()
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "kind", "post"),
                name="blog_notification_user_kind_post_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="blog_notif_user_created"),
        ]

    def __str__(self):
        return f"{self.kind} for {self.user_id}: {self.message[:40]}"
