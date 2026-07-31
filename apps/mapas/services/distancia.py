"""
Distância entre dois cadastros (§6).

"Em qualquer cadastro deve existir: calcular distância até filial, fornecedor,
cliente, motorista." Este serviço resolve qualquer par de entidades
geocodificadas e devolve distância, tempo e o traçado — reaproveitando o mesmo
provider de rotas do §4, para os dois números nunca divergirem.

Devolve TAMBÉM a distância em linha reta (`earthdistance`), que serve de
diagnóstico: quando a rota por rua é muito maior que a reta, ou há um rio/serra
no caminho, ou o endereço foi geocodificado errado.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: tipo -> (app_label, Model, campo usado como nome)
TIPOS = {
    'cliente': ('cadastros', 'Cliente', ('nome_fantasia', 'razao_social')),
    'fornecedor': ('cadastros', 'Fornecedor', ('nome_fantasia', 'razao_social')),
    'transportadora': ('cadastros', 'Transportadora', ('nome_fantasia', 'razao_social')),
    'motorista': ('cadastros', 'Motorista', ('nome',)),
    'filial': ('core', 'Filial', ('nome_fantasia', 'razao_social')),
}


def rotulo(obj, campos) -> str:
    for campo in campos:
        valor = getattr(obj, campo, '')
        if valor:
            return valor
    return f'#{obj.pk}'


class DistanciaService:
    """Distância entre dois cadastros, por rua."""

    def __init__(self, roteirizacao=None):
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        self.roteirizacao = roteirizacao or RoteirizacaoService()

    @staticmethod
    def _escopo_filiais(filial):
        from apps.mapas.services.proximidade import ProximidadeService

        return ProximidadeService._escopo_filiais(filial)

    @classmethod
    def resolver(cls, filial, tipo, pk):
        """
        Instância de um cadastro, respeitando o escopo da filial ativa.

        Devolve None quando o tipo é desconhecido, o registro não existe ou é
        de outra empresa — nunca levanta, porque a origem vem da querystring.
        """
        from django.apps import apps as django_apps

        if tipo not in TIPOS:
            return None
        app_label, model_name, _ = TIPOS[tipo]
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:  # pragma: no cover
            return None

        filiais = cls._escopo_filiais(filial)
        # Filial e escopada por si mesma; as demais, pela filial dona.
        campo = 'pk__in' if model_name == 'Filial' else 'filial__in'
        valor = filiais.values('pk') if model_name == 'Filial' else filiais
        return model.objects.filter(pk=pk, **{campo: valor}).first()

    def calcular(self, *, filial, origem_tipo, origem_id, destino_tipo, destino_id) -> dict:
        """
        Distância, tempo e traçado entre dois cadastros.

        Chaves do retorno: `erro` quando não deu, senão `distancia_km`,
        `duracao_texto`, `geometria`, `origem`, `destino` e `linha_reta_km`.
        """
        from apps.mapas.services.otimizacao import distancia_haversine_m

        origem = self.resolver(filial, origem_tipo, origem_id)
        destino = self.resolver(filial, destino_tipo, destino_id)

        if origem is None or destino is None:
            return {'erro': 'Cadastro nao encontrado nesta empresa.'}
        if not origem.tem_coordenada:
            return {'erro': f'{rotulo(origem, TIPOS[origem_tipo][2])} nao tem coordenada.'}
        if not destino.tem_coordenada:
            return {'erro': f'{rotulo(destino, TIPOS[destino_tipo][2])} nao tem coordenada.'}
        if (origem_tipo, str(origem_id)) == (destino_tipo, str(destino_id)):
            return {'erro': 'Origem e destino sao o mesmo cadastro.'}

        pontos = [
            (origem.latitude, origem.longitude),
            (destino.latitude, destino.longitude),
        ]
        try:
            rota = self.roteirizacao.roteirizador.rota(pontos)
        except Exception as exc:
            logger.warning('falha ao calcular distancia: %s', exc)
            return {'erro': 'Falha no servico de rotas.'}

        if not rota.ok:
            return {'erro': rota.erro or 'Nao foi possivel tracar a rota.'}

        reta_m = distancia_haversine_m(pontos[0], pontos[1])
        return {
            'distancia_m': round(rota.distancia_m),
            'distancia_km': rota.distancia_km,
            'duracao_s': round(rota.duracao_s),
            'duracao_texto': rota.duracao_texto,
            'geometria': rota.geometria,
            'linha_reta_km': round(reta_m / 1000, 2),
            # Rota muito maior que a reta sugere obstaculo (rio, serra) ou
            # endereco geocodificado errado -- vale o usuario saber.
            'desvio': round(rota.distancia_m / reta_m, 2) if reta_m > 50 else None,
            'origem': {
                'tipo': origem_tipo, 'id': origem.pk,
                'nome': rotulo(origem, TIPOS[origem_tipo][2]),
                'lat': origem.latitude, 'lng': origem.longitude,
            },
            'destino': {
                'tipo': destino_tipo, 'id': destino.pk,
                'nome': rotulo(destino, TIPOS[destino_tipo][2]),
                'lat': destino.latitude, 'lng': destino.longitude,
            },
        }

    @classmethod
    def buscar(cls, filial, tipo, termo, limite=15, ids=None):
        """
        Candidatos a destino, para o autocomplete do widget.

        `ids` resolve um conjunto conhecido em vez de filtrar por texto — é
        como o mapa descobre os nomes de uma rota recebida por URL (§8), sem
        precisar carregar id e nome no próprio link.
        """
        from django.apps import apps as django_apps
        from django.db.models import Q

        if tipo not in TIPOS:
            return []
        app_label, model_name, campos = TIPOS[tipo]
        model = django_apps.get_model(app_label, model_name)

        filiais = cls._escopo_filiais(filial)
        if model_name == 'Filial':
            qs = model.objects.filter(pk__in=filiais.values('pk'))
        else:
            qs = model.objects.filter(filial__in=filiais)

        # Só quem tem coordenada: oferecer destino sem coordenada garantiria
        # um erro logo em seguida.
        qs = qs.filter(latitude__isnull=False, longitude__isnull=False)

        if ids:
            qs = qs.filter(pk__in=ids)
            limite = max(limite, len(ids))
        elif termo:
            filtro = Q()
            for campo in campos:
                filtro |= Q(**{f'{campo}__icontains': termo})
            qs = qs.filter(filtro)

        return [
            {'id': obj.pk, 'nome': rotulo(obj, campos),
             'cidade': getattr(obj, 'cidade', '') or ''}
            for obj in qs[:limite]
        ]
