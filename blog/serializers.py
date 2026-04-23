import json

from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Comment, ContactMessage, NewsletterReview, NewsletterSubscriber, Notification, Post
from .utils import calculate_read_time, ensure_unique_slug


def _flatten_nested_json_tag_tokens(items: list) -> list:
    """Split mistaken double-encoded tags (e.g. one token '["[]"]' or nested JSON strings)."""
    out = []
    for t in items:
        t = str(t).strip()
        if not t or t in ("[]", "{}", '""', "null", "None"):
            continue
        if t.startswith("[") and t.endswith("]"):
            try:
                inner = json.loads(t)
                if isinstance(inner, list):
                    out.extend(_flatten_nested_json_tag_tokens([str(x) for x in inner]))
                    continue
                if isinstance(inner, str) and inner.strip():
                    out.extend(_flatten_nested_json_tag_tokens([inner.strip()]))
                    continue
            except json.JSONDecodeError:
                pass
        out.append(t)
    return out


def coerce_tags_to_list(raw) -> list:
    """Normalize tags from DB (JSON string, CSV, plain string, list) to a list of strings."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        base = [str(x).strip() for x in raw if str(x).strip()]
        return _flatten_nested_json_tag_tokens(base)
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            base = [str(x).strip() for x in parsed if str(x).strip()]
            return _flatten_nested_json_tag_tokens(base)
        if isinstance(parsed, str) and parsed.strip():
            return _flatten_nested_json_tag_tokens([parsed.strip()])
    except json.JSONDecodeError:
        pass
    if "," in s:
        base = [p.strip() for p in s.split(",") if p.strip()]
        return _flatten_nested_json_tag_tokens(base)
    return _flatten_nested_json_tag_tokens([s])


class CategorySerializer(serializers.ModelSerializer):
    _id = serializers.SerializerMethodField()
    image = serializers.ImageField(required=False, allow_null=True)
    slug = serializers.SlugField(required=False, allow_blank=True, max_length=220)

    class Meta:
        model = Category
        fields = ("id", "_id", "name", "slug", "image")
        read_only_fields = ("id", "_id")

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Category name is required.")
        return cleaned

    def validate_slug(self, value):
        if value in (None, ""):
            return value
        cleaned = value.strip().lower()
        qs = Category.objects.filter(slug=cleaned)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This slug is already in use.")
        return cleaned

    def create(self, validated_data):
        slug = validated_data.get("slug") or ""
        name = validated_data.get("name", "")
        if not slug:
            validated_data["slug"] = ensure_unique_slug(Category, slugify(name) or name, None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        slug = validated_data.get("slug", serializers.empty)
        if slug is serializers.empty:
            return super().update(instance, validated_data)
        if not slug:
            validated_data["slug"] = ensure_unique_slug(Category, slugify(instance.name) or instance.name, instance)
        return super().update(instance, validated_data)

    def get__id(self, obj):
        return str(obj.id)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.image:
            try:
                data["image"] = instance.image.url
            except Exception:
                data["image"] = None
        else:
            data["image"] = None
        return data


class PostSerializer(serializers.ModelSerializer):
    """Multipart write: use `image` for file upload (maps to featured_image)."""

    image = serializers.ImageField(
        write_only=True,
        required=False,
        allow_null=True,
        source="featured_image",
    )
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())
    read_time = serializers.SerializerMethodField()
    article_summary = serializers.CharField(required=False, allow_blank=True)
    faqs_json = serializers.CharField(required=False, allow_blank=True)
    tags = serializers.CharField(required=False, allow_blank=True)
    slug = serializers.SlugField(required=False, allow_blank=True, max_length=400)
    status = serializers.ChoiceField(choices=Post.STATUS_CHOICES, required=False)

    class Meta:
        model = Post
        fields = (
            "id",
            "title",
            "slug",
            "excerpt",
            "content",
            "featured_image",
            "author",
            "read_time",
            "category",
            "tags",
            "article_summary",
            "faqs_json",
            "status",
            "views_count",
            "created_at",
            "image",
        )
        read_only_fields = ("id", "created_at", "featured_image", "views_count")

    def validate_slug(self, value):
        if value in (None, ""):
            return value
        qs = Post.objects.filter(slug=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This slug is already in use.")
        return value

    def validate(self, attrs):
        if "slug" not in attrs:
            if self.partial:
                return attrs
            title = attrs.get("title") or ""
            attrs["slug"] = ensure_unique_slug(Post, slugify(title) or title, self.instance)
            return attrs
        if attrs.get("slug"):
            return attrs
        title = attrs.get("title") or (self.instance.title if self.instance else "") or ""
        attrs["slug"] = ensure_unique_slug(Post, slugify(title) or title, self.instance)
        return attrs

    def _normalize_tags(self):
        raw = self.initial_data.get("tags", "[]")
        if raw in (None, ""):
            return "[]"
        if isinstance(raw, list):
            return json.dumps(raw)
        return raw if isinstance(raw, str) else json.dumps(raw)

    def _apply_default_author(self, validated_data, instance):
        """
        Route comment/like notifications: Post.author must match a User username or email.
        When staff publishes with JWT and leaves author blank, stamp the editor's username.
        """
        request = self.context.get("request")
        if not request or not getattr(request.user, "is_authenticated", False):
            return validated_data

        has_key = "author" in validated_data
        val = (validated_data.get("author") or "").strip() if has_key else ""

        if has_key and val:
            return validated_data
        if has_key and not val:
            validated_data["author"] = request.user.get_username()
            return validated_data
        if instance is None:
            validated_data["author"] = request.user.get_username()
        elif not (instance.author or "").strip():
            validated_data["author"] = request.user.get_username()
        return validated_data

    def create(self, validated_data):
        self._apply_default_author(validated_data, None)
        validated_data.pop("tags", None)
        validated_data["tags"] = self._normalize_tags()
        if "status" not in validated_data:
            validated_data["status"] = Post.STATUS_PUBLISHED
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._apply_default_author(validated_data, instance)
        validated_data.pop("tags", None)
        if "tags" in self.initial_data:
            validated_data["tags"] = self._normalize_tags()

        # Preserve existing Cloudinary image unless a new one is uploaded.
        # Some clients send `image: null` / empty on edit; treat that as "no change".
        if "featured_image" not in validated_data:
            validated_data["featured_image"] = instance.featured_image
        else:
            incoming = validated_data.get("featured_image")
            if incoming is None:
                raw = self.initial_data.get("image", self.initial_data.get("featured_image", None))
                if raw in (None, "", "null"):
                    validated_data["featured_image"] = instance.featured_image

        return super().update(instance, validated_data)

    def get_read_time(self, obj):
        minutes = calculate_read_time(obj.content or "")
        return f"{minutes} min read"

    def to_representation(self, instance):
        request = self.context.get("request")
        featured_url = None
        if instance.featured_image:
            try:
                featured_url = instance.featured_image.url
            except Exception:
                featured_url = None

        cat = instance.category
        category_obj = {
            "id": cat.id,
            "_id": str(cat.id),
            "name": cat.name,
            "slug": cat.slug,
        }

        tags_list = coerce_tags_to_list(instance.tags)

        try:
            faqs = json.loads(instance.faqs_json) if instance.faqs_json else []
        except json.JSONDecodeError:
            faqs = []

        likes_count = instance.total_likes_count()
        liked_by_me = False
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            liked_by_me = instance.liked_users.filter(pk=request.user.pk).exists()

        return {
            "id": instance.id,
            "_id": str(instance.id),
            "title": instance.title,
            "slug": instance.slug,
            "excerpt": instance.excerpt,
            "content": instance.content,
            "featured_image": featured_url,
            "image": featured_url,
            "author": instance.author,
            "read_time": self.get_read_time(instance),
            "category": category_obj,
            "tags": tags_list,
            "article_summary": instance.article_summary,
            "articleSummary": instance.article_summary,
            "faqs_json": instance.faqs_json,
            "faqs": faqs,
            "status": instance.status,
            "views_count": instance.views_count,
            "likes_count": likes_count,
            "liked_by_me": liked_by_me,
            "created_at": instance.created_at.isoformat() if instance.created_at else None,
        }


class ContactMessageListSerializer(serializers.ModelSerializer):
    """Admin dashboard (fin-bolg-admin-fe Contact page)."""

    _id = serializers.SerializerMethodField()
    fullName = serializers.CharField(source="full_name", read_only=True)
    emailAddress = serializers.EmailField(source="email_address", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = ContactMessage
        fields = ("_id", "fullName", "emailAddress", "subject", "message", "createdAt", "updatedAt")

    def get__id(self, obj):
        return str(obj.id)


class ContactMessageWriteSerializer(serializers.ModelSerializer):
    """Inbound JSON uses camelCase (fullName, emailAddress)."""

    fullName = serializers.CharField(source="full_name", max_length=200)
    emailAddress = serializers.EmailField(source="email_address")

    class Meta:
        model = ContactMessage
        fields = ("fullName", "emailAddress", "subject", "message")

    def validate_fullName(self, value):
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Name is required.")
        return cleaned

    def validate_emailAddress(self, value):
        return (value or "").strip()

    def validate_subject(self, value):
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Subject is required.")
        return cleaned

    def validate_message(self, value):
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Message is required.")
        return cleaned


class NewsletterReviewSerializer(serializers.ModelSerializer):
    """Shape matches fin-bolg-admin-fe NewsletterReviews page (camelCase + _id)."""

    _id = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    date = serializers.SerializerMethodField()

    class Meta:
        model = NewsletterReview
        fields = ("id", "_id", "name", "email", "rating", "review", "createdAt", "updatedAt", "date")
        read_only_fields = ("id", "_id", "createdAt", "updatedAt", "date")

    def get__id(self, obj):
        return str(obj.id)

    def get_date(self, obj):
        if obj.created_at:
            return obj.created_at.strftime("%b %d, %Y")
        return ""

    def validate_rating(self, value):
        v = int(value)
        if v < 1 or v > 5:
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return v

    def validate_review(self, value):
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Review is required.")
        return cleaned


class NewsletterSubscriberSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsletterSubscriber
        fields = ("id", "email", "created_at")
        read_only_fields = ("id", "created_at")

    def validate_email(self, value):
        cleaned = (value or "").strip().lower()
        if not cleaned:
            raise serializers.ValidationError("Enter a valid email address.")
        qs = NewsletterSubscriber.objects.filter(email__iexact=cleaned)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This email is already subscribed.")
        return cleaned


class NewsletterSubscriberListSerializer(serializers.ModelSerializer):
    """Admin dashboard: same shape as fin-bolg-admin-fe Newsletter page."""

    _id = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    interestedCategories = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.SerializerMethodField()

    class Meta:
        model = NewsletterSubscriber
        fields = ("_id", "name", "email", "interestedCategories", "createdAt", "updatedAt")

    def get__id(self, obj):
        return str(obj.id)

    def get_name(self, obj):
        return ""

    def get_interestedCategories(self, obj):
        return []

    def get_updatedAt(self, obj):
        if obj.created_at:
            return obj.created_at.isoformat()
        return None


class CommentSerializer(serializers.ModelSerializer):
    _id = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ("id", "_id", "post", "name", "email", "comment", "created_at")
        read_only_fields = ("id", "_id", "created_at")

    def get__id(self, obj):
        return str(obj.id)

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Name is required.")
        return cleaned

    def validate_comment(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Comment is required.")
        return cleaned


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "kind", "message", "post", "is_read", "created_at")
        read_only_fields = ("id", "created_at")
