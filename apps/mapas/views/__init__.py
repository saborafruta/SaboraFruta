from .geofence import (
    GeofenceCreateView, GeofenceDeleteView, GeofenceEventosView,
    GeofenceListView, GeofenceUpdateView,
)
from .mapa import MapaPrincipalView
from .painel import PainelMapasView
from .rastreio import MapaAoVivoView
from .relatorios import (
    RelatorioCoberturaView, RelatorioCompletoView, RelatorioRegiaoView,
    RelatorioRotasView,
)

__all__ = [
    'MapaPrincipalView', 'PainelMapasView', 'MapaAoVivoView',
    'RelatorioRegiaoView', 'RelatorioCoberturaView', 'RelatorioRotasView',
    'RelatorioCompletoView',
    'GeofenceListView', 'GeofenceCreateView', 'GeofenceUpdateView',
    'GeofenceDeleteView', 'GeofenceEventosView',
]
