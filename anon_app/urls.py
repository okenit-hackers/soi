from django.urls import path
from rest_framework import routers
from rest_framework_jwt.views import obtain_jwt_token, refresh_jwt_token

from anon_app import views as anon_app_views
from anon_app.views import chain_checking

router = routers.DefaultRouter()
router.register(r'chain', anon_app_views.ChainView)
router.register(r'edge', anon_app_views.EdgeView)
router.register(r'node', anon_app_views.NodeView)
router.register(r'hosting', anon_app_views.HostingView)
router.register(r'hosting_account', anon_app_views.HostingAccountView)
router.register(r'server', anon_app_views.ServerView)
router.register(r'server_account', anon_app_views.ServerAccountView)
router.register(r'app_image', anon_app_views.AppImageView)
router.register(r'proxy', anon_app_views.ProxyView)

urlpatterns = [
 path('api/token/auth/', obtain_jwt_token, name='jwt-login'),
 path('api/token/refresh/', refresh_jwt_token, name='jwt-refresh'),
 path('chain_ip_address/<int:chain_pk>', anon_app_views.ChainIpAssociate.as_view()),
 path('chain_test/<int:chain_id>', chain_checking),
]