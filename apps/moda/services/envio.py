"""
A passagem do pedido para a fábrica — o passo 12 do fluxo.

O QUE ESTA TELA RESOLVE: hoje o botão "emitir ordens" mora dentro de UM
pedido. Quem faz a passagem para a produção trabalha ao contrário — olha a
carteira inteira e pergunta *o que já pode descer para a fábrica hoje*. Sem
essa lista, é abrir pedido por pedido para descobrir que oito dos dez ainda
estão travados.

E TRAVA É O ASSUNTO PRINCIPAL. Emitir a OP é o momento em que o pedido vira
tecido cortado, e daí não volta com um Ctrl+Z: as onze validações são
cobradas no serviço, não só na tela. O que faltava era MOSTRAR o motivo
antes, em lista, para a pessoa resolver os oito em vez de tentar dez vezes.

TRÊS ESTADOS, e a fronteira entre eles é objetiva:

  · PRONTO — nenhuma validação bloqueia e há item sem ordem aberta;
  · TRAVADO — há bloqueio; a tela diz qual e onde se resolve;
  · ENVIADO — todo item já tem ordem aberta. Fica visível porque "sumiu da
    lista" e "foi para a fábrica" precisam ser distinguíveis.

ENVIO PARCIAL EXISTE e é normal: o pedido tem três produtos, um ainda sem
ficha, e os outros dois já foram. `gerar_do_pedido` pula item que já tem
ordem aberta, então a tela conta os dois lados -- dizer só "enviado"
esconderia o produto que ficou para trás.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from apps.moda.models import OrdemProducao, PedidoProducao
from apps.moda.services.validacao import ValidacaoProducao

# Quem não desce para a fábrica: proposta ainda não é compromisso, entregue
# já passou, cancelado saiu do fluxo.
FORA = (
    PedidoProducao.Status.ORCAMENTO,
    PedidoProducao.Status.CANCELADO,
    PedidoProducao.Status.ENTREGUE,
)


@dataclass
class Linha:
    pedido: PedidoProducao
    bloqueios: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    ordens: list = field(default_factory=list)
    itens_total: int = 0
    itens_enviados: int = 0
    dias_para_entrega: int | None = None

    @property
    def falta_enviar(self) -> int:
        return max(self.itens_total - self.itens_enviados, 0)

    @property
    def enviado(self) -> bool:
        return self.itens_total > 0 and self.falta_enviar == 0

    @property
    def parcial(self) -> bool:
        return self.itens_enviados > 0 and self.falta_enviar > 0

    @property
    def pode_enviar(self) -> bool:
        return not self.bloqueios and self.falta_enviar > 0

    @property
    def todas_concluidas(self) -> bool:
        """
        Já produziu tudo e nenhuma ordem está aberta.

        Este pedido volta a aparecer como "pronto" porque `gerar_do_pedido`
        só pula item com ordem ABERTA -- ordem concluída não impede uma nova.
        A tela precisa dizer isso, senão alguém emite a segunda leva de uma
        peça que já foi feita.
        """
        return bool(self.ordens) and self.itens_enviados == 0

    @property
    def atrasado(self) -> bool:
        return self.dias_para_entrega is not None and self.dias_para_entrega < 0

    @property
    def apertado(self) -> bool:
        return (self.dias_para_entrega is not None
                and 0 <= self.dias_para_entrega <= 5)


class EnvioProducaoService:

    @staticmethod
    def base(filial):
        return (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=FORA)
            .select_related('cliente')
            .prefetch_related(
                'itens__produto__grade', 'itens__personalizacoes',
                'itens__visuais', 'itens__grade__tamanho',
                'ordens__item',
            )
        )

    @classmethod
    def montar(cls, filial, busca: str = '', hoje=None) -> dict:
        hoje = hoje or timezone.localdate()

        consulta = cls.base(filial)
        if busca:
            from django.db.models import Q

            termo = busca.strip()
            consulta = consulta.filter(
                Q(numero__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
            )

        prontos, travados, enviados = [], [], []

        for pedido in consulta:
            linha = cls._linha(pedido, hoje)

            if linha.enviado:
                enviados.append(linha)
            elif linha.bloqueios:
                travados.append(linha)
            else:
                prontos.append(linha)

        # Prazo manda na ordem: o que entrega antes desce antes. Pedido sem
        # data vai para o fim -- não é urgente, é indefinido, e misturar os
        # dois faria a ausência de prazo parecer folga.
        def por_prazo(linha):
            return (linha.dias_para_entrega
                    if linha.dias_para_entrega is not None else 10_000)

        prontos.sort(key=por_prazo)
        travados.sort(key=por_prazo)
        enviados.sort(key=lambda l: -l.pedido.numero)

        return {
            'prontos': prontos,
            'travados': travados,
            'enviados': enviados[:15],
            'resumo': {
                'prontos': len(prontos),
                'travados': len(travados),
                'enviados': len(enviados),
                'pecas_prontas': sum(l.pedido.quantidade_total for l in prontos),
                'atrasados': sum(1 for l in prontos + travados if l.atrasado),
                'parciais': sum(1 for l in travados + prontos if l.parcial),
            },
        }

    @classmethod
    def _linha(cls, pedido, hoje) -> Linha:
        resumo = ValidacaoProducao.resumo(pedido)

        itens = list(pedido.itens.all())
        abertas = [
            o for o in pedido.ordens.all()
            if o.status not in OrdemProducao.STATUS_ENCERRADOS
        ]
        enviados = {o.item_id for o in abertas}

        return Linha(
            pedido=pedido,
            bloqueios=resumo['bloqueios'],
            avisos=resumo['avisos'],
            # Todas as ordens, e não só as abertas: uma OP concluída também
            # é prova de que aquele item já desceu para a fábrica.
            ordens=list(pedido.ordens.all()),
            itens_total=len(itens),
            itens_enviados=sum(1 for i in itens if i.pk in enviados),
            dias_para_entrega=(
                (pedido.data_prevista_entrega - hoje).days
                if pedido.data_prevista_entrega else None
            ),
        )
