from django.urls import path

from apps.mapas.views import MapaPrincipalView
from apps.mapas.views import api

app_name = 'mapas'

urlpatterns = [
    path('', MapaPrincipalView.as_view(), name='mapa'),

    # APIs consumidas pelo Leaflet
    path('api/camadas/', api.camadas, name='api-camadas'),
    path('api/clientes-proximos/', api.clientes_proximos, name='api-clientes-proximos'),
    path('api/clientes/<int:pk>/', api.cliente_detalhe, name='api-cliente-detalhe'),
]
