"""soi URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
 https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
 1. Add an import: from my_app import views
 2. Add a URL to urlpatterns: path('', views.home, name='home')
Class-based views
 1. Add an import: from other_app.views import Home
 2. Add a URL to urlpatterns: path('', Home.as_view(), name='home')
Including another URLconf
 1. Import the include() function: from django.urls import include, path
 2. Add a URL to urlpatterns: path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from django.contrib.admin.sites import AdminSite

from anon_app.views import import_proxies
from anon_app.urls import router as anon_router, urlpatterns as anon_urlpatterns
from ledger_app.urls import router as ledger_router
from lemmings_app.views import import_bots
from lemmings_app.urls import router as lemmings_router, urlpatterns as lmgs_urlpatterns
from notifications_app.urls import router as notifications_app_router
from notifications_app.urls import urlpatterns as notifications_app_urlpatterns


AdminSite.site_header = 'ПК СО'
AdminSite.site_title = 'ПК СО'

router = DefaultRouter()
router.registry.extend(anon_router.registry)
router.registry.extend(lemmings_router.registry)
router.registry.extend(ledger_router.registry)
router.registry.extend(notifications_app_router.registry)

urlpatterns = [
 path('', include(router.urls)),
 *lmgs_urlpatterns,
 *anon_urlpatterns,
 *notifications_app_urlpatterns,
 path(r'admin/anon_app/proxy/import/', import_proxies, name='import-proxies'),
 path(r'admin/lemmings_app/botaccount/import/', import_bots, name='import-bots'),
 path('admin/', admin.site.urls),
 path('api-auth/', include('rest_framework.urls', namespace='rest_framework')),
 path('', include('lemmings_app.urls')),
]