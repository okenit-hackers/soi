from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class AnonAppConfig(AppConfig):
 name = 'anon_app'
 verbose_name = gettext_lazy('Anon App')