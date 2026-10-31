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
    path('crm/', views.CRMWhatsAppInboxView.as_view(), name='crm-inbox'),
    path(
        'crm/nova-conversa/', views.CRMNovaConversaDadosView.as_view(),
        name='crm-nova-conversa-dados',
    ),
    path(
        'crm/nova-conversa/iniciar/', views.CRMNovaConversaIniciarView.as_view(),
        name='crm-nova-conversa-iniciar',
    ),
    path(
        'crm/numeros/', views.CRMWhatsAppConexoesView.as_view(),
        name='crm-conexoes',
    ),
    path(
        'crm/numeros/novo/', views.CRMWhatsAppConexaoFormView.as_view(),
        name='crm-conexao-nova',
    ),
    path(
        'crm/numeros/<int:pk>/editar/', views.CRMWhatsAppConexaoFormView.as_view(),
        name='crm-conexao-editar',
    ),
    path(
        'crm/numeros/<int:pk>/conectar/',
        views.CRMWhatsAppConexaoOnboardingView.as_view(),
        name='crm-conexao-onboarding',
    ),
    path(
        'crm/numeros/<int:pk>/status/',
        views.CRMWhatsAppConexaoStatusView.as_view(),
        name='crm-conexao-status',
    ),
    path(
        'crm/<int:pk>/atualizacoes/',
        views.CRMWhatsAppAtualizacoesView.as_view(),
        name='crm-atualizacoes',
    ),
    path(
        'crm/<int:pk>/', views.CRMWhatsAppInboxView.as_view(),
        name='crm-inbox-detail',
    ),
    path(
        'crm/<int:pk>/enviar/', views.CRMWhatsAppEnviarView.as_view(),
        name='crm-enviar',
    ),
    path(
        'crm/<int:pk>/mensagens/<int:mensagem_pk>/arquivo/',
        views.CRMWhatsAppMidiaView.as_view(), name='crm-midia',
    ),
    path(
        'crm/<int:pk>/orcamento/', views.CRMOrcamentoEditorView.as_view(),
        name='crm-orcamento-editor',
    ),
    path(
        'crm/<int:pk>/orcamento/produtos/', views.CRMOrcamentoProdutosView.as_view(),
        name='crm-orcamento-produtos',
    ),
    path(
        'crm/<int:pk>/orcamento/salvar/', views.CRMOrcamentoSalvarView.as_view(),
        name='crm-orcamento-salvar',
    ),
    path(
        'crm/<int:pk>/orcamento/<int:orcamento_pk>/enviar/',
        views.CRMOrcamentoEnviarView.as_view(), name='crm-orcamento-enviar',
    ),
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
