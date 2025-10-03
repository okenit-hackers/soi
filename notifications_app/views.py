import logging

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from .models import Notification
from .serializers import NotificationSerializer

logger = logging.getLogger(__name__)


class NotificationFilter(filters.FilterSet):
 send_date_is_none = filters.BooleanFilter(field_name='send_date', label='send_date_is_none', lookup_expr='isnull')
 seen_date_is_none = filters.BooleanFilter(field_name='seen_date', label='seen_date_is_none', lookup_expr='isnull')

 class Meta:
  model = Notification
  fields = '__all__'


class NotificationViewSet(viewsets.ModelViewSet):
 queryset = Notification.objects.all()
 serializer_class = NotificationSerializer
 permission_classes = (IsAuthenticated, IsAdminUser)
 filterset_class = NotificationFilter

 def list(self, request, *args, **kwargs):
  critical_notification = self.get_queryset().filter(
   Q(log_level=Notification.LogLevelChoice.COLOR_DANGER) |
   Q(log_level=Notification.LogLevelChoice.COLOR_WARNING),
   user=request.user, seen_date=None).select_related('user')

  normal_notification = self.get_queryset().filter(
   Q(log_level=Notification.LogLevelChoice.COLOR_INFO) |
   Q(log_level=Notification.LogLevelChoice.COLOR_SUCCESS),
   user=request.user, seen_date=None).select_related('user')

  if critical_notification.exists() and normal_notification.exists():
   notification_pks = []
   for notification in critical_notification.union(normal_notification):
    notification_pks.append(notification.pk)
   self.queryset = Notification.objects.filter(pk__in=notification_pks).order_by('-log_level', '-created_date')
  else:
   self.queryset = Notification.objects.filter(user=request.user)

  return super(NotificationViewSet, self).list(request, *args, **kwargs)


def change_on_sent_notification(request):
 notification_pk = request.POST['pk'] # get data from ajax

 notification = Notification.objects.get(pk=notification_pk)

 notification.send_date = timezone.now()
 notification.save()
 logger.info(f'Notification - {notification.pk}, was sent')
 return HttpResponse()