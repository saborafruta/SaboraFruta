"""Tela principal do mapa (§1) e indicadores de cobertura (§14)."""
from django.views.generic import TemplateView

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.mapas import constants as c


class MapaPrincipalView(PermissaoRequiredMixin, TemplateView):
    """Mapa em tela cheia com o menu de camadas."""

    template_name = 'mapas/mapa.html'
    permissao_modulo = 'mapas'
    permissao_acao = 'ver'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filial = getattr(self.request, 'filial_ativa', None)

        ctx['title'] = 'Mapas e Geolocalização'
        ctx['camadas'] = [
            {'chave': k, 'rotulo': v[0], 'cor': v[1], 'icone': v[2]}
            for k, v in c.CAMADAS.items()
        ]
        ctx['raios'] = c.RAIOS_OFERECIDOS_M
        ctx['raio_padrao'] = c.RAIO_PADRAO_M
        ctx['cores_status'] = c.CORES_STATUS_RECOMPRA
        ctx['centro'] = self._centro_inicial(filial)
        ctx['cobertura'] = self._cobertura(filial)
        return ctx

    @staticmethod
    def _centro_inicial(filial):
        """
        Centro inicial do mapa: a filial ativa, se já geocodificada.

        Sem isso o mapa abriria no meio do oceano (0,0). O fallback é o centro
        aproximado do Brasil, útil enquanto o backfill não rodou.
        """
        if filial is not None and getattr(filial, 'tem_coordenada', False):
            return {'lat': filial.latitude, 'lng': filial.longitude, 'zoom': 12}
        return {'lat': -14.235, 'lng': -51.925, 'zoom': 4}

    @staticmethod
    def _cobertura(filial):
        """
        Quantos clientes têm coordenada (§14).

        Um COUNT com filtro condicional em vez de duas queries — e é o número
        que diz se o mapa está confiável ou se falta rodar o backfill.
        """
        from django.db.models import Count, Q

        from apps.cadastros.models import Cliente
        from apps.mapas.services import ProximidadeService

        filiais = ProximidadeService._escopo_filiais(filial)
        agg = Cliente.objects.filter(filial__in=filiais, ativo=True).aggregate(
            total=Count('id'),
            com_coordenada=Count('id', filter=Q(latitude__isnull=False)),
        )
        total = agg['total'] or 0
        com = agg['com_coordenada'] or 0
        return {
            'total': total,
            'com_coordenada': com,
            'sem_coordenada': total - com,
            'percentual': round(com / total * 100, 1) if total else 0.0,
        }
