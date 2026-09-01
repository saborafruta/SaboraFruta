"""Serviços de negócio para Contas a Receber."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.core.services.calendario import adicionar_dias_uteis_bancarios
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaReceber
from apps.financeiro.services.entrega_receber import entrega_receber_habilitada, validar_entrega_receber
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo


class ContaReceberService:

    CAMPOS_REFERENCIA = (
        'documento_numero', 'status_entrega', 'data_entrega_prevista',
        'previsao_entrega_complemento',
    )
    CAMPOS_EDICAO = (
        'cliente', 'documento_numero', 'status_entrega',
        'data_entrega_prevista', 'previsao_entrega_complemento',
        'parcela', 'total_parcelas', 'valor_original', 'data_emissao',
        'data_vencimento', 'competencia', 'forma_pagamento',
        'plano_contas', 'observacao',
    )

    @staticmethod
    def _validar_referencia(conta):
        try:
            for nome in ContaReceberService.CAMPOS_REFERENCIA:
                campo = ContaReceber._meta.get_field(nome)
                setattr(conta, nome, campo.clean(getattr(conta, nome), conta))
        except ValidationError as exc:
            raise DomainError(' '.join(exc.messages)) from exc
        validar_entrega_receber(conta.status_entrega, conta.data_entrega_prevista,
                               conta.previsao_entrega_complemento)

    @staticmethod
    @transaction.atomic
    def editar_referencia(*, conta, dados, usuario):
        conta = ContaReceber.objects.select_for_update().get(pk=conta.pk)
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível editar uma conta cancelada.')
        campos = ['documento_numero']
        if entrega_receber_habilitada(conta.filial):
            campos = list(ContaReceberService.CAMPOS_REFERENCIA)
        antes = snapshot_modelo(conta, campos=campos)
        for nome in campos:
            if nome in dados:
                setattr(conta, nome, dados[nome])
        ContaReceberService._validar_referencia(conta)
        conta.save(update_fields=[*campos, 'updated_at'])
        registrar_auditoria(
            usuario=usuario, filial=conta.filial, modulo='financeiro', acao='editar',
            objeto=conta, descricao='Documento e entrega da conta a receber',
            antes=antes, depois=snapshot_modelo(conta, campos=campos),
        )
        return conta

    @staticmethod
    @transaction.atomic
    def editar(*, conta, dados, usuario):
        """Atualiza o título e recalcula os valores derivados com segurança."""
        conta = (
            ContaReceber.objects.select_for_update()
            .select_related('filial__empresa')
            .get(pk=conta.pk)
        )
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível editar uma conta cancelada.')

        campos = list(ContaReceberService.CAMPOS_EDICAO)
        campos_auditoria = [
            *campos, 'conta_contabil', 'valor_final', 'valor_saldo', 'status',
        ]
        antes = snapshot_modelo(conta, campos=campos_auditoria)

        tem_recebimentos = conta.pagamentos.exists()
        for nome in campos:
            if nome not in dados:
                continue
            if nome == 'forma_pagamento' and tem_recebimentos:
                continue
            setattr(conta, nome, dados[nome])

        if conta.cliente.filial_id != conta.filial_id:
            raise DomainError('O cliente não pertence à filial desta conta.')
        if conta.plano_contas_id:
            if conta.plano_contas.empresa_id != conta.filial.empresa_id:
                raise DomainError('A categoria financeira não pertence à empresa desta conta.')
            if not conta.plano_contas.conta_contabil_id:
                raise DomainError('A categoria financeira não possui conta contábil vinculada.')
            conta.conta_contabil = conta.plano_contas.conta_contabil
        else:
            conta.conta_contabil = None

        ContaReceberService._validar_referencia(conta)
        if conta.data_vencimento < conta.data_emissao:
            raise DomainError('Vencimento não pode ser anterior à emissão.')
        if conta.parcela > conta.total_parcelas:
            raise DomainError('Parcela não pode ser maior que o total de parcelas.')

        conta.valor_final = max(
            conta.valor_original + conta.valor_juros + conta.valor_multa - conta.valor_desconto,
            Decimal('0'),
        )
        if conta.valor_final < conta.valor_pago:
            raise DomainError(
                'O novo valor não pode deixar o total do título menor que o valor já recebido.'
            )
        conta.valor_saldo = conta.valor_final - conta.valor_pago
        if conta.valor_saldo <= Decimal('0'):
            conta.valor_saldo = Decimal('0')
            conta.status = StatusContaReceber.PAGO
        elif conta.valor_pago > Decimal('0'):
            conta.status = StatusContaReceber.PAGO_PARCIAL
        elif conta.status not in (StatusContaReceber.NEGOCIADO, StatusContaReceber.DEVOLVIDO):
            conta.status = (
                StatusContaReceber.VENCIDO
                if conta.data_vencimento < timezone.localdate()
                else StatusContaReceber.ABERTO
            )

        conta.save(update_fields=[
            *[nome for nome in campos if nome in dados and not (
                nome == 'forma_pagamento' and tem_recebimentos
            )],
            'conta_contabil', 'valor_final', 'valor_saldo', 'status', 'updated_at',
        ])
        registrar_auditoria(
            usuario=usuario, filial=conta.filial, modulo='financeiro', acao='editar',
            objeto=conta, descricao='Edição completa da conta a receber',
            antes=antes, depois=snapshot_modelo(conta, campos=campos_auditoria),
        )
        return conta

    @staticmethod
    @transaction.atomic
    def criar(
        filial,
        cliente,
        valor_original: Decimal,
        data_emissao: date,
        data_vencimento: date,
        parcela: int = 1,
        total_parcelas: int = 1,
        documento_numero: str = '',
        forma_pagamento=None,
        plano_contas=None,
        observacao: str = '',
        usuario=None,
        status_entrega: str = ContaReceber.StatusEntrega.SEM_PREVISAO,
        data_entrega_prevista: date | None = None,
        previsao_entrega_complemento: str = '',
    ) -> ContaReceber:
        """Cria um lançamento manual de conta a receber."""
        conta_contabil = plano_contas.conta_contabil if plano_contas else None
        if plano_contas and not conta_contabil:
            raise DomainError('A categoria financeira não possui conta contábil vinculada.')
        if (status_entrega != ContaReceber.StatusEntrega.SEM_PREVISAO
                or data_entrega_prevista or previsao_entrega_complemento):
            if not entrega_receber_habilitada(filial):
                raise DomainError('O acompanhamento de entrega está desativado nesta filial.')
        conta = ContaReceber(
            filial=filial,
            cliente=cliente,
            documento_numero=documento_numero,
            status_entrega=status_entrega,
            data_entrega_prevista=data_entrega_prevista,
            previsao_entrega_complemento=previsao_entrega_complemento,
            parcela=parcela,
            total_parcelas=total_parcelas,
            valor_original=valor_original,
            valor_juros=Decimal('0'),
            valor_multa=Decimal('0'),
            valor_desconto=Decimal('0'),
            valor_final=valor_original,
            valor_pago=Decimal('0'),
            valor_saldo=valor_original,
            data_emissao=data_emissao,
            data_vencimento=data_vencimento,
            forma_pagamento=forma_pagamento,
            plano_contas=plano_contas,
            conta_contabil=conta_contabil,
            observacao=observacao,
            status=StatusContaReceber.ABERTO,
            usuario=usuario,
        )
        ContaReceberService._validar_referencia(conta)
        conta.save()
        return conta

    @staticmethod
    @transaction.atomic
    def registrar_baixa(
        conta: ContaReceber,
        data_pagamento: date,
        valor_pago: Decimal,
        forma_pagamento,
        usuario,
        conta_bancaria=None,
        valor_juros: Decimal = Decimal('0'),
        valor_multa: Decimal = Decimal('0'),
        valor_desconto: Decimal = Decimal('0'),
        observacao: str = '',
        bandeira: str = '',
        numero_parcelas: int | None = None,
    ) -> ContaReceber:
        """Registra o recebimento (total ou parcial) de uma conta a receber."""
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível baixar uma conta cancelada.')
        if conta.status == StatusContaReceber.PAGO:
            raise DomainError('Esta conta já foi integralmente recebida.')

        # Acumula acréscimos/descontos desta baixa
        conta.valor_juros += valor_juros or Decimal('0')
        conta.valor_multa += valor_multa or Decimal('0')
        conta.valor_desconto += valor_desconto or Decimal('0')

        # Recalcula valor final
        conta.valor_final = (
            conta.valor_original
            + conta.valor_juros
            + conta.valor_multa
            - conta.valor_desconto
        )
        if conta.valor_final < Decimal('0'):
            conta.valor_final = Decimal('0')

        # Acumula valor pago e recalcula saldo
        conta.valor_pago += valor_pago
        conta.valor_saldo = conta.valor_final - conta.valor_pago

        bandeira = forma_pagamento.normalizar_bandeira(bandeira)
        parcelas_operacao = numero_parcelas or 1
        calculo = forma_pagamento.calcular_taxa_recebimento(
            conta.valor_pago,
            parcelas_operacao,
            bandeira,
        )
        calculo_baixa = forma_pagamento.calcular_taxa_recebimento(
            valor_pago,
            parcelas_operacao,
            bandeira,
        )
        conta.bandeira_recebimento = bandeira
        conta.parcelas_recebimento = numero_parcelas or None
        conta.taxa_percentual_aplicada = calculo['percentual']
        conta.taxa_fixa_aplicada = calculo['fixa']
        conta.taxa_calculada_em = timezone.now()
        conta.valor_taxa_recebimento = calculo['taxa']
        conta.valor_liquido_recebido = calculo['liquido']

        if conta.valor_saldo <= Decimal('0'):
            conta.valor_saldo = Decimal('0')
            conta.status = StatusContaReceber.PAGO
        else:
            conta.status = StatusContaReceber.PAGO_PARCIAL

        conta.data_pagamento = data_pagamento
        conta.forma_pagamento = forma_pagamento
        conta.prazo_compensacao_aplicado = forma_pagamento.prazo_compensacao_dias_uteis or 0
        conta.data_liquidacao_prevista = adicionar_dias_uteis_bancarios(
            data_pagamento,
            conta.prazo_compensacao_aplicado,
            conta.filial,
        )
        conta_bancaria = conta_bancaria or forma_pagamento.conta_bancaria_padrao
        if conta_bancaria:
            conta.conta_bancaria = conta_bancaria
        conta.usuario_baixa = usuario

        if observacao:
            sufixo = f'[Baixa {data_pagamento:%d/%m/%Y}] {observacao}'
            conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo

        conta.save()
        PagamentoContaReceber.objects.create(
            filial=conta.filial,
            conta_receber=conta,
            data_pagamento=data_pagamento,
            valor_pago=valor_pago,
            valor_juros=valor_juros or Decimal('0'),
            valor_multa=valor_multa or Decimal('0'),
            valor_desconto=valor_desconto or Decimal('0'),
            valor_taxa=calculo_baixa['taxa'],
            valor_liquido=calculo_baixa['liquido'],
            forma_pagamento=forma_pagamento,
            conta_bancaria=conta_bancaria,
            bandeira=bandeira,
            numero_parcelas=numero_parcelas or None,
            observacao=observacao,
            usuario=usuario,
        )
        return ContaReceberService._recalcular_resumo(conta)

    @staticmethod
    def _recalcular_resumo(conta: ContaReceber) -> ContaReceber:
        pagamentos = list(
            conta.pagamentos.select_related('forma_pagamento', 'conta_bancaria')
            .order_by('data_pagamento', 'created_at', 'pk')
        )
        total_pago = sum((p.valor_pago or Decimal('0') for p in pagamentos), Decimal('0'))
        total_juros = sum((p.valor_juros or Decimal('0') for p in pagamentos), Decimal('0'))
        total_multa = sum((p.valor_multa or Decimal('0') for p in pagamentos), Decimal('0'))
        total_desconto = sum((p.valor_desconto or Decimal('0') for p in pagamentos), Decimal('0'))
        total_taxa = sum((p.valor_taxa or Decimal('0') for p in pagamentos), Decimal('0'))
        total_liquido = sum((p.valor_liquido or Decimal('0') for p in pagamentos), Decimal('0'))

        conta.valor_juros = total_juros
        conta.valor_multa = total_multa
        conta.valor_desconto = total_desconto
        conta.valor_final = max(
            conta.valor_original + total_juros + total_multa - total_desconto,
            Decimal('0'),
        )
        conta.valor_pago = total_pago
        conta.valor_saldo = max(conta.valor_final - total_pago, Decimal('0'))
        conta.valor_taxa_recebimento = total_taxa
        conta.valor_liquido_recebido = total_liquido

        ultimo = pagamentos[-1] if pagamentos else None
        if ultimo:
            conta.data_pagamento = ultimo.data_pagamento
            conta.forma_pagamento = ultimo.forma_pagamento
            conta.conta_bancaria = ultimo.conta_bancaria
            conta.bandeira_recebimento = ultimo.bandeira
            conta.parcelas_recebimento = ultimo.numero_parcelas
            conta.taxa_percentual_aplicada = (
                ultimo.forma_pagamento.calcular_taxa_recebimento(
                    ultimo.valor_pago,
                    ultimo.numero_parcelas or 1,
                    ultimo.bandeira,
                )['percentual']
                if ultimo.forma_pagamento_id else Decimal('0')
            )
            conta.taxa_fixa_aplicada = (
                ultimo.forma_pagamento.calcular_taxa_recebimento(
                    ultimo.valor_pago,
                    ultimo.numero_parcelas or 1,
                    ultimo.bandeira,
                )['fixa']
                if ultimo.forma_pagamento_id else Decimal('0')
            )
            conta.taxa_calculada_em = timezone.now() if total_taxa else None
            prazo = ultimo.forma_pagamento.prazo_compensacao_dias_uteis if ultimo.forma_pagamento_id else 0
            conta.prazo_compensacao_aplicado = prazo or 0
            conta.data_liquidacao_prevista = adicionar_dias_uteis_bancarios(
                ultimo.data_pagamento,
                conta.prazo_compensacao_aplicado,
                conta.filial,
            )
            conta.status = (
                StatusContaReceber.PAGO
                if conta.valor_saldo <= Decimal('0')
                else StatusContaReceber.PAGO_PARCIAL
            )
        else:
            conta.data_pagamento = None
            conta.valor_taxa_recebimento = Decimal('0')
            conta.valor_liquido_recebido = Decimal('0')
            conta.taxa_percentual_aplicada = Decimal('0')
            conta.taxa_fixa_aplicada = Decimal('0')
            conta.taxa_calculada_em = None
            conta.bandeira_recebimento = ''
            conta.parcelas_recebimento = None
            conta.prazo_compensacao_aplicado = 0
            conta.data_liquidacao_prevista = None
            if conta.status != StatusContaReceber.NEGOCIADO:
                conta.status = (
                    StatusContaReceber.VENCIDO
                    if conta.data_vencimento < timezone.localdate()
                    else StatusContaReceber.ABERTO
                )

        conta.save()
        return conta

    @staticmethod
    @transaction.atomic
    def editar_baixa(
        pagamento: PagamentoContaReceber,
        data_pagamento: date,
        valor_pago: Decimal,
        forma_pagamento,
        usuario,
        conta_bancaria=None,
        valor_juros: Decimal = Decimal('0'),
        valor_multa: Decimal = Decimal('0'),
        valor_desconto: Decimal = Decimal('0'),
        observacao: str = '',
        bandeira: str = '',
        numero_parcelas: int | None = None,
    ) -> ContaReceber:
        """Atualiza uma baixa individual e refaz o resumo do título."""
        conta = pagamento.conta_receber
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível alterar recebimento de uma conta cancelada.')

        bandeira = forma_pagamento.normalizar_bandeira(bandeira)
        parcelas_operacao = numero_parcelas or 1
        calculo = forma_pagamento.calcular_taxa_recebimento(
            valor_pago,
            parcelas_operacao,
            bandeira,
        )
        pagamento.data_pagamento = data_pagamento
        pagamento.valor_pago = valor_pago
        pagamento.valor_juros = valor_juros or Decimal('0')
        pagamento.valor_multa = valor_multa or Decimal('0')
        pagamento.valor_desconto = valor_desconto or Decimal('0')
        pagamento.valor_taxa = calculo['taxa']
        pagamento.valor_liquido = calculo['liquido']
        pagamento.forma_pagamento = forma_pagamento
        pagamento.conta_bancaria = conta_bancaria or forma_pagamento.conta_bancaria_padrao
        pagamento.bandeira = bandeira
        pagamento.numero_parcelas = numero_parcelas or None
        pagamento.observacao = observacao
        pagamento.usuario = usuario
        pagamento.save()
        return ContaReceberService._recalcular_resumo(conta)

    @staticmethod
    @transaction.atomic
    def excluir_baixa(
        pagamento: PagamentoContaReceber,
        motivo: str,
        usuario,
    ) -> ContaReceber:
        """Remove uma baixa individual e refaz saldo, status e taxas."""
        conta = pagamento.conta_receber
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível excluir recebimento de uma conta cancelada.')
        resumo = (
            f'[Recebimento excluído por {usuario} em {timezone.localdate():%d/%m/%Y}] '
            f'{pagamento.data_pagamento:%d/%m/%Y} - R$ {pagamento.valor_pago}. {motivo}'
        )
        conta.observacao = f'{conta.observacao}\n{resumo}'.strip() if conta.observacao else resumo
        conta.save(update_fields=['observacao', 'updated_at'])
        pagamento.delete()
        return ContaReceberService._recalcular_resumo(conta)

    @staticmethod
    @transaction.atomic
    def cancelar(conta: ContaReceber, motivo: str, usuario) -> ContaReceber:
        """Cancela uma conta a receber ainda não paga."""
        if conta.status == StatusContaReceber.PAGO:
            raise DomainError('Não é possível cancelar uma conta já recebida.')
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Esta conta já está cancelada.')

        conta.status = StatusContaReceber.CANCELADO
        sufixo = f'[Cancelado por {usuario} em {timezone.localdate():%d/%m/%Y}] {motivo}'
        conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo
        conta.save()
        return conta

    @staticmethod
    @transaction.atomic
    def alterar_prazo(
        conta: ContaReceber,
        nova_data_vencimento: date,
        motivo: str,
        usuario,
    ) -> ContaReceber:
        """
        Renegocia o vencimento de uma conta ainda não paga. Vira NEGOCIADO
        (não ABERTO/VENCIDO) porque é isso que o campo de status já
        representa: um prazo que foi conversado/ajustado com o cliente.
        """
        if conta.status == StatusContaReceber.PAGO:
            raise DomainError('Não é possível alterar o prazo de uma conta já recebida.')
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível alterar o prazo de uma conta cancelada.')

        data_anterior = conta.data_vencimento
        conta.data_vencimento = nova_data_vencimento
        conta.status = StatusContaReceber.NEGOCIADO

        usuario_nome = getattr(usuario, 'nome', '') or str(usuario).split('@')[0]
        sufixo = (
            f'[Prazo alterado por {usuario_nome} em {timezone.localdate():%d/%m/%Y}] '
            f'{data_anterior:%d/%m/%Y} → {nova_data_vencimento:%d/%m/%Y}.'
        )
        if motivo:
            sufixo += f' {motivo}'
        conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo

        conta.save()
        return conta

    @staticmethod
    def atualizar_status_vencidos(filial) -> int:
        """Marca como VENCIDO contas com data_vencimento < hoje e status ABERTO."""
        hoje = timezone.localdate()
        atualizado = (
            ContaReceber.objects
            .for_filial(filial)
            .filter(status=StatusContaReceber.ABERTO, data_vencimento__lt=hoje)
            .update(status=StatusContaReceber.VENCIDO)
        )
        return atualizado
