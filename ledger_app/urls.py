from django.urls import path, include
from rest_framework import routers

from ledger_app import views as ledger_app_views

router = routers.DefaultRouter()
router.register(r'paid_service', ledger_app_views.PaidServiceViewSet)
router.register(r'currency', ledger_app_views.CurrencyViewSet)
router.register(r'service_account', ledger_app_views.ServiceAccountViewSet)
router.register(r'ledger', ledger_app_views.LedgerViewSet)
router.register(r'phonerent', ledger_app_views.PhoneRentViewSet)
router.register(r'phonerent_account', ledger_app_views.PhoneRentAccountViewSet)

urlpatterns = [
 path('', include(router.urls)),
]