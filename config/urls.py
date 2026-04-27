"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from blog.views import GenerateBlogPostView

def home(request):
    return HttpResponse("Django is working 🚀")


def api_ai_ready(request):
    """
    No-auth JSON smoke test. If this 404s on a production base URL, that deploy
    is missing the current config/urls.py; redeploy the backend.
    """
    return JsonResponse(
        {
            "ok": True,
            "ai_generate_paths_registered": True,
            "v": 2,
            "paths": ["/api/generate-post/", "/api/ai/draft/", "/ai-generate/"],
        }
    )


# Register AI + smoke test *before* include("blog.urls") so old blog urlconfs
# never shadow /api/generate-post/.
urlpatterns = [
    path('', home),
    path('admin/', admin.site.urls),
    # JWT auth endpoints (both legacy and standard SimpleJWT aliases)
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair_standard'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh_standard'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path("api/ai/ready", api_ai_ready, name="api_ai_ready_noslash"),
    path("api/ai/ready/", api_ai_ready, name="api_ai_ready"),
    # AI generation — several paths so live/proxy setups still match one of them
    path('ai-generate/', GenerateBlogPostView.as_view(), name='ai_generate_no_api_prefix'),
    path('api/ai/draft', GenerateBlogPostView.as_view(), name='api_ai_draft_noslash'),
    path('api/ai/draft/', GenerateBlogPostView.as_view(), name='api_ai_draft'),
    path('api/generate-post', GenerateBlogPostView.as_view(), name='api_generate_post_noslash'),
    path('api/generate-post/', GenerateBlogPostView.as_view(), name='api_generate_post'),
    path('api/', include('blog.urls')),
]

# Images are stored in Cloudinary; avoid local `/media/` serving dependency.
