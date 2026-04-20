"""
Post activity notifications: deduplicated per (recipient, kind, post).
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

    # Hard fail only when system has zero users; caller can skip notification safely.
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


def comment_notification_message(comment: Comment, post: Post) -> str:
    body = (comment.comment or "").strip().replace("\n", " ")
    preview = body[:50]
    if len(body) > 50:
        preview += "..."
    title = (post.title or "Post")[:120]
    return f"New comment by {comment.name} on '{title}': {preview}"


def like_notification_message(post: Post, actor_label: str) -> str:
    title = (post.title or "Post")[:120]
    return f"Your post '{title}' received a new like from {actor_label}."


@transaction.atomic
def upsert_post_activity_notification(
    *,
    recipient: User,
    kind: str,
    post: Post,
    message: str,
) -> Notification:
    """
    One row per (recipient, kind, post). Refreshes message and marks unread on repeat events.
    """
    obj, created = Notification.objects.get_or_create(
        user=recipient,
        kind=kind,
        post=post,
        defaults={"message": message, "is_read": False},
    )
    if not created:
        Notification.objects.filter(pk=obj.pk).update(message=message, is_read=False)
        obj.refresh_from_db(fields=["message", "is_read"])
    return obj


def notify_post_comment(*, post: Post, comment: Comment) -> Notification | None:
    try:
        recipient = resolve_post_recipient_user(post)
    except RuntimeError:
        logger.warning("Skipping comment notification for post_id=%s: no users in system", post.pk)
        return None
    if actor_matches_post_author_display(post, display_name=comment.name):
        return None
    message = comment_notification_message(comment, post)
    return upsert_post_activity_notification(
        recipient=recipient,
        kind=Notification.KIND_COMMENT,
        post=post,
        message=message,
    )


def notify_post_like(
    *,
    post: Post,
    user: User | None = None,
    display_name: str | None = None,
    client_id: str | None = None,
) -> Notification | None:
    try:
        recipient = resolve_post_recipient_user(post)
    except RuntimeError:
        logger.warning("Skipping like notification for post_id=%s: no users in system", post.pk)
        return None
    if actor_matches_post_author_display(post, user=user, display_name=display_name):
        return None
    actor_label = (display_name or "").strip() or (client_id or "Someone")
    message = like_notification_message(post, actor_label)
    return upsert_post_activity_notification(
        recipient=recipient,
        kind=Notification.KIND_LIKE,
        post=post,
        message=message,
    )


def notify_post_like_authenticated_user(*, post: Post, user: User) -> Notification | None:
    label = user.get_username() or str(user.pk)
    return notify_post_like(post=post, user=user, display_name=label)
