"""
A entrega: fechar a cadeia de frio na porta do cliente.

A PARADA JÁ EXISTE, A PROVA NÃO. `ItemRomaneioCarga` sabe que aquela parada
foi entregue; ninguém sabe QUEM recebeu, A QUE HORAS e EM QUE TEMPERATURA.
Um status "entregue" sozinho é a fábrica dizendo que entregou -- e quando o
cliente reclama duas semanas depois não há nada para pôr do outro lado da
mesa. Por isso o status continua sendo do romaneio, e a prova mora na ficha
deste vertical.

A CORRENTE SÓ FECHA AQUI. O túnel prova o congelamento, a câmara prova o
armazenamento, o baú prova a saída. Sem a medição na porta do cliente, a
cadeia termina no portão da fábrica -- que é justamente o trecho contestado.

NOME É PROVA, "SIM" NÃO É. Quem assina o canhoto responde por ter recebido
o produto naquele estado; um booleano transforma toda divergência em
palavra contra palavra. Por isso `recebido_por` é obrigatório para marcar
uma entrega -- e é a única exigência da tela.

NÃO ENTREGUE PRECISA DE MOTIVO, pela mesma razão pela outra ponta: sem
razão vira número em relatório e some; com razão, vira roteiro que muda.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.logistica.models import ItemRomaneioCarga, RomaneioCarga
from apps.polpa.models import EntregaFria

# A parada que ainda interessa a esta tela: o caminhão saiu e a entrega
# ainda não terminou, ou terminou hoje.
NA_RUA = (RomaneioCarga.Status.EM_ROTA, RomaneioCarga.Status.ENTREGUE)
RESOLVIDAS = (
    ItemRomaneioCarga.StatusEntrega.ENTREGUE,
    ItemRomaneioCarga.StatusEntrega.NAO_ENTREGUE,
    ItemRomaneioCarga.StatusEntrega.CANCELADO,
)


class EntregaService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def ficha(parada) -> EntregaFria:
        """A ficha da parada — criada na primeira vez que se olha."""
        ficha, _ = EntregaFria.objects.get_or_create(
            parada=parada, defaults={'filial': parada.romaneio.filial},
        )
        return ficha

    @staticmethod
    def temperatura_exigida(parada) -> Decimal | None:
        """
        A mais exigente entre os produtos desta parada.

        `None` quando nenhum produto declara: é "ninguém cadastrou", e a
        tela diz isso em vez de aprovar qualquer número.
        """
        if not parada.pedido_venda_id:
            return None

        from apps.vendas.models import ItemPedidoVenda

        temperaturas = [
            t for t in ItemPedidoVenda.objects
            .filter(pedido_id=parada.pedido_venda_id)
            .values_list('produto__temperatura_maxima', flat=True)
            if t is not None
        ]
        return min(temperaturas) if temperaturas else None

    @classmethod
    def _linha(cls, parada, hoje) -> dict:
        ficha = getattr(parada, 'ficha_polpa', None)
        exigida = cls.temperatura_exigida(parada)
        temperatura = ficha.temperatura if ficha else None
        carga = getattr(parada.romaneio, 'ficha_polpa', None)

        return {
            'parada': parada,
            'romaneio': parada.romaneio,
            'ficha': ficha,
            'pedido': parada.pedido_venda,
            'exigida': exigida,
            'temperatura': temperatura,
            # O BAÚ NA SAÍDA AO LADO DA ENTREGA: "saiu a -19 e chegou a -12"
            # é a frase que explica o problema; só a segunda metade não.
            'saida': carga.temperatura_bau if carga else None,
            'fora_da_faixa': bool(
                temperatura is not None and exigida is not None
                and temperatura > exigida
            ),
            'entregue': (
                parada.status_entrega == ItemRomaneioCarga.StatusEntrega.ENTREGUE
            ),
            'nao_entregue': (
                parada.status_entrega
                == ItemRomaneioCarga.StatusEntrega.NAO_ENTREGUE
            ),
            'pendente': parada.status_entrega not in RESOLVIDAS,
            'sem_medicao': bool(
                parada.status_entrega
                == ItemRomaneioCarga.StatusEntrega.ENTREGUE
                and temperatura is None
            ),
        }

    @classmethod
    def paradas(cls, filial, filtros: dict | None = None) -> list[dict]:
        """
        As entregas da rua, as pendentes primeiro.

        Entrega resolvida só continua na lista no dia em que aconteceu --
        depois disso ela é histórico, e histórico misturado com trabalho faz
        a pessoa parar de olhar a lista.
        """
        filtros = filtros or {}
        hoje = timezone.localdate()

        qs = (
            ItemRomaneioCarga.objects
            .filter(romaneio__filial=filial, romaneio__status__in=NA_RUA)
            .select_related(
                'romaneio', 'romaneio__ficha_polpa', 'pedido_venda',
                'ficha_polpa',
            )
        )
        if filtros.get('busca'):
            from django.db.models import Q
            termo = filtros['busca']
            qs = qs.filter(
                Q(cliente_nome__icontains=termo)
                | Q(romaneio__motorista_nome__icontains=termo)
                | Q(romaneio__veiculo_placa__icontains=termo)
            )

        linhas = []
        for parada in qs:
            linha = cls._linha(parada, hoje)
            if not linha['pendente']:
                quando = linha['ficha'].entregue_em if linha['ficha'] else None
                atual = (
                    timezone.localtime(quando).date() if quando
                    else parada.updated_at and timezone.localtime(
                        parada.updated_at,
                    ).date()
                )
                if atual != hoje:
                    continue
            linhas.append(linha)

        linhas.sort(key=lambda l: (
            not l['pendente'], l['romaneio'].numero, l['parada'].ordem,
        ))
        return linhas

    @classmethod
    def resumo(cls, linhas) -> dict:
        entregues = [l for l in linhas if l['entregue']]
        return {
            'na_rua': sum(1 for l in linhas if l['pendente']),
            'entregues': len(entregues),
            'nao_entregues': sum(1 for l in linhas if l['nao_entregue']),
            'sem_medicao': sum(1 for l in entregues if l['sem_medicao']),
            'fora_da_faixa': sum(1 for l in entregues if l['fora_da_faixa']),
        }

    # ── Ação ─────────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def entregar(cls, parada, dados: dict, usuario=None) -> EntregaFria:
        """
        Registra a entrega: quem recebeu, quando e em que temperatura.

        NOME É A ÚNICA EXIGÊNCIA. A temperatura devia vir sempre, mas
        recusar o registro por falta dela faria a entrega ficar sem NENHUM
        canhoto -- pior do que um canhoto sem termômetro. A tela cobra o que
        falta; o registro acontece.
        """
        nome = (dados.get('recebido_por') or '').strip()
        if not nome:
            raise DomainError(
                'Informe quem recebeu — um "entregue" sem nome não prova nada.'
            )

        ficha = cls.ficha(parada)
        ficha.recebido_por = nome
        ficha.documento = (dados.get('documento') or '').strip()
        ficha.temperatura = dados.get('temperatura')
        ficha.observacao = (dados.get('observacao') or '').strip()
        ficha.ocorrencia = ''
        ficha.entregue_em = dados.get('entregue_em') or timezone.now()
        ficha.registrada_por = usuario
        ficha.save()

        parada.status_entrega = ItemRomaneioCarga.StatusEntrega.ENTREGUE
        parada.save(update_fields=['status_entrega', 'updated_at'])
        cls._fechar_romaneio(parada.romaneio)
        return ficha

    @classmethod
    @transaction.atomic
    def nao_entregar(cls, parada, dados: dict, usuario=None) -> EntregaFria:
        """
        Registra a ocorrência de quem voltou com a carga.

        O MOTIVO É OBRIGATÓRIO: sem ele a não entrega vira número em
        relatório e some. Com ele, vira roteiro que muda -- cliente fechado
        às duas da tarde, endereço errado no cadastro, recusa por
        temperatura.
        """
        ocorrencia = (dados.get('ocorrencia') or '').strip()
        if ocorrencia not in EntregaFria.Ocorrencia.values:
            raise DomainError('Escolha o motivo da não entrega.')

        ficha = cls.ficha(parada)
        ficha.ocorrencia = ocorrencia
        ficha.observacao = (dados.get('observacao') or '').strip()
        ficha.temperatura = dados.get('temperatura')
        ficha.entregue_em = None
        ficha.recebido_por = ''
        ficha.registrada_por = usuario
        ficha.save()

        parada.status_entrega = ItemRomaneioCarga.StatusEntrega.NAO_ENTREGUE
        parada.save(update_fields=['status_entrega', 'updated_at'])
        cls._fechar_romaneio(parada.romaneio)
        return ficha

    @staticmethod
    def _fechar_romaneio(romaneio):
        """
        Romaneio com todas as paradas resolvidas vira entregue.

        DERIVADO, e não marcado à mão: um botão "encerrar romaneio" ao lado
        das paradas seria um segundo lugar para dizer a mesma coisa, e os
        dois discordariam no dia em que alguém clicasse antes da hora.
        """
        if romaneio.status != RomaneioCarga.Status.EM_ROTA:
            return
        pendentes = romaneio.itens.exclude(status_entrega__in=RESOLVIDAS)
        if pendentes.exists():
            return
        romaneio.status = RomaneioCarga.Status.ENTREGUE
        romaneio.save(update_fields=['status', 'updated_at'])
