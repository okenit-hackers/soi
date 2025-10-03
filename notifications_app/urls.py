from django.conf.urls import url
from django.urls import path, include
from rest_framework import routers

from notifications_app import views as webline_notifications_view
from .views import change_on_sent_notification

router = routers.DefaultRouter()

router.register(r'notification', webline_notifications_view.NotificationViewSet, basename='notification')


urlpatterns = [
 url(r'notify_sent/', change_on_sent_notification, name='notify_sent'),
 path('', include(router.urls)),
]