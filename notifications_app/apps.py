from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class NotificationsAppConfig(AppConfig):
 name = 'notifications_app'
 verbose_name = gettext_lazy('notifications app')