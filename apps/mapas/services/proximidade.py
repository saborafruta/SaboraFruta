"""
Busca de entidades por proximidade geográfica.

O trabalho pesado fica no banco (índice GIST + `earth_distance`), não em
Python: com milhares de clientes, trazer tudo para a memória e calcular
Haversine seria O(n) por request. Ver `apps.mapas.expressions`.
"""
from __future__ import annotations

from apps.mapas import constants as c
from apps.mapas.managers import no_raio


class ProximidadeService:
    """Consultas 'o que está perto daqui'."""

    @staticmethod
    def _escopo_filiais(filial):
        """Matriz enxerga a empresa inteira; filial comum, só ela mesma.

        Mesma regra de escopo já usada no dashboard e no CRM — repetir o
        critério à mão em cada consulta é o que faz telas divergirem.
        """
        from apps.core.models import Filial

        if filial is None:
            return Filial.objects.none()
        if getattr(filial, 'is_matriz', False):
            return Filial.objects.filter(empresa=filial.empresa)
        return Filial.objects.filter(pk=filial.pk)

    @classmethod
    def clientes_proximos(
        cls, *, filial, latitude, longitude,
        raio_m=c.RAIO_PADRAO_M, limite=c.LIMITE_PROXIMIDADE,
        excluir_cliente_id=None, apenas_ativos=True,
    ):
        """
        Clientes dentro do raio, do mais perto para o mais longe.

        Enriquece cada um com os dados de recompra que o módulo de CRM já
        calcula (`RecompraCliente`): última compra, dias sem comprar, valor
        médio, frequência e score. É o que torna a sugestão do §8 útil —
        proximidade sozinha não diz se vale a pena bater na porta.
        """
        from apps.cadastros.models import Cliente

        raio_m = min(max(int(raio_m), 1), c.RAIO_MAXIMO_M)
        limite = min(max(int(limite), 1), c.LIMITE_PROXIMIDADE)

        qs = Cliente.objects.filter(filial__in=cls._escopo_filiais(filial))
        if apenas_ativos:
            qs = qs.filter(ativo=True)
        if excluir_cliente_id:
            qs = qs.exclude(pk=excluir_cliente_id)

        clientes = list(
            no_raio(qs, latitude, longitude, raio_m).only(
                'id', 'razao_social', 'nome_fantasia', 'cpf_cnpj',
                'telefone', 'celular', 'cidade', 'uf', 'bairro',
                'latitude', 'longitude',
            )[:limite]
        )
        return cls._anexar_recompra(clientes, filial)

    @staticmethod
    def _anexar_recompra(clientes, filial):
        """
        Anexa o registro de recompra de cada cliente como `.recompra`.

        Uma query só para o lote (não N+1). Se o módulo de CRM falhar por
        qualquer motivo, os clientes voltam sem o enriquecimento em vez de
        estourar a busca — a proximidade em si já tem valor.
        """
        if not clientes:
            return clientes

        try:
            from apps.crm.models import RecompraCliente

            registros = {
                r.cliente_id: r
                for r in RecompraCliente.objects.filter(
                    cliente_id__in=[cl.pk for cl in clientes],
                    filial__in=ProximidadeService._escopo_filiais(filial),
                )
            }
        except Exception:  # pragma: no cover - defensivo
            registros = {}

        for cl in clientes:
            cl.recompra = registros.get(cl.pk)
        return clientes

    @classmethod
    def proximos_de_entrega(cls, venda, *, raio_m=c.RAIO_PADRAO_M):
        """
        Clientes próximos ao endereço de entrega de uma venda de delivery
        (§8 — sugestão inteligente ao faturar).

        Devolve `(lat, lng, [clientes])`; lat/lng vêm None quando a venda não
        tem coordenada, e aí a tela simplesmente não oferece a sugestão.
        """
        lat, lng = cls._coordenada_da_venda(venda)
        if lat is None:
            return None, None, []

        clientes = cls.clientes_proximos(
            filial=venda.filial,
            latitude=lat, longitude=lng, raio_m=raio_m,
            excluir_cliente_id=venda.cliente_id,
        )
        return lat, lng, clientes

    @staticmethod
    def _coordenada_da_venda(venda):
        """
        Coordenada da entrega, na ordem: endereço de entrega da venda ->
        endereço cadastrado do cliente.

        `endereco_entrega` do PDV é um JSONField montado no checkout; pode
        trazer lat/lng se o operador ajustou no mapa, senão caímos no
        cadastro do cliente, que o backfill já geocodificou.
        """
        entrega = getattr(venda, 'endereco_entrega', None) or {}
        if isinstance(entrega, dict):
            lat, lng = entrega.get('latitude'), entrega.get('longitude')
            if lat is not None and lng is not None:
                try:
                    return float(lat), float(lng)
                except (TypeError, ValueError):
                    pass

        cliente = getattr(venda, 'cliente', None)
        if cliente is not None and cliente.tem_coordenada:
            return cliente.latitude, cliente.longitude
        return None, None
