"""
As regras do recebimento de fruta.

O QUE ESTE SERVIÇO PROTEGE. Uma carga aprovada vira lote de matéria-prima,
com peso, custo e validade — e a partir daí a fábrica processa em cima dela.
Se o lote nascer com o peso errado, o custo de tudo que sair daquela fruta
nasce errado junto, e ninguém volta atrás depois que o produto foi vendido.

TRÊS DECISÕES FICAM AQUI, e não na tela:

  · APROVAR exige peso, classificação e preço. Não é burocracia: sem peso
    não há quanto pagar, sem classificação não há por que aceitar, sem
    preço não há custo — e lote sem custo contamina toda a margem depois.
  · RECUSAR exige motivo. Carga recusada é dinheiro que volta no caminhão e
    conversa com o produtor na semana seguinte; "recusado" sozinho não
    sustenta essa conversa.
  · DESVIO NÃO TRAVA, mas fica escrito. Brix meio ponto abaixo pode ser
    aceito para um produto que leva açúcar. O que não pode é a aceitação
    apagar o desvio — a medição fica no romaneio, e o lote aponta de volta
    para ele.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.estoque.models.estoque import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.polpa.models import Recebimento

ZERO = Decimal('0')
S = Recebimento.Status


class RecebimentoService:

    # ── Classificação ────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def classificar(recebimento: Recebimento, dados: dict, usuario=None) -> Recebimento:
        """
        Grava a análise da carga e marca quem mediu.

        QUEM MEDIU IMPORTA tanto quanto o número: análise sem responsável é
        o registro que ninguém consegue defender numa auditoria do MAPA, e
        é o primeiro que a fiscalização pede.
        """
        if not recebimento.editavel:
            raise DomainError(
                'Esta carga já foi decidida — a classificação não muda mais. '
                'Cancele e refaça se o registro estiver errado.'
            )

        campos = ('temperatura_chegada', 'brix', 'ph', 'acidez', 'impureza', 'danificada')
        for campo in campos:
            if campo in dados:
                setattr(recebimento, campo, dados[campo])

        recebimento.classificado_em = timezone.now()
        recebimento.classificado_por = usuario
        if recebimento.status == S.PESAGEM:
            recebimento.status = S.CLASSIFICACAO
        recebimento.save()
        return recebimento

    # ── Decisão ──────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def aprovar(recebimento: Recebimento, usuario=None) -> LoteProduto:
        """
        Aceita a carga e faz nascer o lote de matéria-prima.

        O LOTE É DO ESTOQUE (`estoque.LoteProduto`), e não uma tabela do
        vertical: é o mesmo lote que a produção consome, que a qualidade
        bloqueia e que a rastreabilidade percorre. Um lote próprio daqui
        daria dois saldos da mesma fruta e nenhum confiável.
        """
        if recebimento.encerrado:
            raise DomainError('Esta carga já foi decidida.')

        pendencias = recebimento.pendencias()
        if pendencias:
            raise DomainError(
                'Não dá para aprovar a carga — ' + ' '.join(pendencias)
            )

        produto = recebimento.fruta.produto
        if produto is None:
            raise DomainError(
                f'A fruta {recebimento.fruta} não está ligada a um produto do '
                'catálogo, e o lote precisa de um para entrar no estoque. '
                'Ligue em Formulação › Produtos.'
            )

        lote = LoteProduto.objects.create(
            filial=recebimento.filial,
            produto=produto,
            numero_lote=RecebimentoService.numero_do_lote(recebimento),
            data_fabricacao=recebimento.data,
            fornecedor=recebimento.produtor,
            numero_nota_entrada=recebimento.nota_fiscal,
            quantidade_inicial=recebimento.peso_aceito,
            # ZERO AQUI: quem enche o lote é a movimentação abaixo. Gravar o
            # peso nos dois lugares somaria duas vezes, porque a entrada faz
            # `quantidade_atual = F(...) + quantidade`. É o mesmo padrão do
            # `op_service` ao criar o lote do produto acabado.
            quantidade_atual=ZERO,
            custo_unitario=recebimento.preco_kg,
            status=LoteProduto.Status.ATIVO,
        )

        # A FRUTA PRECISA ENTRAR NO SALDO, e não só existir como lote. Sem
        # isto, `LoteProduto.quantidade_atual` subia e `Estoque` ficava em
        # zero: os dois números divergiam desde o primeiro recebimento. E é o
        # `Estoque` que a sugestão de compra, o planejamento e a reserva leem
        # -- a fábrica receberia mil quilos de acerola e o sistema continuaria
        # mandando comprar acerola.
        #
        # `MovimentacaoService` é quem pode mexer em saldo, aqui como em todo
        # o resto do ERP; e a movimentação é o lançamento que a
        # rastreabilidade e o custo do lote leem depois.
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=recebimento.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=recebimento.peso_aceito,
            usuario_id=usuario.pk if usuario else None,
            lote_id=lote.pk,
            valor_unitario=recebimento.preco_kg,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
            documento_id=recebimento.pk,
            documento_numero=recebimento.nota_fiscal or str(recebimento.numero),
            observacao=(
                f'Recebimento #{recebimento.numero} — '
                f'{recebimento.fruta} de {recebimento.produtor}'
            ),
        )
        lote.refresh_from_db()

        # O DESVIO NÃO SE PERDE. Aceitar não apaga o problema, e quem for
        # processar precisa saber que a fruta entrou fora da régua — o
        # romaneio guarda a medição e o lote aponta para ele
        # (`lote.recebimentos_polpa`), então o caminho de volta existe sem
        # duplicar a informação numa segunda coluna que envelheceria.

        recebimento.lote = lote
        recebimento.status = S.APROVADO
        recebimento.decidido_em = timezone.now()
        recebimento.decidido_por = usuario
        recebimento.save(update_fields=[
            'lote', 'status', 'decidido_em', 'decidido_por', 'updated_at',
        ])
        return lote

    @staticmethod
    @transaction.atomic
    def recusar(recebimento: Recebimento, motivo: str, usuario=None) -> Recebimento:
        """
        Devolve a carga, com o motivo por extenso.

        SEM MOTIVO NÃO RECUSA. A carga volta no caminhão e vira conversa com
        o produtor na semana seguinte -- "recusado" sozinho não sustenta
        essa conversa, e é o registro que some quando alguém precisa dele.
        """
        if recebimento.encerrado:
            raise DomainError('Esta carga já foi decidida.')

        motivo = (motivo or '').strip()
        if len(motivo) < 5:
            raise DomainError(
                'Escreva o motivo da recusa — é o que o produtor vai receber '
                'como explicação.'
            )

        recebimento.status = S.RECUSADO
        recebimento.motivo_recusa = motivo
        recebimento.decidido_em = timezone.now()
        recebimento.decidido_por = usuario
        recebimento.save(update_fields=[
            'status', 'motivo_recusa', 'decidido_em', 'decidido_por', 'updated_at',
        ])
        return recebimento

    @staticmethod
    @transaction.atomic
    def cancelar(recebimento: Recebimento, motivo: str, usuario=None) -> Recebimento:
        """
        Anula o romaneio — o caminho para o erro de digitação.

        NÃO CANCELA CARGA QUE JÁ VIROU LOTE. A fruta pode já estar dentro de
        um tanque; anular o romaneio deixaria estoque sem origem, que é pior
        do que um romaneio errado com correção registrada.
        """
        if recebimento.lote_id:
            raise DomainError(
                'Esta carga já virou lote de matéria-prima. Ajuste pelo '
                'estoque, para o saldo não ficar sem origem.'
            )
        if recebimento.status == S.CANCELADO:
            raise DomainError('Este romaneio já está cancelado.')

        recebimento.status = S.CANCELADO
        recebimento.motivo_recusa = (motivo or '').strip()
        recebimento.decidido_em = timezone.now()
        recebimento.decidido_por = usuario
        recebimento.save(update_fields=[
            'status', 'motivo_recusa', 'decidido_em', 'decidido_por', 'updated_at',
        ])
        return recebimento

    # ── Apoio ────────────────────────────────────────────────────────────

    @staticmethod
    def numero_do_lote(recebimento: Recebimento) -> str:
        """
        O número do lote CONTA DE ONDE VEIO: data + romaneio.

        Num recall, a primeira pergunta é "de que carga saiu isto?" — e um
        número sequencial anônimo obriga a consultar o sistema para
        responder. Assim o próprio rótulo já responde.
        """
        return f'MP{recebimento.data:%y%m%d}-{recebimento.numero:05d}'

    @staticmethod
    def fila(filial, filtros: dict | None = None):
        filtros = filtros or {}
        qs = (
            Recebimento.objects.for_filial(filial)
            .select_related('fruta', 'produtor', 'lote')
        )
        if filtros.get('status'):
            qs = qs.filter(status=filtros['status'])
        if filtros.get('fruta'):
            qs = qs.filter(fruta_id=filtros['fruta'])
        if filtros.get('busca'):
            termo = filtros['busca']
            from django.db.models import Q
            qs = qs.filter(
                Q(produtor__razao_social__icontains=termo)
                | Q(produtor__nome_fantasia__icontains=termo)
                | Q(fruta__nome__icontains=termo)
                | Q(nota_fiscal__icontains=termo)
                | Q(placa__icontains=termo)
            )
        return qs

    @staticmethod
    def resumo(filial, dia=None) -> dict:
        """
        O dia da balança em números.

        Só o que foi APROVADO conta como entrada: carga em classificação
        ainda pode voltar no caminhão, e somá-la faria o painel prometer
        fruta que a fábrica talvez não tenha.
        """
        dia = dia or timezone.localdate()
        do_dia = Recebimento.objects.for_filial(filial).filter(data=dia)

        aprovados = [r for r in do_dia.select_related('fruta') if r.status == S.APROVADO]
        return {
            'dia': dia,
            'cargas': do_dia.count(),
            'aguardando': do_dia.filter(status__in=Recebimento.ABERTOS).count(),
            'recusadas': do_dia.filter(status=S.RECUSADO).count(),
            'kg_aceitos': sum((r.peso_aceito for r in aprovados), ZERO),
            'valor': sum((r.valor_total for r in aprovados), ZERO),
            'polpa_prevista': sum((r.rendimento_previsto for r in aprovados), ZERO),
        }
