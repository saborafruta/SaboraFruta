from django.urls import path

from apps.mapas.views import MapaPrincipalView, PainelMapasView
from apps.mapas.views import api, distancia, heatmap, rota, territorio

app_name = 'mapas'

urlpatterns = [
    path('', MapaPrincipalView.as_view(), name='mapa'),
    path('painel/', PainelMapasView.as_view(), name='painel'),

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
