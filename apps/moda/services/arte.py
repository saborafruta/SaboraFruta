"""
A fila da arte — o passo 5 do fluxo, visto de cima.

NÃO É A MESMA COISA QUE A FILA DE APROVAÇÃO DO PEDIDO. Aquela pergunta quem
está esperando uma decisão comercial (preço, prazo, aceite). Esta pergunta
uma coisa só, que é o que trava a fábrica:

    a arte de cada pedido já existe, e o cliente já aceitou o layout?

São perguntas diferentes com respostas diferentes: um pedido pode estar
liberado e vendido e mesmo assim não ter o escudo anexado — e é o corte que
descobre isso, tarde.

O QUE CONTA COMO "TEM ARTE" é a MESMA regra da validação de produção
(`ValidacaoProducao._arte`): peça lisa não precisa de arte e não trava; o
que trava é a peça que DECLARA personalização e não tem arquivo nem visual.
Duplicar essa regra aqui faria as duas telas discordarem sobre o mesmo
pedido — e aí ninguém sabe qual acreditar.

ORÇAMENTO ENTRA QUANDO TEM ARTE EM JOGO. Na confecção o layout costuma ser
desenhado antes de o pedido fechar: é o que convence o cliente. Um orçamento
com personalização declarada pertence a esta fila; um sem nada disso, não —
senão a tela encheria de proposta que nem arte terá.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from apps.moda.models import ArquivoPedido, PedidoProducao

# Pedido que já saiu do fluxo não tem arte a resolver.
ENCERRADOS = (
    PedidoProducao.Status.ENTREGUE,
    PedidoProducao.Status.CANCELADO,
)


@dataclass
class Arte:
    """Uma peça de arte para a tela mostrar — do item ou do pedido."""

    url: str
    nome: str
    imagem: bool
    extensao: str
    origem: str


@dataclass
class Linha:
    pedido: PedidoProducao
    aprovacao: object | None
    artes: list[Arte] = field(default_factory=list)
    sem_arte: list[str] = field(default_factory=list)
    dias_parado: int = 0

    @property
    def tem_arte(self) -> bool:
        return bool(self.artes)

    @property
    def liberado(self) -> bool:
        return bool(self.aprovacao and self.aprovacao.liberado)


class FilaArteService:

    @staticmethod
    def base(filial):
        return (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=ENCERRADOS)
            .select_related('cliente', 'aprovacao')
            .prefetch_related(
                'itens__personalizacoes', 'itens__visuais__mockup', 'arquivos',
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

        falta, pronta, enviada, ajuste, aceita = [], [], [], [], []

        for pedido in consulta:
            linha = cls._linha(pedido, hoje)
            if linha is None:
                continue

            aprovacao = linha.aprovacao
            if linha.sem_arte:
                falta.append(linha)
            elif aprovacao is not None and aprovacao.pediu_ajuste:
                ajuste.append(linha)
            elif aprovacao is not None and aprovacao.aprovado_pelo_cliente:
                aceita.append(linha)
            elif linha.liberado:
                enviada.append(linha)
            else:
                pronta.append(linha)

        for fila in (falta, pronta, enviada, ajuste):
            fila.sort(key=lambda l: -l.dias_parado)
        aceita.sort(key=lambda l: l.aprovacao.respondido_em, reverse=True)

        return {
            'falta': falta,
            'pronta': pronta,
            'enviada': enviada,
            'ajuste': ajuste,
            'aceita': aceita[:10],
            'resumo': {
                'falta': len(falta),
                'pronta': len(pronta),
                'enviada': len(enviada),
                'ajuste': len(ajuste),
                'pecas_travadas': sum(l.pedido.quantidade_total for l in falta),
            },
        }

    @classmethod
    def _linha(cls, pedido, hoje) -> Linha | None:
        artes = cls.artes_do_pedido(pedido)
        sem_arte = cls.itens_sem_arte(pedido)
        aprovacao = getattr(pedido, 'aprovacao', None)

        if pedido.status == PedidoProducao.Status.ORCAMENTO:
            # Proposta sem arte em jogo não é assunto desta tela.
            if not artes and not sem_arte:
                return None

        return Linha(
            pedido=pedido, aprovacao=aprovacao, artes=artes, sem_arte=sem_arte,
            dias_parado=(hoje - pedido.data_pedido).days,
        )

    @staticmethod
    def itens_sem_arte(pedido) -> list[str]:
        """
        Os produtos que declaram personalização e não têm o que aplicar.

        Mesma regra de `ValidacaoProducao._arte`, de propósito: é ela que
        bloqueia a liberação da produção, e uma segunda leitura aqui faria
        esta tela dizer "tudo certo" enquanto a outra trava o pedido.
        """
        faltando = []
        for item in pedido.itens.all():
            personalizacoes = list(item.personalizacoes.all())
            if not personalizacoes:
                continue
            tem_arquivo = any(p.arquivo for p in personalizacoes)
            tem_visual = any(v.tem_imagem for v in item.visuais.all())
            if not tem_arquivo and not tem_visual:
                faltando.append(item.nome_exibicao)
        return faltando

    @staticmethod
    def artes_do_pedido(pedido) -> list[Arte]:
        """
        Tudo que é arte neste pedido, de onde quer que venha.

        As duas origens existem e são diferentes: a personalização é a arte
        APLICADA numa peça (técnica e local, que a fábrica lê); o arquivo do
        pedido é o acervo que chegou do cliente. Para quem confere o layout,
        as duas são a mesma pergunta — "o que vamos imprimir?" — e por isso
        aparecem juntas.
        """
        artes = []

        for item in pedido.itens.all():
            for p in item.personalizacoes.all():
                if not p.arquivo:
                    continue
                artes.append(Arte(
                    url=p.arquivo.url,
                    nome=f'{p.get_tipo_display()}'
                         + (f' · {p.local}' if p.local else ''),
                    imagem=p.pode_pre_visualizar,
                    extensao=p.extensao,
                    origem=item.nome_exibicao,
                ))

        for anexo in pedido.arquivos.all():
            if anexo.tipo != ArquivoPedido.Tipo.ARTE:
                continue
            artes.append(Arte(
                url=anexo.arquivo.url,
                nome=anexo.descricao or anexo.nome_arquivo,
                imagem=anexo.pode_pre_visualizar,
                extensao=anexo.extensao,
                origem='Arquivo do pedido',
            ))

        return artes
