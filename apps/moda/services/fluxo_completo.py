"""
Os 23 passos do processo, do cliente à finalização.

ISTO NÃO É UM MOTOR DE WORKFLOW. Não existe estado novo aqui, nem trava que
impeça pular etapa: cada passo é uma PERGUNTA sobre o que já está no banco
("este pedido tem grade?", "o cliente respondeu?"), respondida na hora. A
confecção real pula passos — pedido sem personalização não passa pela arte,
cliente antigo não repete aprovação — e um motor que exigisse a sequência
inteira seria contornado no primeiro dia, com o pessoal marcando etapa falsa
para poder seguir.

O que a tela faz é MOSTRAR ONDE O PEDIDO ESTÁ e qual é o próximo passo, com
o link para a tela que o executa. É um mapa, não uma cancela.

TRÊS SITUAÇÕES, e a diferença entre as duas últimas é o que dá utilidade ao
painel:

  · FEITO — já aconteceu, com a data quando dá para saber;
  · AGORA — é o próximo passo que depende de alguém desta casa;
  · ESPERANDO — depende de fora (o cliente respondendo) ou de um passo
    anterior. Não é atraso de ninguém aqui dentro, e marcar como pendente
    faria o comercial cobrar a produção de algo que está com o cliente.

Um passo que não se aplica ao pedido (arte, num pedido sem personalização)
sai como DISPENSADO, e não como pendente eterno.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.moda.models import (
    EtapaOrdem, Expedicao, Inspecao, OrdemProducao, PedidoProducao,
    Personalizacao, RegistroCorte, ReservaMaterial,
)

# As personalizações que só existem pessoa a pessoa. Escudo, patrocínio e
# arte de estampa são iguais na peça inteira e não pedem lista de nomes.
TIPOS_INDIVIDUAIS = (Personalizacao.Tipo.NOME, Personalizacao.Tipo.NUMERO)

FEITO = 'feito'
AGORA = 'agora'
ESPERANDO = 'esperando'
DISPENSADO = 'dispensado'


@dataclass
class Passo:
    numero: int
    chave: str
    label: str
    # A frase que explica o que falta — só aparece quando o passo não está
    # feito. Passo concluído não precisa de instrução.
    ajuda: str = ''
    situacao: str = ESPERANDO
    quando: datetime | date | None = None
    detalhe: str = ''
    # (nome da rota, args) da tela que executa o passo.
    rota: tuple | None = None

    @property
    def feito(self) -> bool:
        return self.situacao == FEITO

    @property
    def url(self) -> str:
        """
        O endereço da tela do passo, já resolvido.

        Sai daqui e não do template porque `{% url %}` não aceita uma lista
        de argumentos vinda de uma variável — e a alternativa seria escrever
        23 condicionais no HTML.
        """
        if not self.rota:
            return ''
        try:
            return reverse(self.rota[0], args=self.rota[1])
        except NoReverseMatch:
            return ''


def _data_da_etapa(pedido, etapa: str):
    """Quando a etapa foi concluída em qualquer ordem deste pedido."""
    return (
        EtapaOrdem.objects
        .filter(ordem__pedido=pedido, etapa=etapa,
                status=EtapaOrdem.Status.CONCLUIDA)
        .order_by('-data_conclusao')
        .values_list('data_conclusao', flat=True)
        .first()
    )


def _etapa_em_andamento(pedido, etapa: str) -> bool:
    return EtapaOrdem.objects.filter(
        ordem__pedido=pedido, etapa=etapa,
        status=EtapaOrdem.Status.EM_ANDAMENTO,
    ).exists()


class FluxoCompletoService:

    @classmethod
    def do_pedido(cls, pedido) -> dict:
        passos = cls._passos(pedido)
        cls._marcar_agora(passos)
        feitos = [p for p in passos if p.feito]
        return {
            'passos': passos,
            'feitos': len(feitos),
            'total': len([p for p in passos if p.situacao != DISPENSADO]),
            'atual': next((p for p in passos if p.situacao == AGORA), None),
            'esperando_cliente': any(
                p.chave == 'aprovacao_cliente' and p.situacao == ESPERANDO
                for p in passos
            ),
        }

    # ── Os 23 ────────────────────────────────────────────────────────────

    @classmethod
    def _passos(cls, pedido) -> list[Passo]:
        itens = list(pedido.itens.all())
        aprovacao = getattr(pedido, 'aprovacao', None)
        ordens = list(OrdemProducao.objects.filter(pedido=pedido))

        return [
            cls._cliente(pedido),
            cls._orcamento(pedido),
            cls._pedido(pedido, itens),
            cls._grade(pedido, itens),
            cls._personalizacao(pedido, itens),
            cls._arte(pedido, itens),
            cls._aprovacao_interna(pedido, aprovacao),
            cls._pdf(pedido, aprovacao),
            cls._whatsapp(pedido, aprovacao),
            cls._aprovacao_cliente(pedido, aprovacao),
            cls._ficha(pedido, itens),
            cls._pcp(pedido, ordens),
            cls._reserva(pedido, ordens),
            cls._ordem(pedido, ordens),
            *cls._chao_de_fabrica(pedido, ordens),
            cls._finalizacao(pedido),
        ]

    # ── 1 a 6: comercial ─────────────────────────────────────────────────

    @staticmethod
    def _cliente(pedido) -> Passo:
        return Passo(
            1, 'cliente', 'Cliente', situacao=FEITO,
            detalhe=str(pedido.cliente),
        )

    @staticmethod
    def _orcamento(pedido) -> Passo:
        # Orçamento é o status inicial: todo pedido passou por ele, mesmo
        # que por um minuto. Ainda estar nele é o que define "não fechado".
        e_orcamento = pedido.status == PedidoProducao.Status.ORCAMENTO
        return Passo(
            2, 'orcamento', 'Orçamento',
            ajuda='Feche o orçamento para virar pedido.',
            situacao=AGORA if e_orcamento else FEITO,
            quando=pedido.data_pedido,
            rota=('moda:pedido-detail', [pedido.pk]),
        )

    @staticmethod
    def _pedido(pedido, itens) -> Passo:
        tem_itens = bool(itens)
        return Passo(
            3, 'pedido', 'Pedido',
            ajuda='Lance ao menos um produto no pedido.',
            situacao=FEITO if tem_itens else AGORA,
            quando=pedido.data_pedido,
            detalhe=f'{len(itens)} produto(s)' if tem_itens else '',
            rota=('moda:pedido-detail', [pedido.pk]),
        )

    @staticmethod
    def _grade(pedido, itens) -> Passo:
        com_grade = [i for i in itens if i.grade.exists()]
        completo = bool(itens) and len(com_grade) == len(itens)
        return Passo(
            4, 'grade', 'Grade',
            ajuda='Distribua a quantidade por tamanho em cada produto.',
            situacao=FEITO if completo else AGORA if itens else ESPERANDO,
            detalhe=(f'{pedido.quantidade_total} peças' if completo
                     else f'{len(com_grade)} de {len(itens)} produtos com grade'),
            rota=('moda:pedido-detail', [pedido.pk]),
        )

    @staticmethod
    def _personalizacao(pedido, itens) -> Passo:
        """
        Nome e número por pessoa — só faz sentido em pedido personalizado.

        Sem isso, todo pedido de camiseta lisa ficaria com um passo pendente
        para sempre, e um painel com pendência permanente deixa de ser lido.
        """
        individuais = pedido.individuais.count()
        # Só pede nome e número por pessoa quando a arte É nome ou número.
        # Camisa com escudo da empresa tem personalização (a técnica), e
        # ninguém vai lançar 200 nomes: exigir isso deixaria uma pendência
        # eterna no pedido mais comum da confecção.
        tem_personalizacao = any(
            i.personalizacoes.filter(tipo__in=TIPOS_INDIVIDUAIS).exists()
            for i in itens
        )

        if individuais:
            return Passo(5, 'personalizacao', 'Personalização', situacao=FEITO,
                         detalhe=f'{individuais} pessoa(s)',
                         rota=('moda:pedido-detail', [pedido.pk]))
        if not tem_personalizacao:
            # Dispensado ainda leva à tela: o pedido pode ganhar nome e
            # número depois, e sem link não haveria por onde lançar.
            return Passo(5, 'personalizacao', 'Personalização',
                         situacao=DISPENSADO,
                         detalhe='sem arte de nome ou número por pessoa',
                         rota=('moda:pedido-detail', [pedido.pk]))
        return Passo(
            5, 'personalizacao', 'Personalização',
            ajuda='Lance nome e número de cada pessoa, ou marque que não há.',
            situacao=AGORA, rota=('moda:pedido-detail', [pedido.pk]),
        )

    @staticmethod
    def _arte(pedido, itens) -> Passo:
        com_arte = sum(1 for i in itens if i.personalizacoes.exists())
        visuais = sum(i.visuais.count() for i in itens)
        if com_arte or visuais:
            return Passo(6, 'arte', 'Arte', situacao=FEITO,
                         detalhe=f'{visuais} visual(is), {com_arte} produto(s) com arte',
                         rota=('moda:pedido-detail', [pedido.pk]))
        return Passo(
            6, 'arte', 'Arte',
            ajuda='Anexe a arte e os visuais das peças.',
            situacao=AGORA if itens else ESPERANDO,
            rota=('moda:pedido-detail', [pedido.pk]),
        )

    # ── 7 a 10: aprovação e envio ────────────────────────────────────────

    @staticmethod
    def _aprovacao_interna(pedido, aprovacao) -> Passo:
        if aprovacao and aprovacao.liberado:
            return Passo(
                7, 'aprovacao', 'Aprovação interna', situacao=FEITO,
                quando=aprovacao.liberado_em,
                detalhe=f'por {aprovacao.liberado_por}' if aprovacao.liberado_por_id else '',
                rota=('moda:pedido-aprovacao', [pedido.pk]),
            )
        return Passo(
            7, 'aprovacao', 'Aprovação interna',
            ajuda='Confira preço, prazo e condição antes de mandar ao cliente.',
            situacao=AGORA, rota=('moda:pedido-aprovacao', [pedido.pk]),
        )

    @staticmethod
    def _pdf(pedido, aprovacao) -> Passo:
        """
        O PDF é gerado sob demanda — não há "gerado em".

        Então o passo não pergunta "existe o arquivo?", e sim se já há o que
        colocar nele: com produto e grade, o PDF sai a qualquer momento.
        """
        pronto = pedido.quantidade_total > 0
        return Passo(
            8, 'pdf', 'PDF do pedido',
            ajuda='Lance produtos e grade para o PDF ter conteúdo.',
            situacao=FEITO if pronto else ESPERANDO,
            detalhe='gerado sob demanda, sempre com os dados de agora',
            rota=('moda:pedido-pdf', [pedido.pk]),
        )

    @staticmethod
    def _whatsapp(pedido, aprovacao) -> Passo:
        # Não dá para saber se a mensagem foi enviada: o wa.me abre o
        # WhatsApp e o envio acontece fora daqui. O que dá para afirmar é que
        # o link existe — e ele existe desde que o pedido existe.
        return Passo(
            9, 'whatsapp', 'Envio ao cliente',
            situacao=FEITO if aprovacao and aprovacao.liberado else ESPERANDO,
            ajuda='Libere o pedido internamente antes de mandar ao cliente.',
            detalhe='link e PDF prontos para enviar',
            rota=('moda:pedido-detail', [pedido.pk]),
        )

    @staticmethod
    def _aprovacao_cliente(pedido, aprovacao) -> Passo:
        if aprovacao and aprovacao.aprovado_pelo_cliente:
            return Passo(
                10, 'aprovacao_cliente', 'Aprovação do cliente', situacao=FEITO,
                quando=aprovacao.respondido_em,
                detalhe=f'por {aprovacao.respondido_por}' if aprovacao.respondido_por else '',
                rota=('moda:pedido-aprovacao', [pedido.pk]),
            )
        if aprovacao and aprovacao.pediu_ajuste:
            return Passo(
                10, 'aprovacao_cliente', 'Aprovação do cliente',
                ajuda='O cliente pediu ajuste — corrija e mande de novo.',
                situacao=AGORA, quando=aprovacao.respondido_em,
                detalhe=aprovacao.motivo_ajuste[:160],
                rota=('moda:pedido-aprovacao', [pedido.pk]),
            )
        # Esperando, e não pendente: a bola está com o cliente, e cobrar
        # isso da produção seria cobrar da pessoa errada.
        return Passo(
            10, 'aprovacao_cliente', 'Aprovação do cliente',
            ajuda='Aguardando o cliente responder pelo link.',
            situacao=ESPERANDO, rota=('moda:pedido-aprovacao', [pedido.pk]),
        )

    # ── 11 a 14: engenharia e PCP ────────────────────────────────────────

    @staticmethod
    def _ficha(pedido, itens) -> Passo:
        produtos = [i.produto for i in itens if i.produto_id]
        com_ficha = [p for p in produtos if getattr(p, 'ficha', None)]
        if produtos and len(com_ficha) == len(produtos):
            return Passo(11, 'ficha', 'Ficha técnica', situacao=FEITO,
                         detalhe=f'{len(com_ficha)} produto(s) com ficha',
                         rota=('moda:ficha-list', []))
        return Passo(
            11, 'ficha', 'Ficha técnica',
            ajuda='Cadastre a ficha dos produtos — sem ela não há material nem custo.',
            situacao=AGORA if produtos else ESPERANDO,
            detalhe=f'{len(com_ficha)} de {len(produtos)} produtos com ficha',
            rota=('moda:ficha-list', []),
        )

    @staticmethod
    def _pcp(pedido, ordens) -> Passo:
        return Passo(
            12, 'pcp', 'Planejamento PCP',
            ajuda='Confira carga e capacidade antes de emitir a ordem.',
            situacao=FEITO if ordens else AGORA,
            detalhe='pedido entrou na carga do PCP',
            rota=('moda:pcp-planejamento', []),
        )

    @staticmethod
    def _reserva(pedido, ordens) -> Passo:
        reservas = ReservaMaterial.objects.filter(ordem__in=ordens).count() if ordens else 0
        if reservas:
            return Passo(13, 'reserva', 'Reserva de material', situacao=FEITO,
                         detalhe=f'{reservas} material(is) reservado(s)',
                         rota=('moda:necessidade', []))
        return Passo(
            13, 'reserva', 'Reserva de material',
            ajuda='Reserve o material ou gere a requisição de compra.',
            situacao=AGORA if ordens else ESPERANDO,
            rota=('moda:necessidade', []),
        )

    @staticmethod
    def _ordem(pedido, ordens) -> Passo:
        if ordens:
            return Passo(
                14, 'ordem', 'Ordem de produção', situacao=FEITO,
                quando=min(o.emitida_em for o in ordens),
                detalhe=', '.join(o.numero for o in ordens[:3]),
                rota=('moda:ordem-detail', [ordens[0].pk]),
            )
        return Passo(
            14, 'ordem', 'Ordem de produção',
            ajuda='Emita a OP para o pedido descer para a fábrica.',
            situacao=AGORA, rota=('moda:pedido-detail', [pedido.pk]),
        )

    # ── 15 a 22: chão de fábrica ─────────────────────────────────────────

    @classmethod
    def _chao_de_fabrica(cls, pedido, ordens) -> list[Passo]:
        """
        Os oito passos que já vivem no fluxo da OP — lidos de lá, não
        recontados aqui.

        A etapa do fluxo é a mesma que o terminal aponta e que o WIP soma.
        Uma segunda contagem daria dois números para "o corte acabou?".
        """
        E = EtapaOrdem.Etapa
        mapa = [
            (15, 'corte', 'Corte', E.CORTE, ('moda:corte-list', [])),
            (16, 'estampa', 'Sublimação / Bordado / Silk', E.ESTAMPA,
             ('moda:terminal', ['sublimacao'])),
            (17, 'costura', 'Costura', E.COSTURA, ('moda:terminal', ['costura'])),
            (18, 'acabamento', 'Acabamento', E.ACABAMENTO,
             ('moda:terminal', ['acabamento'])),
            (19, 'qualidade', 'Qualidade', E.QUALIDADE, ('moda:qualidade-list', [])),
            (20, 'embalagem', 'Embalagem', E.EMBALAGEM, ('moda:expedicao-list', [])),
            (21, 'expedicao', 'Expedição', E.EXPEDICAO, ('moda:expedicao-list', [])),
            (22, 'entrega', 'Entrega', E.ENTREGA, ('moda:expedicao-list', [])),
        ]

        passos = []
        for numero, chave, label, etapa, rota in mapa:
            if not ordens:
                passos.append(Passo(
                    numero, chave, label, situacao=ESPERANDO,
                    ajuda='Depende da ordem de produção.', rota=rota,
                ))
                continue

            concluida = _data_da_etapa(pedido, etapa)
            if concluida:
                passos.append(Passo(numero, chave, label, situacao=FEITO,
                                    quando=concluida, rota=rota))
            elif _etapa_em_andamento(pedido, etapa):
                passos.append(Passo(numero, chave, label, situacao=AGORA,
                                    detalhe='em andamento', rota=rota))
            else:
                passos.append(Passo(numero, chave, label, situacao=ESPERANDO,
                                    rota=rota))
        return passos

    # ── 23: finalização ──────────────────────────────────────────────────

    @staticmethod
    def _finalizacao(pedido) -> Passo:
        if pedido.status == PedidoProducao.Status.ENTREGUE:
            return Passo(23, 'finalizacao', 'Finalização', situacao=FEITO,
                         detalhe='pedido entregue e encerrado',
                         rota=('moda:pedido-detail', [pedido.pk]))
        if pedido.status == PedidoProducao.Status.CANCELADO:
            return Passo(23, 'finalizacao', 'Finalização', situacao=FEITO,
                         detalhe='pedido cancelado',
                         rota=('moda:pedido-detail', [pedido.pk]))
        return Passo(
            23, 'finalizacao', 'Finalização',
            ajuda='Marque o pedido como entregue para encerrar.',
            situacao=ESPERANDO, rota=('moda:pedido-detail', [pedido.pk]),
        )

    # ── Qual é o próximo ─────────────────────────────────────────────────

    @staticmethod
    def _marcar_agora(passos) -> None:
        """
        Um único passo fica como AGORA: o primeiro que depende desta casa.

        Vários "agora" ao mesmo tempo devolveriam a pergunta ao usuário — e a
        pergunta que ele veio fazer é justamente "por onde eu continuo".
        """
        achou = False
        for passo in passos:
            if passo.situacao != AGORA:
                continue
            if achou:
                passo.situacao = ESPERANDO
            achou = True
