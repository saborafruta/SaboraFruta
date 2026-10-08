from django.urls import path

from . import views
from . import central_views

app_name = 'whatsapp_agent'

urlpatterns = [
    path('', views.ConfiguracaoView.as_view(), name='configuracao'),
    path('fluxo/', views.FluxoConfiguracaoView.as_view(), name='fluxo-configuracao'),
    path('conectar/', views.ConectarView.as_view(), name='conectar'),
    path('conexao/', views.ConexaoView.as_view(), name='conexao'),
    path('api/status/', views.StatusConexaoView.as_view(), name='status'),
    path('conversas/', views.ConversaListView.as_view(), name='conversa-list'),
    path('conversas/<int:pk>/', views.ConversaDetailView.as_view(), name='conversa-detail'),
    path('conversas/<int:pk>/retomar/', views.RetomarAgenteView.as_view(), name='retomar-agente'),
    path('conversas/<int:pk>/encerrar/', views.EncerrarConversaView.as_view(), name='encerrar-conversa'),
    path('conversas/<int:pk>/etapa/', views.AlterarEtapaCRMView.as_view(), name='alterar-etapa-crm'),
    path('webhook/<uuid:secret>/', views.webhook, name='webhook'),
    path(
        'webhook-central/<uuid:secret>/',
        central_views.webhook_central,
        name='webhook-central',
    ),
]
