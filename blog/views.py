import json

from django.core.paginator import Paginator
from django.db.models import F, ProtectedError, Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, Comment, ContactMessage, NewsletterReview, NewsletterSubscriber, Notification, Post, PostLike
from .notifications import notify_post_comment, notify_post_like, notify_post_like_authenticated_user
try:
    from .models import Tag
except ImportError:  # Keep compatibility when Tag model is not present.
    Tag = None
from .serializers import (
    CategorySerializer,
    CommentSerializer,
    ContactMessageListSerializer,
    ContactMessageWriteSerializer,
    NewsletterReviewSerializer,
    NewsletterSubscriberListSerializer,
    NewsletterSubscriberSerializer,
    NotificationSerializer,
    PostSerializer,
)


def _is_blog_staff(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


def _published_posts_queryset():
    return Post.objects.select_related("category").filter(status=Post.STATUS_PUBLISHED)


def _posts_queryset_for_list(request):
    qs = Post.objects.select_related("category").all().order_by("-created_at")
    if _is_blog_staff(request):
        return qs
    return qs.filter(status=Post.STATUS_PUBLISHED)


def _post_visible_to_request(request, post):
    if post.status == Post.STATUS_PUBLISHED:
        return True
    return _is_blog_staff(request)


def _resolve_post_by_lookup(lookup: str):
    if lookup.isdigit():
        try:
            return Post.objects.select_related("category").get(pk=int(lookup))
        except Post.DoesNotExist:
            pass
    try:
        return Post.objects.select_related("category").get(slug=lookup)
    except Post.DoesNotExist:
        return None


def _record_post_view(request, post):
    if post.status != Post.STATUS_PUBLISHED:
        return
    if _is_blog_staff(request):
        return
    Post.objects.filter(pk=post.pk).update(views_count=F("views_count") + 1)
    post.refresh_from_db(fields=["views_count"])


def _normalized_tag_names(request):
    """
    Return de-duplicated, normalized tag names from request payload.
    Supports multipart/form-data (getlist) and non-list payloads.
    """
    raw_tags = []
    if hasattr(request.data, "getlist"):
        raw_tags = request.data.getlist("tags")
    elif "tags" in request.data:
        value = request.data.get("tags")
        if isinstance(value, list):
            raw_tags = value
        elif value is not None:
            raw_tags = [value]

    cleaned = []
    seen = set()
    for raw in raw_tags:
        tag_name = str(raw).strip().lower()
        if not tag_name or tag_name in seen:
            continue
        seen.add(tag_name)
        cleaned.append(tag_name)
    return cleaned


def _apply_tags_to_post(post, normalized_tags, replace=False):
    """
    Attach tags for both model styles:
    1) ManyToMany Post.tags with Tag model (preferred).
    2) TextField Post.tags storing JSON array (legacy fallback).
    """
    relation = getattr(post, "tags", None)
    if relation is not None and hasattr(relation, "add") and Tag is not None:
        if replace and hasattr(relation, "clear"):
            relation.clear()
        for tag_name in normalized_tags:
            tag, _ = Tag.objects.get_or_create(name=tag_name)
            relation.add(tag)
        return

    # Fallback for text-based tags field.
    post.tags = json.dumps(normalized_tags)
    post.save(update_fields=["tags"])


def _resolve_client_id(request):
    value = (
        request.data.get("client_id")
        or request.headers.get("X-Client-Id")
        or request.GET.get("client_id")
        or request.META.get("REMOTE_ADDR")
        or "anonymous"
    )
    return str(value).strip().lower()


def _resolve_actor(request):
    if getattr(request, "user", None) and request.user.is_authenticated:
        username = request.user.get_username() or ""
        email = request.user.email or ""
    else:
        username = (
            request.data.get("username")
            or request.headers.get("X-Username")
            or request.GET.get("username")
            or ""
        )
        email = (
            request.data.get("email")
            or request.headers.get("X-User-Email")
            or request.GET.get("email")
            or ""
        )
    return str(username).strip(), str(email).strip()


def _serialize_likers(post):
    likes = PostLike.objects.filter(post=post).order_by("-created_at")
    return [
        {
            "client_id": like.client_id,
            "username": like.liker_name or like.client_id,
            "email": like.liker_email or "",
            "liked_at": like.created_at.isoformat() if like.created_at else None,
        }
        for like in likes
    ]


@method_decorator(csrf_exempt, name="dispatch")
class NewsletterSubscribeView(APIView):
    """GET: list subscribers (admin dashboard). POST: public signup."""

    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        try:
            page = max(1, int(request.GET.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            limit = int(request.GET.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = min(max(limit, 1), 200)
        search = (request.GET.get("search") or "").strip()

        qs = NewsletterSubscriber.objects.all().order_by("-created_at")
        if search:
            qs = qs.filter(email__icontains=search)
        paginator = Paginator(qs, limit)
        page_obj = paginator.get_page(page)
        ser = NewsletterSubscriberListSerializer(page_obj.object_list, many=True)
        return Response(
            {
                "data": {"subscribers": ser.data},
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": paginator.count,
                    "pages": paginator.num_pages,
                },
            }
        )

    def post(self, request):
        ser = NewsletterSubscriberSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        out = NewsletterSubscriberListSerializer(ser.instance)
        return Response(
            {"detail": "Subscribed successfully.", "subscriber": out.data},
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class NewsletterSubscriberDetailView(APIView):
    """Admin: update or delete a subscriber by id."""

    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_object(self, pk):
        try:
            return NewsletterSubscriber.objects.get(pk=pk)
        except NewsletterSubscriber.DoesNotExist:
            return None

    def put(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Subscriber not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = {"email": request.data.get("email", obj.email)}
        ser = NewsletterSubscriberSerializer(obj, data=payload, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response({"data": {"subscriber": NewsletterSubscriberListSerializer(ser.instance).data}})

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Subscriber not found."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_exempt, name="dispatch")
class ContactMessageListCreateView(APIView):
    """GET: list messages (admin). POST: public contact form."""

    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        try:
            page = max(1, int(request.GET.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            limit = int(request.GET.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = min(max(limit, 1), 200)
        search = (request.GET.get("search") or "").strip()

        qs = ContactMessage.objects.all().order_by("-created_at")
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search)
                | Q(email_address__icontains=search)
                | Q(subject__icontains=search)
                | Q(message__icontains=search)
            )
        paginator = Paginator(qs, limit)
        page_obj = paginator.get_page(page)
        ser = ContactMessageListSerializer(page_obj.object_list, many=True)
        return Response(
            {
                "data": {"contacts": ser.data},
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": paginator.count,
                    "pages": paginator.num_pages,
                },
            }
        )

    def post(self, request):
        ser = ContactMessageWriteSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        instance = ser.save()
        out = ContactMessageListSerializer(instance)
        return Response(
            {"detail": "Message received.", "contact": out.data},
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class ContactMessageDetailView(APIView):
    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_object(self, pk):
        try:
            return ContactMessage.objects.get(pk=pk)
        except ContactMessage.DoesNotExist:
            return None

    def put(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Contact not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = ContactMessageWriteSerializer(obj, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response({"data": {"contact": ContactMessageListSerializer(ser.instance).data}})

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Contact not found."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_exempt, name="dispatch")
class NewsletterReviewListCreateView(APIView):
    """
    GET: paginated list + search (admin + public).
    POST: submit a review (public).
    """

    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        try:
            page = max(1, int(request.GET.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            limit = int(request.GET.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = min(max(limit, 1), 200)
        search = (request.GET.get("search") or "").strip()

        qs = NewsletterReview.objects.all().order_by("-created_at")
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(email__icontains=search) | Q(review__icontains=search)
            )
        paginator = Paginator(qs, limit)
        page_obj = paginator.get_page(page)
        ser = NewsletterReviewSerializer(page_obj.object_list, many=True)
        return Response(
            {
                "data": {"reviews": ser.data},
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": paginator.count,
                    "pages": paginator.num_pages,
                },
            }
        )

    def post(self, request):
        ser = NewsletterReviewSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response(
            {"data": {"review": ser.data}},
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class NewsletterReviewDetailView(APIView):
    """PUT/PATCH/DELETE for admin dashboard (no JWT in current admin; same pattern as other blog APIs)."""

    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_object(self, pk):
        try:
            return NewsletterReview.objects.get(pk=pk)
        except NewsletterReview.DoesNotExist:
            return None

    def put(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = NewsletterReviewSerializer(obj, data=request.data, partial=False)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response({"data": {"review": ser.data}})

    def patch(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = NewsletterReviewSerializer(obj, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response({"data": {"review": ser.data}})

    def delete(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_exempt, name="dispatch")
class CategoryListCreateView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        qs = Category.objects.all().order_by("name")
        ser = CategorySerializer(qs, many=True, context={"request": request})
        return Response({"categories": ser.data})

    def post(self, request):
        ser = CategorySerializer(data=request.data, context={"request": request})
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response(
            {"category": ser.data},
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class CategoryDetailView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_object(self, pk):
        try:
            return Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return None

    def put(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = CategorySerializer(
            obj,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response({"category": ser.data})

    def delete(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            obj.delete()
        except ProtectedError:
            return Response(
                {"detail": "Cannot delete a category that has posts assigned."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_exempt, name="dispatch")
class PostListCreateView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        qs = _posts_queryset_for_list(request)
        ser = PostSerializer(qs, many=True, context={"request": request})
        return Response({"posts": ser.data})

    def post(self, request):
        ser = PostSerializer(data=request.data, context={"request": request})
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        post = ser.save()
        normalized_tags = _normalized_tag_names(request)
        _apply_tags_to_post(post, normalized_tags, replace=False)
        out = PostSerializer(post, context={"request": request})
        return Response({"post": out.data}, status=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name="dispatch")
class PostDetailView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_object(self, post_key):
        obj = _resolve_post_by_lookup(str(post_key))
        if not obj:
            return None
        if not _post_visible_to_request(self.request, obj):
            return None
        return obj

    def get(self, request, post_key):
        obj = self.get_object(post_key)
        if not obj:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        _record_post_view(request, obj)
        ser = PostSerializer(obj, context={"request": request})
        return Response(ser.data)

    def put(self, request, post_key):
        obj = self.get_object(post_key)
        if not obj:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = PostSerializer(
            obj,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        instance = ser.save()
        normalized_tags = _normalized_tag_names(request)
        _apply_tags_to_post(instance, normalized_tags, replace=True)
        out = PostSerializer(instance, context={"request": request})
        return Response({"post": out.data})

    def patch(self, request, post_key):
        return self.put(request, post_key)

    def delete(self, request, post_key):
        obj = self.get_object(post_key)
        if not obj:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_exempt, name="dispatch")
class PostCommentListCreateView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_post(self, request, post_id):
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return None
        if not _post_visible_to_request(request, post):
            return None
        return post

    def get(self, request, post_id):
        post = self.get_post(request, post_id)
        if not post:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = Comment.objects.filter(post=post).order_by("-created_at")
        ser = CommentSerializer(qs, many=True)
        return Response({"comments": ser.data})

    def post(self, request, post_id):
        post = self.get_post(request, post_id)
        if not post:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = request.data.copy()
        payload["post"] = str(post.id)
        ser = CommentSerializer(data=payload)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        comment = ser.save()
        notify_post_comment(post=post, comment=comment)
        out = CommentSerializer(comment)
        return Response({"comment": out.data}, status=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name="dispatch")
class CommentDetailView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_object(self, pk):
        try:
            return Comment.objects.get(pk=pk)
        except Comment.DoesNotExist:
            return None

    def put(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Comment not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = CommentSerializer(obj, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        instance = ser.save()
        out = CommentSerializer(instance)
        return Response({"comment": out.data})

    def delete(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({"detail": "Comment not found."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_exempt, name="dispatch")
class PostLikeToggleView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request, post_id):
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _post_visible_to_request(request, post):
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)

        client_id = _resolve_client_id(request)
        username, email = _resolve_actor(request)
        like = PostLike.objects.filter(post=post, client_id=client_id).first()
        if like:
            like.delete()
            liked = False
        else:
            PostLike.objects.create(
                post=post,
                client_id=client_id,
                liker_name=username,
                liker_email=email,
            )
            actor_user = request.user if request.user.is_authenticated else None
            notify_post_like(
                post=post,
                user=actor_user,
                display_name=username or None,
                client_id=client_id,
            )
            liked = True

        likes_count = post.total_likes_count()
        likers = _serialize_likers(post)
        return Response({"liked": liked, "likes_count": likes_count, "likers": likers})


@method_decorator(csrf_exempt, name="dispatch")
class PostLikeStatusView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request, post_id):
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _post_visible_to_request(request, post):
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        client_id = _resolve_client_id(request)
        liked = PostLike.objects.filter(post=post, client_id=client_id).exists()
        likes_count = post.total_likes_count()
        likers = _serialize_likers(post)
        return Response({"liked": liked, "likes_count": likes_count, "likers": likers})


@method_decorator(csrf_exempt, name="dispatch")
class PostUserLikeToggleView(APIView):
    """Toggle like for the authenticated user (ManyToMany). Requires JWT or session auth."""

    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request, post_id):
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _post_visible_to_request(request, post):
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if post.liked_users.filter(pk=user.pk).exists():
            post.liked_users.remove(user)
            liked = False
        else:
            post.liked_users.add(user)
            liked = True
            notify_post_like_authenticated_user(post=post, user=user)

        likes_count = post.total_likes_count()
        return Response({"liked": liked, "likes_count": likes_count})


@method_decorator(csrf_exempt, name="dispatch")
class PostUserLikeStatusView(APIView):
    """Return total likes and whether the current user (if any) liked via M2M."""

    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request, post_id):
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _post_visible_to_request(request, post):
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)

        likes_count = post.total_likes_count()
        liked = False
        if request.user.is_authenticated:
            liked = post.liked_users.filter(pk=request.user.pk).exists()
        return Response({"liked": liked, "likes_count": likes_count})


@method_decorator(csrf_exempt, name="dispatch")
class PostViewsAnalyticsView(APIView):
    """Staff-only: views per post and top viewed (published + drafts for staff)."""

    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        if not _is_blog_staff(request):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        qs = Post.objects.select_related("category").all().order_by("-views_count")
        rows = []
        for p in qs:
            rows.append(
                {
                    "id": p.id,
                    "title": p.title,
                    "slug": p.slug,
                    "views_count": p.views_count,
                    "status": p.status,
                }
            )
        top_viewed = rows[:25]
        chart_series = sorted(
            [{"label": r["title"][:40] + ("…" if len(r["title"]) > 40 else ""), "views": r["views_count"]} for r in rows],
            key=lambda x: x["views"],
        )
        return Response(
            {
                "top_viewed": top_viewed,
                "chart_series": chart_series,
                "posts": rows,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class NotificationListView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        base = Notification.objects.filter(user=request.user)
        qs = base.select_related("post").order_by("-created_at")[:30]
        ser = NotificationSerializer(qs, many=True)
        unread_count = base.filter(is_read=False).count()
        return Response({"notifications": ser.data, "unread_count": unread_count})


@method_decorator(csrf_exempt, name="dispatch")
class NotificationReadView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request, pk):
        updated = Notification.objects.filter(pk=pk, user=request.user).update(is_read=True)
        if not updated:
            return Response({"detail": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"detail": "Notification marked as read."})


@method_decorator(csrf_exempt, name="dispatch")
class NotificationReadAllView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"detail": "All notifications marked as read."})


@method_decorator(csrf_exempt, name="dispatch")
class CategoryPostsBySlugView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request, slug):
        try:
            category = Category.objects.get(slug=slug)
        except Category.DoesNotExist:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = _published_posts_queryset().filter(category=category).order_by("-created_at")
        ser = PostSerializer(qs, many=True, context={"request": request})
        return Response({"posts": ser.data})


@method_decorator(csrf_exempt, name="dispatch")
class PostSearchView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        q = (request.GET.get("q") or "").strip()
        if not q:
            return Response({"posts": []})
        qs = (
            _published_posts_queryset()
            .filter(Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(content__icontains=q))
            .order_by("-created_at")[:100]
        )
        ser = PostSerializer(qs, many=True, context={"request": request})
        return Response({"posts": ser.data})
