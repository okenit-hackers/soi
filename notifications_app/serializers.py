import logging

from rest_framework import serializers

from notifications_app.models import Notification, NotificationsEnabling

logger = logging.getLogger(__name__)


class NotificationSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Notification
  fields = 'pk', 'log_level', 'content', 'seen_date', 'send_date', 'created_date'


class NotificationsEnableSerializer(serializers.ModelSerializer):

 class Meta:
  model = NotificationsEnabling
  fields = 'user', 'enable_notifications'