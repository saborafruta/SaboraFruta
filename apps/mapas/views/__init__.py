from .geofence import (
    GeofenceCreateView, GeofenceDeleteView, GeofenceEventosView,
    GeofenceListView, GeofenceUpdateView,
)
from .mapa import MapaPrincipalView
from .painel import PainelMapasView
from .relatorios import (
    RelatorioCoberturaView, RelatorioRegiaoView, RelatorioRotasView,
)

__all__ = [
    'MapaPrincipalView', 'PainelMapasView',
    'RelatorioRegiaoView', 'RelatorioCoberturaView', 'RelatorioRotasView',
    'GeofenceListView', 'GeofenceCreateView', 'GeofenceUpdateView',
    'GeofenceDeleteView', 'GeofenceEventosView',
]
