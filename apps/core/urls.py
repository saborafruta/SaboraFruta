from django.urls import path

from apps.core.views import admin_area
from apps.core.views import parametros as parametros_views
from apps.core.views.audit import CoreAdminLogExportCsvView, CoreAdminLogExportPdfView, CoreAdminLogItemsView
from apps.core.views import (
    CurvaAbcRelatorioView, DashboardView, LoginView, RelatoriosHubView, SelecionarFilialView, TrocarFilialView,
    VendasDowPeriodoView, atualizar_minha_foto, logout_view,
)
from apps.core.views.notificacoes import (
    NotificacaoAbrirView, NotificacaoMarcarTodasView, NotificacaoStatusView,
)

app_name = 'core'

urlpatterns = [
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('dashboard/vendas-dia-semana/', VendasDowPeriodoView.as_view(), name='dashboard-vendas-dow-periodo'),
    path('dashboard/curva-abc/relatorio/', CurvaAbcRelatorioView.as_view(), name='dashboard-curva-abc-relatorio'),
    path('relatorios/', RelatoriosHubView.as_view(), name='relatorios-hub'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/logout/', logout_view, name='logout'),
    path('auth/minha-foto/', atualizar_minha_foto, name='atualizar_minha_foto'),
    path('auth/selecionar-filial/', SelecionarFilialView.as_view(), name='selecionar-filial'),
    path('auth/trocar-filial/<int:filial_id>/', TrocarFilialView.as_view(), name='trocar-filial'),
    path('notificacoes/<int:pk>/abrir/', NotificacaoAbrirView.as_view(), name='notificacao-abrir'),
    path('notificacoes/marcar-todas/', NotificacaoMarcarTodasView.as_view(), name='notificacoes-marcar-todas'),
    path('notificacoes/status/', NotificacaoStatusView.as_view(), name='notificacoes-status'),

    path('gestao/central/', admin_area.central_administrativa, name='admin_central'),
    path('gestao/parametros/', parametros_views.parametros_sistema, name='admin_parametros'),
    path('gestao/parametros/sincronizar-focus/', parametros_views.api_sincronizar_focus, name='admin_parametros_sincronizar_focus'),
    path('gestao/parametros/revelar-segredo/', parametros_views.api_revelar_segredo, name='admin_parametros_revelar_segredo'),
    path('gestao/manutencao/limpar-fiscal/', admin_area.limpar_documentos_fiscais, name='admin_limpar_fiscal'),
    path('gestao/media-diagnostico/', admin_area.media_diagnostico, name='admin_media_diagnostico'),
    path('gestao/log/<str:tipo>/<int:pk>/registros/', CoreAdminLogItemsView.as_view(), name='admin-log-items'),
    path('gestao/log/<str:tipo>/<int:pk>/exportar/csv/', CoreAdminLogExportCsvView.as_view(), name='admin-log-export-csv'),
    path('gestao/log/<str:tipo>/<int:pk>/exportar/pdf/', CoreAdminLogExportPdfView.as_view(), name='admin-log-export-pdf'),
    path('gestao/central/empresas/<int:empresa_id>/replicacao/', admin_area.politica_replicacao_update, name='admin_politica_replicacao_update'),
    path('gestao/central/filiais/<int:filial_id>/imagem/', admin_area.filial_imagem_update, name='admin_filial_imagem_update'),
    path('gestao/central/filiais/<int:filial_id>/modulos/', admin_area.modulos_update, name='admin_modulos_update'),
    path('gestao/central/empresas/<int:empresa_id>/segmento/', admin_area.segmento_update, name='admin_segmento_update'),

    path('gestao/empresas/', admin_area.empresa_list, name='admin_empresa_list'),
    path('gestao/empresas/nova/', admin_area.empresa_form, name='admin_empresa_create'),
    path('gestao/empresas/<int:pk>/editar/', admin_area.empresa_form, name='admin_empresa_edit'),
    path('gestao/empresas/<int:pk>/toggle/', admin_area.empresa_toggle, name='admin_empresa_toggle'),

    path('gestao/filiais/', admin_area.filial_list, name='admin_filial_list'),
    path('gestao/filiais/nova/', admin_area.filial_form, name='admin_filial_create'),
    path('gestao/filiais/<int:pk>/editar/', admin_area.filial_form, name='admin_filial_edit'),
    path('gestao/filiais/<int:pk>/toggle/', admin_area.filial_toggle, name='admin_filial_toggle'),

    path('gestao/usuarios/', admin_area.usuario_list, name='admin_usuario_list'),
    path('gestao/usuarios/novo/', admin_area.usuario_form, name='admin_usuario_create'),
    path('gestao/usuarios/<int:pk>/editar/', admin_area.usuario_form, name='admin_usuario_edit'),
    path('gestao/usuarios/<int:pk>/toggle/', admin_area.usuario_toggle, name='admin_usuario_toggle'),
    path('gestao/usuarios/<int:pk>/encerrar-sessoes/', admin_area.usuario_encerrar_sessoes,
         name='admin_usuario_encerrar_sessoes'),

    path('gestao/perfis/', admin_area.perfil_list, name='admin_perfil_list'),
    path('gestao/perfis/novo/', admin_area.perfil_form, name='admin_perfil_create'),
    path('gestao/perfis/<int:pk>/editar/', admin_area.perfil_form, name='admin_perfil_edit'),
    path('gestao/perfis/<int:pk>/toggle/', admin_area.perfil_toggle, name='admin_perfil_toggle'),
    path('gestao/perfis/<int:pk>/duplicar/', admin_area.perfil_duplicar, name='admin_perfil_duplicate'),
]
