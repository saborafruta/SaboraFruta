from django.urls import path

from apps.mapas.views import (
    GeofenceCreateView, GeofenceDeleteView, GeofenceEventosView,
    GeofenceListView, GeofenceUpdateView,
    MapaAoVivoView, MapaPrincipalView, PainelMapasView,
    RelatorioCoberturaView, RelatorioRegiaoView, RelatorioRotasView,
)
from apps.mapas.views import (
    api, distancia, geofence, heatmap, rastreio, rota, territorio,
)

app_name = 'mapas'

urlpatterns = [
    path('', MapaPrincipalView.as_view(), name='mapa'),
    path('painel/', PainelMapasView.as_view(), name='painel'),

    # Relatorios imprimiveis / PDF
    path('relatorios/regiao/', RelatorioRegiaoView.as_view(), name='relatorio-regiao'),
    path('relatorios/cobertura/', RelatorioCoberturaView.as_view(),
         name='relatorio-cobertura'),
    path('relatorios/rotas/', RelatorioRotasView.as_view(), name='relatorio-rotas'),

    # Cercas virtuais (secao 12)
    path('cercas/', GeofenceListView.as_view(), name='geofence-list'),
    path('cercas/nova/', GeofenceCreateView.as_view(), name='geofence-novo'),
    path('cercas/<int:pk>/editar/', GeofenceUpdateView.as_view(), name='geofence-editar'),
    path('cercas/<int:pk>/excluir/', GeofenceDeleteView.as_view(), name='geofence-excluir'),
    path('cercas/eventos/', GeofenceEventosView.as_view(), name='geofence-eventos'),
    path('rastreio/', geofence.pagina_rastreio, name='rastreio'),
    path('api/posicao/', geofence.registrar_posicao, name='api-posicao'),

    # Rastreamento ao vivo (secao 13)
    path('ao-vivo/', MapaAoVivoView.as_view(), name='ao-vivo'),
    path('api/ao-vivo/', rastreio.api_ao_vivo, name='api-ao-vivo'),
    path('api/percurso/<int:pk>/', rastreio.api_percurso, name='api-percurso'),
    path('api/rastreio/<int:pk>/limpar/', rastreio.api_limpar_rastreio,
         name='api-limpar-rastreio'),

    # APIs consumidas pelo Leaflet
    path('api/camadas/', api.camadas, name='api-camadas'),
    path('api/clientes-proximos/', api.clientes_proximos, name='api-clientes-proximos'),
    path('api/clientes/<int:pk>/', api.cliente_detalhe, name='api-cliente-detalhe'),

    # Sugestao ao entregar (secao 8) -- consumida pelo Kanban de delivery
    path('api/sugestao-entrega/<int:pk>/', api.sugestao_entrega,
         name='api-sugestao-entrega'),

    # Rotas (secao 4)
    path('api/rota/', rota.criar_rota, name='api-rota'),
    path('api/rota/otimizar/', rota.otimizar_rota, name='api-rota-otimizar'),

    # Distancia entre cadastros (secao 6)
    path('api/distancia/', distancia.calcular_distancia, name='api-distancia'),
    path('api/distancia/destinos/', distancia.buscar_destino, name='api-distancia-destinos'),

    # Mapa de calor (secao 10)
    path('api/heatmap/', heatmap.heatmap, name='api-heatmap'),
    path('api/heatmap/filtros/', heatmap.heatmap_filtros, name='api-heatmap-filtros'),

    # Territorios (secao 11)
    path('api/territorios/', territorio.territorios, name='api-territorios'),
    path('api/territorios/recalcular/', territorio.recalcular_territorios,
         name='api-territorios-recalcular'),
    path('api/territorios/<int:pk>/poligono/', territorio.salvar_poligono,
         name='api-territorio-poligono'),
    path('api/territorios/<int:pk>/indicadores/', territorio.indicadores_territorio,
         name='api-territorio-indicadores'),
]
