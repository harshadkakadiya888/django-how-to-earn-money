from django.contrib import admin

from .models import Category, ContactMessage, NewsletterReview, NewsletterSubscriber, Post


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "email_address", "subject", "created_at")
    search_fields = ("full_name", "email_address", "subject", "message")
    ordering = ("-created_at",)


@admin.register(NewsletterReview)
class NewsletterReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "rating", "created_at")
    search_fields = ("name", "email", "review")
    list_filter = ("rating",)
    ordering = ("-created_at",)


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "created_at")
    search_fields = ("email",)
    ordering = ("-created_at",)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "slug", "status", "views_count", "category", "created_at")
    list_filter = ("category", "status")
    search_fields = ("title", "slug")
