"""
OrdemProducaoService — máquina de estados + explosão de BOM.

Fluxo:
    abrir_op() → rascunho → aberta (valida MP disponível)
    iniciar() → aberta → em_producao (marca início)
    encerrar() → em_producao → encerrada (consome MP, gera lote, calcula custo)
    cancelar() → rascunho|aberta → cancelada
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import (
    DadosInvalidosError, EstoqueInsuficienteError,
)
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.producao.models import FichaTecnica, OrdemProducao, PerdaProducao

logger = logging.getLogger(__name__)


class OrdemProducaoService:
    """Operações atômicas e validações de OP."""

    # Gatilhos de alerta
    RENDIMENTO_MINIMO_ALERTA = Decimal('80')  # <80% dispara alerta
    PERDA_MAXIMA_ACEITAVEL = Decimal('5')  # >5% dispara revisão

    # ----------------------------------------------------------------------
    # Criação de OP
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def criar_op(
        cls,
        ficha_id: int,
        quantidade_planejada: Decimal,
        filial,
        usuario,
        data_inicio_prevista=None,
        observacao: str = '',
    ) -> OrdemProducao:
        """Cria OP em status rascunho."""
        try:
            ficha = FichaTecnica.objects.get(
                pk=ficha_id, filial=filial, status=FichaTecnica.Status.ATIVA,
            )
        except FichaTecnica.DoesNotExist:
            raise DadosInvalidosError('Ficha técnica inválida ou inativa.')

        if quantidade_planejada <= 0:
            raise DadosInvalidosError('Quantidade planejada deve ser positiva.')

        numero = cls._gerar_numero(filial)

        op = OrdemProducao.objects.create(
            filial=filial,
            numero=numero,
            ficha_tecnica=ficha,
            produto_acabado=ficha.produto_acabado,
            quantidade_planejada=quantidade_planejada,
            status=OrdemProducao.Status.RASCUNHO,
            data_inicio_prevista=data_inicio_prevista,
            usuario_abertura=usuario,
            observacao=observacao,
        )
        return op

    @staticmethod
    def _gerar_numero(filial) -> str:
        """Gera número sequencial por filial: OP-0000001."""
        ultima = OrdemProducao.objects.filter(filial=filial).order_by('-pk').first()
        if not ultima or not ultima.numero.startswith('OP-'):
            return 'OP-0000001'
        try:
            num = int(ultima.numero.split('-')[1]) + 1
        except (ValueError, IndexError):
            num = 1
        return f'OP-{num:07d}'

    # ----------------------------------------------------------------------
    # Abrir OP (rascunho → aberta)
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def abrir(cls, op: OrdemProducao, usuario) -> OrdemProducao:
        """Valida disponibilidade de MP e abre a OP."""
        if not op.pode_abrir:
            raise DadosInvalidosError(
                f'OP só pode ser aberta a partir do status rascunho. Status atual: {op.get_status_display()}.'
            )

        # Valida MP: percorre BOM e verifica estoque
        faltas = cls._calcular_faltas_mp(op)
        if faltas:
            descricao = '; '.join(
                f'{f["produto"]}: falta {f["falta"]} {f["unidade"]}'
                for f in faltas
            )
            raise EstoqueInsuficienteError(f'Matéria-prima insuficiente: {descricao}')

        op.status = OrdemProducao.Status.ABERTA
        op.save(update_fields=['status', 'updated_at'])
        return op

    @classmethod
    def _calcular_faltas_mp(cls, op: OrdemProducao) -> list[dict]:
        """Retorna lista de MPs com estoque insuficiente para a OP."""
        faltas = []
        fator = op.quantidade_planejada / op.ficha_tecnica.quantidade_produzida

        for item in op.ficha_tecnica.itens.select_related('materia_prima', 'materia_prima__unidade_medida'):
            necessario = item.quantidade_com_perda() * fator
            try:
                estoque = Estoque.objects.get(
                    produto=item.materia_prima, filial=op.filial,
                )
                disponivel = estoque.quantidade_disponivel
            except Estoque.DoesNotExist:
                disponivel = Decimal('0')

            if disponivel < necessario:
                faltas.append({
                    'produto': item.materia_prima.descricao,
                    'necessario': necessario,
                    'disponivel': disponivel,
                    'falta': necessario - disponivel,
                    'unidade': item.materia_prima.unidade_medida.sigla,
                })
        return faltas

    # ----------------------------------------------------------------------
    # Iniciar produção
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def iniciar(cls, op: OrdemProducao, usuario) -> OrdemProducao:
        """aberta → em_producao. Registra data/hora de início."""
        if not op.pode_iniciar:
            raise DadosInvalidosError(
                f'OP só pode ser iniciada a partir do status "aberta". Status atual: {op.get_status_display()}.'
            )
        op.status = OrdemProducao.Status.EM_PRODUCAO
        op.data_inicio_real = timezone.now()
        op.save(update_fields=['status', 'data_inicio_real', 'updated_at'])
        return op

    # ----------------------------------------------------------------------
    # Encerrar OP (em_producao → encerrada)
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def encerrar(
        cls,
        op: OrdemProducao,
        usuario,
        quantidade_produzida: Decimal,
        peso_saida: Decimal | None = None,
        numero_lote_gerado: str = '',
        data_validade=None,
        perdas: list[dict] | None = None,
    ) -> OrdemProducao:
        """
        Encerra a OP:
          1. Explode BOM e consome MP via MovimentacaoService (FEFO).
          2. Cria LoteProduto para o produto acabado.
          3. Registra entrada de estoque do PA.
          4. Calcula rendimento, custo e dispara alertas.
          5. Registra perdas classificadas.
        """
        if not op.pode_encerrar:
            raise DadosInvalidosError(
                f'OP só pode ser encerrada a partir de "em_producao". Status atual: {op.get_status_display()}.'
            )
        if quantidade_produzida <= 0:
            raise DadosInvalidosError('Quantidade produzida deve ser positiva.')

        fator = op.quantidade_planejada / op.ficha_tecnica.quantidade_produzida
        custo_mp_total = Decimal('0')
        peso_entrada = Decimal('0')

        # 1. Consumir MP (BOM × fator)
        for item in op.ficha_tecnica.itens.select_related('materia_prima'):
            qtd_consumir = item.quantidade_com_perda() * fator
            movimentacoes = MovimentacaoService.registrar_saida_fefo(
                produto_id=item.materia_prima_id,
                filial_id=op.filial_id,
                quantidade=qtd_consumir,
                usuario_id=usuario.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
                documento_id=op.pk,
                documento_numero=op.numero,
            )
            for m in movimentacoes:
                if m.valor_total:
                    custo_mp_total += m.valor_total
            if item.materia_prima.peso_liquido:
                peso_entrada += qtd_consumir * item.materia_prima.peso_liquido

        # 2. Criar lote de produto acabado
        if not numero_lote_gerado:
            numero_lote_gerado = cls._gerar_numero_lote(op)

        custo_total = (
            custo_mp_total
            + op.ficha_tecnica.custo_mao_obra_padrao
            + op.ficha_tecnica.custo_indireto_padrao
        )
        custo_unitario = (
            (custo_total / quantidade_produzida).quantize(Decimal('0.0001'))
            if quantidade_produzida > 0
            else Decimal('0')
        )

        lote_pa = LoteProduto.objects.create(
            filial=op.filial,
            produto=op.produto_acabado,
            numero_lote=numero_lote_gerado,
            data_fabricacao=timezone.localdate(),
            data_validade=data_validade,
            ordem_producao_id=op.pk,
            quantidade_inicial=quantidade_produzida,
            quantidade_atual=0,  # incrementado pela movimentação
            custo_unitario=custo_unitario,
            status=LoteProduto.Status.ATIVO,
        )

        # 3. Entrada do PA no estoque
        MovimentacaoService.registrar_movimentacao(
            produto_id=op.produto_acabado_id,
            filial_id=op.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
            quantidade=quantidade_produzida,
            usuario_id=usuario.pk,
            lote_id=lote_pa.pk,
            valor_unitario=custo_unitario,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            documento_id=op.pk,
            documento_numero=op.numero,
            observacao=f'Entrada por produção — OP {op.numero}',
        )

        # 4. Registrar perdas
        if perdas:
            for p in perdas:
                cls._registrar_perda(op, usuario, p)

        # 5. Calcular rendimento e custos
        rendimento = (quantidade_produzida / op.quantidade_planejada) * 100
        rendimento = rendimento.quantize(Decimal('0.01'))

        op.status = OrdemProducao.Status.ENCERRADA
        op.data_fim_real = timezone.now()
        op.quantidade_produzida = quantidade_produzida
        op.peso_entrada_mp = peso_entrada
        op.peso_saida_produzido = peso_saida or Decimal('0')
        op.rendimento = rendimento
        op.custo_materia_prima = custo_mp_total
        op.custo_mao_obra = op.ficha_tecnica.custo_mao_obra_padrao
        op.custo_indireto = op.ficha_tecnica.custo_indireto_padrao
        op.custo_total = custo_total
        op.custo_unitario_lote = custo_unitario
        op.lote_gerado = lote_pa
        op.usuario_encerramento = usuario
        op.save()

        # 6. Alertas
        if rendimento < cls.RENDIMENTO_MINIMO_ALERTA:
            logger.warning(
                'OP %s com rendimento baixo: %s%% (mínimo esperado: %s%%)',
                op.numero, rendimento, cls.RENDIMENTO_MINIMO_ALERTA,
            )

        return op

    @staticmethod
    def _gerar_numero_lote(op: OrdemProducao) -> str:
        """Gera número de lote padrão: L<YYYYMMDD>-<OP-number>"""
        hoje = timezone.localdate()
        return f'L{hoje:%Y%m%d}-{op.numero.replace("OP-", "")}'

    @staticmethod
    def _registrar_perda(op: OrdemProducao, usuario, perda: dict):
        from apps.produtos.models import Produto
        produto = Produto.objects.get(pk=perda['produto_id'])
        quantidade = Decimal(str(perda['quantidade']))
        valor = quantidade * produto.preco_custo_medio
        PerdaProducao.objects.create(
            ordem_producao=op,
            tipo_perda=perda.get('tipo_perda') or perda.get('categoria') or 'processo',
            produto=produto,
            quantidade=quantidade,
            unidade_medida=getattr(produto.unidade_medida, 'sigla', '') if produto.unidade_medida_id else '',
            impacto_custo=valor,
            motivo_detalhado=perda.get('motivo_detalhado') or perda.get('descricao', ''),
            usuario_registro=usuario,
        )

    # ----------------------------------------------------------------------
    # Cancelar
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def cancelar(cls, op: OrdemProducao, usuario, motivo: str) -> OrdemProducao:
        if not op.pode_cancelar:
            raise DadosInvalidosError(
                f'OP não pode ser cancelada no status atual ({op.get_status_display()}).'
            )
        if not motivo.strip():
            raise DadosInvalidosError('Cancelamento exige motivo.')
        op.status = OrdemProducao.Status.CANCELADA
        op.observacao = f'{op.observacao}\n[CANCELADA]: {motivo}'.strip()
        op.save(update_fields=['status', 'observacao', 'updated_at'])
        return op

    # ----------------------------------------------------------------------
    # Sugestão automática de OP (baseada em média de vendas)
    # ----------------------------------------------------------------------

    @classmethod
    def sugerir_op(cls, produto, filial) -> Decimal | None:
        """
        Sugere quantidade a produzir quando estoque < mínimo.
        Baseado na média de vendas dos últimos 30 dias.
        Retorna None se sugestão não é aplicável.
        """
        try:
            estoque = Estoque.objects.get(produto=produto, filial=filial)
        except Estoque.DoesNotExist:
            return None

        if estoque.quantidade_disponivel >= produto.estoque_minimo:
            return None

        # Média de vendas dos últimos 30 dias (placeholder — integrar com vendas futuramente)
        trinta_dias_atras = timezone.now() - timedelta(days=30)
        saidas = MovimentacaoEstoque.objects.filter(
            produto=produto,
            filial=filial,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            data_movimentacao__gte=trinta_dias_atras,
        ).aggregate_sum = 0

        # Placeholder simplificado — quantidade = estoque_maximo - disponivel
        qtd_sugerida = produto.estoque_maximo - estoque.quantidade_disponivel
        if qtd_sugerida <= 0:
            return produto.estoque_minimo  # pelo menos o mínimo
        return qtd_sugerida
