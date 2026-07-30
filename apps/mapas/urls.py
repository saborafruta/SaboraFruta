from django.urls import path

from apps.mapas.views import MapaPrincipalView
from apps.mapas.views import api, territorio

app_name = 'mapas'

urlpatterns = [
    path('', MapaPrincipalView.as_view(), name='mapa'),

    # APIs consumidas pelo Leaflet
    path('api/camadas/', api.camadas, name='api-camadas'),
    path('api/clientes-proximos/', api.clientes_proximos, name='api-clientes-proximos'),
    path('api/clientes/<int:pk>/', api.cliente_detalhe, name='api-cliente-detalhe'),

    # Territorios (secao 11)
    path('api/territorios/', territorio.territorios, name='api-territorios'),
    path('api/territorios/recalcular/', territorio.recalcular_territorios,
         name='api-territorios-recalcular'),
    path('api/territorios/<int:pk>/poligono/', territorio.salvar_poligono,
         name='api-territorio-poligono'),
    path('api/territorios/<int:pk>/indicadores/', territorio.indicadores_territorio,
         name='api-territorio-indicadores'),
]
