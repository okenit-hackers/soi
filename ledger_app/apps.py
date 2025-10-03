from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class LedgerUpConfig(AppConfig):
 name = 'ledger_app'
 verbose_name = gettext_lazy('Ledger app')