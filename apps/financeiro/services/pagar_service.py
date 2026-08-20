"""Serviços de negócio para Contas a Pagar."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid

from django.db import transaction
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from apps.core.services.calendario import proximo_dia_util
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.models.receber_pagar import ContaPagar, PagamentoContaPagar


class ContaPagarService:

    @staticmethod
    @transaction.atomic
    def criar(
        filial,
        valor_original: Decimal,
        data_emissao: date,
        data_vencimento: date,
        fornecedor=None,
        funcionario=None,
        tipo_lancamento=ContaPagar.TipoLancamento.FORNECEDOR,
        parcela: int = 1,
        total_parcelas: int = 1,
        documento_numero: str = '',
        nota_fiscal_fornecedor: str = '',
        forma_pagamento_prevista=None,
        plano_contas=None,
        data_competencia: date | None = None,
        observacao: str = '',
        usuario=None,
        grupo_recorrencia=None,
        frequencia_recorrencia: str = '',
        ajustar_vencimento_dia_util: bool = False,
    ) -> ContaPagar:
        """Cria um lançamento manual de conta a pagar."""
        conta_contabil = plano_contas.conta_contabil if plano_contas else None
        if plano_contas and not conta_contabil:
            raise DomainError('A categoria financeira não possui conta contábil vinculada.')
        if tipo_lancamento == ContaPagar.TipoLancamento.FUNCIONARIO and not funcionario:
            raise DomainError('Selecione o funcionario deste pagamento.')
        if tipo_lancamento != ContaPagar.TipoLancamento.FORNECEDOR:
            fornecedor = None
        if tipo_lancamento == ContaPagar.TipoLancamento.FORNECEDOR:
            funcionario = None
        if ajustar_vencimento_dia_util:
            data_vencimento = proximo_dia_util(data_vencimento, filial)
        conta = ContaPagar(
            filial=filial,
            fornecedor=fornecedor,
            funcionario=funcionario,
            tipo_lancamento=tipo_lancamento,
            documento_numero=documento_numero,
            nota_fiscal_fornecedor=nota_fiscal_fornecedor,
            parcela=parcela,
            total_parcelas=total_parcelas,
            grupo_recorrencia=grupo_recorrencia,
            frequencia_recorrencia=frequencia_recorrencia,
            valor_original=valor_original,
            valor_juros=Decimal('0'),
            valor_multa=Decimal('0'),
            valor_desconto=Decimal('0'),
            valor_final=valor_original,
            valor_pago=Decimal('0'),
            valor_saldo=valor_original,
            data_emissao=data_emissao,
            data_vencimento=data_vencimento,
            data_competencia=data_competencia,
            ajustar_vencimento_dia_util=ajustar_vencimento_dia_util,
            forma_pagamento_prevista=forma_pagamento_prevista,
            plano_contas=plano_contas,
            conta_contabil=conta_contabil,
            observacao=observacao,
            status=StatusContaPagar.ABERTO,
            usuario=usuario,
        )
        conta.save()
        return conta

    @staticmethod
    @transaction.atomic
    def criar_e_quitar(
        *, data_pagamento: date, forma_pagamento_utilizada,
        conta_bancaria_pagamento=None, comprovante_url_pagamento: str = '',
        usuario=None, **dados,
    ) -> ContaPagar:
        """Cria um título único e registra sua quitação integral na mesma transação."""
        conta = ContaPagarService.criar(usuario=usuario, **dados)
        return ContaPagarService.registrar_pagamento(
            conta=conta,
            data_pagamento=data_pagamento,
            valor_pago=conta.valor_saldo,
            forma_pagamento=forma_pagamento_utilizada,
            conta_bancaria=conta_bancaria_pagamento,
            comprovante_url=comprovante_url_pagamento,
            observacao='Quitado no lançamento do título.',
            usuario=usuario,
        )

    @staticmethod
    @transaction.atomic
    def criar_recorrencia(
        *, quantidade: int, frequencia: str, data_vencimento: date,
        data_competencia: date | None = None, **dados,
    ) -> list[ContaPagar]:
        if quantidade < 2 or quantidade > 60:
            raise DomainError('A recorrência deve gerar entre 2 e 60 títulos.')
        incrementos = {
            ContaPagar.FrequenciaRecorrencia.SEMANAL: relativedelta(weeks=1),
            ContaPagar.FrequenciaRecorrencia.MENSAL: relativedelta(months=1),
            ContaPagar.FrequenciaRecorrencia.TRIMESTRAL: relativedelta(months=3),
            ContaPagar.FrequenciaRecorrencia.SEMESTRAL: relativedelta(months=6),
            ContaPagar.FrequenciaRecorrencia.ANUAL: relativedelta(years=1),
        }
        incremento = incrementos.get(frequencia)
        if not incremento:
            raise DomainError('Periodicidade de recorrência inválida.')

        grupo = uuid.uuid4()
        contas = []
        for indice in range(quantidade):
            deslocamento = incremento * indice
            contas.append(ContaPagarService.criar(
                **dados,
                data_vencimento=data_vencimento + deslocamento,
                data_competencia=(data_competencia + deslocamento) if data_competencia else None,
                parcela=indice + 1,
                total_parcelas=quantidade,
                grupo_recorrencia=grupo,
                frequencia_recorrencia=frequencia,
            ))
        return contas

    @staticmethod
    @transaction.atomic
    def registrar_pagamento(
        conta: ContaPagar,
        data_pagamento: date,
        valor_pago: Decimal,
        forma_pagamento,
        usuario,
        conta_bancaria=None,
        valor_juros: Decimal = Decimal('0'),
        valor_multa: Decimal = Decimal('0'),
        valor_desconto: Decimal = Decimal('0'),
        comprovante_url: str = '',
        observacao: str = '',
    ) -> ContaPagar:
        """Registra o pagamento (total ou parcial) de uma conta a pagar."""
        if conta.status == StatusContaPagar.CANCELADO:
            raise DomainError('Não é possível pagar uma conta cancelada.')
        if conta.status == StatusContaPagar.PAGO:
            raise DomainError('Esta conta já foi integralmente paga.')
        if not forma_pagamento:
            raise DomainError('Informe a forma utilizada no pagamento.')
        if data_pagamento < conta.data_emissao:
            raise DomainError('A data do pagamento não pode ser anterior à emissão.')
        if valor_pago <= Decimal('0'):
            raise DomainError('O valor pago deve ser maior que zero.')

        conta.valor_juros += valor_juros or Decimal('0')
        conta.valor_multa += valor_multa or Decimal('0')
        conta.valor_desconto += valor_desconto or Decimal('0')

        conta.valor_final = (
            conta.valor_original
            + conta.valor_juros
            + conta.valor_multa
            - conta.valor_desconto
        )
        if conta.valor_final < Decimal('0'):
            conta.valor_final = Decimal('0')

        conta.valor_pago += valor_pago
        conta.valor_saldo = conta.valor_final - conta.valor_pago

        if conta.valor_saldo <= Decimal('0'):
            conta.valor_saldo = Decimal('0')
            conta.status = StatusContaPagar.PAGO
        else:
            conta.status = StatusContaPagar.ABERTO

        conta.data_pagamento = data_pagamento
        conta.forma_pagamento = forma_pagamento
        conta.conta_bancaria = conta_bancaria
        if comprovante_url:
            conta.comprovante_url = comprovante_url
        conta.usuario_pagamento = usuario

        if observacao:
            sufixo = f'[Pgto {data_pagamento:%d/%m/%Y}] {observacao}'
            conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo

        conta.save()
        PagamentoContaPagar.objects.create(
            filial=conta.filial,
            conta_pagar=conta,
            data_pagamento=data_pagamento,
            valor_pago=valor_pago,
            valor_juros=valor_juros or Decimal('0'),
            valor_multa=valor_multa or Decimal('0'),
            valor_desconto=valor_desconto or Decimal('0'),
            forma_pagamento=forma_pagamento,
            conta_bancaria=conta_bancaria,
            comprovante_url=comprovante_url,
            observacao=observacao,
            usuario=usuario,
        )
        return conta

    @staticmethod
    @transaction.atomic
    def cancelar(conta: ContaPagar, motivo: str, usuario) -> ContaPagar:
        """Cancela uma conta a pagar ainda não paga."""
        if conta.status == StatusContaPagar.PAGO:
            raise DomainError('Não é possível cancelar uma conta já paga.')
        if conta.status == StatusContaPagar.CANCELADO:
            raise DomainError('Esta conta já está cancelada.')

        conta.status = StatusContaPagar.CANCELADO
        sufixo = f'[Cancelado por {usuario} em {timezone.localdate():%d/%m/%Y}] {motivo}'
        conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo
        conta.save()
        return conta

    @staticmethod
    def atualizar_status_vencidos(filial) -> int:
        """Marca como VENCIDO contas com data_vencimento < hoje e status ABERTO."""
        hoje = timezone.localdate()
        return (
            ContaPagar.objects
            .for_filial(filial)
            .filter(status=StatusContaPagar.ABERTO, data_vencimento__lt=hoje)
            .update(status=StatusContaPagar.VENCIDO)
        )
