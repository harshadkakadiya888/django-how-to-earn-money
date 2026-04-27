"""
Post activity notifications: deduplicated per (recipient, kind, post) when a post
is set; ad-hoc rows for system messages (post is NULL).
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Comment, Notification, Post

User = get_user_model()
logger = logging.getLogger(__name__)


def resolve_post_recipient_user(post: Post) -> User:
    """
    Map Post.author (free-form string in this project) to a User inbox.
    Current safe fallback keeps notifications working until Post.author is
    migrated to a proper ForeignKey in a future refactor.
    """
    author = (post.author or "").strip()
    if author:
        matched_user = User.objects.filter(username__iexact=author).first()
        if matched_user:
            return matched_user

    fallback_user = User.objects.filter(is_superuser=True).order_by("id").first()
    if fallback_user:
        logger.warning(
            "Notification recipient fallback used for post_id=%s author=%r; routed to superuser_id=%s",
            post.pk,
            post.author,
            fallback_user.pk,
        )
        return fallback_user

    any_user = User.objects.order_by("id").first()
    if any_user:
        logger.warning(
            "Notification recipient fallback used for post_id=%s author=%r; no superuser found, routed to user_id=%s",
            post.pk,
            post.author,
            any_user.pk,
        )
        return any_user

    raise RuntimeError("No users available to receive notifications.")


def actor_matches_post_author_display(
    post: Post,
    *,
    user: User | None = None,
    display_name: str | None = None,
) -> bool:
    """Avoid self-notifications when the string author matches the actor."""
    author = (post.author or "").strip()
    if not author:
        return False
    author_l = author.lower()
    if user is not None and user.is_authenticated:
        uname = (user.get_username() or "").strip().lower()
        if uname and author_l == uname:
            return True
        email = (user.email or "").strip().lower()
        if email and author_l == email:
            return True
    if display_name and display_name.strip().lower() == author_l:
        return True
    return False


def _display_from_latest(latest: str) -> str:
    """Short label for bold UI text (name or email local-part)."""
    s = (latest or "").strip()
    if not s:
        return "Someone"
    if "@" in s:
        part = s.split("@", 1)[0].strip()
        return part[:1].upper() + part[1:] if part else s[:200]
    return s[:200]


def _build_like_data(
    *,
    post: Post,
    user_label: str,
    latest_user: str,
    count: int,
) -> dict:
    title = (post.title or "Post")[:200]
    others = max(0, int(count) - 1)
    return {
        "type": Notification.KIND_LIKE,
        "user": _display_from_latest(user_label if user_label else latest_user),
        "latest_user": latest_user[:254],
        "count": int(count),
        "others_count": others,
        "post_title": title,
        "post_id": post.pk,
        "post_slug": (post.slug or "")[:400],
    }


def _build_comment_data(
    *,
    comment: Comment,
    post: Post,
    total_count: int,
    latest_user: str,
) -> dict:
    body = (comment.comment or "").strip().replace("\n", " ")
    preview = body[:100]
    if len(body) > 100:
        preview += "..."
    title = (post.title or "Post")[:200]
    name_display = (comment.name or "Someone")[:200]
    others = max(0, int(total_count) - 1)
    lu = (latest_user or "").strip() or (comment.email or "").strip() or name_display
    return {
        "type": Notification.KIND_COMMENT,
        "user": name_display,
        "latest_user": lu[:254],
        "count": int(total_count),
        "others_count": others,
        "post_title": title,
        "post_id": post.pk,
        "post_slug": (post.slug or "")[:400],
        "comment_preview": preview,
    }


def _build_view_data(*, post: Post, viewer_label: str) -> dict:
    title = (post.title or "Post")[:200]
    return {
        "type": Notification.KIND_VIEW,
        "user": viewer_label,
        "post_title": title,
        "post_id": post.pk,
        "post_slug": (post.slug or "")[:400],
        "views_count": int(post.views_count or 0),
    }


def summary_from_data(kind: str, data: dict) -> str:
    """One-line text for list views / search / email."""
    t = (data or {}).get("type") or kind
    d = data or {}
    if t == Notification.KIND_LIKE or kind == Notification.KIND_LIKE:
        u = d.get("user", "Someone")
        oc = d.get("others_count")
        if oc is not None and int(oc) > 1:
            return f'{u} and {int(oc)} others liked "{d.get("post_title", "your post")}"'
        if oc is not None and int(oc) == 1:
            return f'{u} and 1 other liked "{d.get("post_title", "your post")}"'
        return f'{u} liked "{d.get("post_title", "your post")}"'
    if t == Notification.KIND_COMMENT or kind == Notification.KIND_COMMENT:
        p = d.get("comment_preview", "")
        u = d.get("user", "Someone")
        oc = d.get("others_count")
        if oc is not None and int(oc) > 1:
            return f'{u} and {int(oc)} others on "{d.get("post_title", "your post")}": {p}'
        if oc is not None and int(oc) == 1:
            return f'{u} and 1 other on "{d.get("post_title", "your post")}": {p}'
        return f'{u} commented on "{d.get("post_title", "your post")}": {p}'
    if t == Notification.KIND_VIEW or kind == Notification.KIND_VIEW:
        vc = d.get("views_count", "")
        return f'New activity on "{d.get("post_title", "your post")}" — {vc} total views (last viewer: {d.get("user", "reader")})'
    if t == Notification.KIND_SYSTEM or kind == Notification.KIND_SYSTEM:
        return d.get("title") or d.get("body") or d.get("message", "System notification")
    return str(d.get("message", "")) or t


@transaction.atomic
def upsert_post_activity_notification(
    *,
    recipient: User,
    kind: str,
    post: Post,
    data: dict,
    message: str = "",
    renotify: bool = True,
) -> Notification:
    """
    One row per (recipient, kind, post) when post is set. Refreshes `data` on repeat.
    `renotify` controls whether the row is marked unread again (False for view bumps).
    """
    msg = message or summary_from_data(kind, data)
    obj, created = Notification.objects.get_or_create(
        user=recipient,
        kind=kind,
        post=post,
        defaults={"data": data, "message": msg, "is_read": False},
    )
    if not created:
        obj.data = data
        obj.message = msg
        if renotify:
            obj.is_read = False
        # Full save so `updated_at` (auto_now) is refreshed; .update() bypasses it.
        obj.save()
        obj.refresh_from_db(fields=["data", "message", "is_read", "updated_at", "created_at"])
    return obj


@transaction.atomic
def create_system_notification(
    *,
    user: User,
    data: dict,
    message: str = "",
) -> Notification:
    """Non–post-bound notification; always creates a new row."""
    payload = {**data, "type": data.get("type", Notification.KIND_SYSTEM)}
    msg = message or summary_from_data(Notification.KIND_SYSTEM, payload)
    return Notification.objects.create(
        user=user,
        kind=Notification.KIND_SYSTEM,
        post=None,
        data=payload,
        message=msg,
        is_read=False,
    )


def notify_post_comment(*, post: Post, comment: Comment) -> Notification | None:
    try:
        recipient = resolve_post_recipient_user(post)
    except RuntimeError:
        logger.warning("Skipping comment notification for post_id=%s: no users in system", post.pk)
        return None
    if actor_matches_post_author_display(post, display_name=comment.name):
        return None
    total_count = Comment.objects.filter(post=post).count()
    latest_user = (comment.email or "").strip() or (comment.name or "")
    payload = _build_comment_data(
        comment=comment,
        post=post,
        total_count=total_count,
        latest_user=latest_user,
    )
    return upsert_post_activity_notification(
        recipient=recipient,
        kind=Notification.KIND_COMMENT,
        post=post,
        data=payload,
        renotify=True,
    )


def notify_post_like(
    *,
    post: Post,
    user: User | None = None,
    display_name: str | None = None,
    client_id: str | None = None,
    liker_email: str | None = None,
) -> Notification | None:
    try:
        recipient = resolve_post_recipient_user(post)
    except RuntimeError:
        logger.warning("Skipping like notification for post_id=%s: no users in system", post.pk)
        return None
    if actor_matches_post_author_display(post, user=user, display_name=display_name):
        return None
    post.refresh_from_db()
    count = post.total_likes_count()
    if user and user.is_authenticated:
        email_s = (getattr(user, "email", None) or "").strip()
        uname = (user.get_username() or str(user.pk))[:200]
        latest_user = email_s or uname
        if email_s and "@" in email_s:
            user_label = _display_from_latest(email_s.split("@", 1)[0])
        else:
            user_label = uname
    else:
        email_s = (liker_email or "").strip()
        name_part = (display_name or "").strip()
        if email_s:
            latest_user = email_s
            user_label = _display_from_latest(email_s.split("@", 1)[0]) if "@" in email_s else (name_part or email_s)[:200]
        else:
            latest_user = name_part or (client_id or "anonymous")
            user_label = (name_part or (client_id or "Someone"))[:200]
    payload = _build_like_data(
        post=post,
        user_label=user_label,
        latest_user=latest_user,
        count=count,
    )
    return upsert_post_activity_notification(
        recipient=recipient,
        kind=Notification.KIND_LIKE,
        post=post,
        data=payload,
        renotify=True,
    )


def notify_post_like_authenticated_user(*, post: Post, user: User) -> Notification | None:
    return notify_post_like(
        post=post,
        user=user,
        display_name=user.get_username() or str(user.pk),
        liker_email=(user.email or None),
    )


def notify_post_view(
    *,
    post: Post,
    request_user: User | None = None,
) -> Notification | None:
    """
    Bumps a single (recipient, view, post) row with latest count; does not re-open read state.
    """
    try:
        recipient = resolve_post_recipient_user(post)
    except RuntimeError:
        return None
    if request_user and request_user.is_authenticated and actor_matches_post_author_display(
        post, user=request_user
    ):
        return None
    if request_user and request_user.is_authenticated:
        viewer = (request_user.get_username() or str(request_user.pk))[:200]
    else:
        viewer = "A reader"
    post.refresh_from_db(fields=["views_count", "title", "slug"])
    payload = _build_view_data(post=post, viewer_label=viewer)
    return upsert_post_activity_notification(
        recipient=recipient,
        kind=Notification.KIND_VIEW,
        post=post,
        data=payload,
        renotify=False,
    )


def notify_like(
    user: User | None,
    post: Post,
    *,
    display_name: str | None = None,
    client_id: str | None = None,
    liker_email: str | None = None,
) -> Notification | None:
    """
    Public helper: `user` is the liker when authenticated, else pass display_name or client_id.
    """
    return notify_post_like(
        post=post,
        user=user,
        display_name=display_name,
        client_id=client_id,
        liker_email=liker_email,
    )


def notify_comment(
    user: User | None,
    post: Post,
    comment: Comment,
) -> Notification | None:
    """
    Public helper matching ``notify_comment(user, post, comment)``; actor name comes from
    the ``comment`` row (``user`` is reserved for a future Comment→User link).
    """
    return notify_post_comment(post=post, comment=comment)


def notify_system(*, user: User, data: dict, message: str = "") -> Notification:
    """Ad-hoc system / product message to one user."""
    return create_system_notification(user=user, data=data, message=message)


# Alias for readable imports: ``from .notifications import notify_view``
notify_view = notify_post_view
