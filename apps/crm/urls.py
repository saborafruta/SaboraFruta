from django.urls import path

from . import views

app_name = 'crm'

urlpatterns = [
    path('', views.FunilComercialView.as_view(), name='funil'),
    path('carteiras/', views.CarteiraClientesView.as_view(), name='carteiras'),
    path('oportunidades/nova/', views.OportunidadeCreateView.as_view(), name='oportunidade-nova'),
    path(
        'oportunidades/<int:pk>/', views.OportunidadeDetailView.as_view(),
        name='oportunidade-detalhe',
    ),
    path(
        'oportunidades/<int:pk>/mover/', views.OportunidadeMoverView.as_view(),
        name='oportunidade-mover',
    ),
    path(
        'oportunidades/<int:pk>/nota/', views.OportunidadeNotaView.as_view(),
        name='oportunidade-nota',
    ),
    path('clientes/<int:cliente_id>/', views.Cliente360View.as_view(), name='cliente-360'),
    path('recompra/', views.AlertasRecompraView.as_view(), name='recompra'),
    path('recompra/recalcular/', views.RecompraRecalcularView.as_view(), name='recompra-recalcular'),
    path('recompra/faixas/', views.RecompraFaixasSalvarView.as_view(), name='recompra-faixas'),
    path('recompra/buscar-cliente/', views.RecompraBuscarClienteView.as_view(), name='recompra-buscar-cliente'),
    path('recompra/manual/', views.RecompraDefinirManualView.as_view(), name='recompra-manual'),
    path(
        'recompra/manual/<int:cliente_id>/remover/',
        views.RecompraRemoverManualView.as_view(), name='recompra-manual-remover',
    ),
]
