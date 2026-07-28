from django.urls import path

from . import views

app_name = 'crm'

urlpatterns = [
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
