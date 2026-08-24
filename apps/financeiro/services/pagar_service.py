"""Serviços de negócio para Contas a Pagar."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import uuid

from django.db import transaction
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from apps.core.services.calendario import (
    adicionar_dias_uteis_bancarios,
    dia_util_anterior_ou_mesmo,
    proximo_dia_util,
)
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
        descricao_despesa: str = '',
        fornecedor=None,
        funcionario=None,
        tipo_lancamento=ContaPagar.TipoLancamento.FORNECEDOR,
        parcela: int = 1,
        total_parcelas: int = 1,
        documento_numero: str = '',
        nota_fiscal_fornecedor: str = '',
        chave_acesso_nfe: str = '',
        forma_pagamento_prevista=None,
        plano_contas=None,
        data_competencia: date | None = None,
        observacao: str = '',
        usuario=None,
        grupo_recorrencia=None,
        frequencia_recorrencia: str = '',
        intervalo_recorrencia_dias: int | None = None,
        regra_vencimento_mensal: str = ContaPagar.RegraVencimentoMensal.DATA_INFORMADA,
        dia_vencimento_mensal: int | None = None,
        ajustar_vencimento_dia_util: bool = False,
        antecipar_vencimento_dia_util: bool = False,
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
            data_vencimento = (
                dia_util_anterior_ou_mesmo(data_vencimento, filial)
                if antecipar_vencimento_dia_util
                else proximo_dia_util(data_vencimento, filial)
            )
        conta = ContaPagar(
            filial=filial,
            fornecedor=fornecedor,
            funcionario=funcionario,
            tipo_lancamento=tipo_lancamento,
            descricao_despesa=(descricao_despesa or '').strip(),
            documento_numero=documento_numero,
            nota_fiscal_fornecedor=nota_fiscal_fornecedor,
            chave_acesso_nfe=chave_acesso_nfe,
            parcela=parcela,
            total_parcelas=total_parcelas,
            grupo_recorrencia=grupo_recorrencia,
            frequencia_recorrencia=frequencia_recorrencia,
            intervalo_recorrencia_dias=intervalo_recorrencia_dias,
            regra_vencimento_mensal=regra_vencimento_mensal,
            dia_vencimento_mensal=dia_vencimento_mensal,
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
            antecipar_vencimento_dia_util=antecipar_vencimento_dia_util,
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
        conta_bancaria_pagamento=None, comprovante_pagamento=None,
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
            comprovante=comprovante_pagamento,
            observacao='Quitado no lançamento do título.',
            usuario=usuario,
        )

    @staticmethod
    @transaction.atomic
    def criar_recorrencia(
        *, quantidade: int, frequencia: str, data_vencimento: date,
        intervalo_dias: int | None = None,
        data_competencia: date | None = None,
        regra_vencimento_mensal: str = ContaPagar.RegraVencimentoMensal.DATA_INFORMADA,
        dia_vencimento_mensal: int | None = None,
        **dados,
    ) -> list[ContaPagar]:
        if quantidade < 2 or quantidade > 60:
            raise DomainError('A recorrência deve gerar entre 2 e 60 títulos.')
        incrementos = {
            ContaPagar.FrequenciaRecorrencia.DIARIA: relativedelta(days=1),
            ContaPagar.FrequenciaRecorrencia.SEMANAL: relativedelta(weeks=1),
            ContaPagar.FrequenciaRecorrencia.MENSAL: relativedelta(months=1),
            ContaPagar.FrequenciaRecorrencia.TRIMESTRAL: relativedelta(months=3),
            ContaPagar.FrequenciaRecorrencia.SEMESTRAL: relativedelta(months=6),
            ContaPagar.FrequenciaRecorrencia.ANUAL: relativedelta(years=1),
        }
        if frequencia == ContaPagar.FrequenciaRecorrencia.PERSONALIZADA:
            if not intervalo_dias or not 1 <= intervalo_dias <= 365:
                raise DomainError('Informe um intervalo personalizado entre 1 e 365 dias.')
            incrementos[frequencia] = relativedelta(days=intervalo_dias)
        incremento = incrementos.get(frequencia)
        if not incremento:
            raise DomainError('Periodicidade de recorrência inválida.')

        grupo = uuid.uuid4()
        contas = []
        for indice in range(quantidade):
            deslocamento = incremento * indice
            vencimento_base = data_vencimento + deslocamento
            if regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.PRIMEIRO_DIA:
                vencimento_base = vencimento_base.replace(day=1)
            elif regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.ULTIMO_DIA:
                vencimento_base = vencimento_base.replace(
                    day=monthrange(vencimento_base.year, vencimento_base.month)[1]
                )
            elif regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.DIA_FIXO:
                if not dia_vencimento_mensal:
                    raise DomainError('Informe o dia fixo do vencimento mensal.')
                vencimento_base = vencimento_base.replace(
                    day=min(dia_vencimento_mensal, monthrange(vencimento_base.year, vencimento_base.month)[1])
                )
            elif regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.QUINTO_DIA_UTIL:
                inicio_mes = vencimento_base.replace(day=1) - timedelta(days=1)
                vencimento_base = adicionar_dias_uteis_bancarios(inicio_mes, 5, dados['filial'])
            contas.append(ContaPagarService.criar(
                **dados,
                data_vencimento=vencimento_base,
                data_competencia=(data_competencia + deslocamento) if data_competencia else None,
                parcela=indice + 1,
                total_parcelas=quantidade,
                grupo_recorrencia=grupo,
                frequencia_recorrencia=frequencia,
                intervalo_recorrencia_dias=(
                    intervalo_dias
                    if frequencia == ContaPagar.FrequenciaRecorrencia.PERSONALIZADA
                    else None
                ),
                regra_vencimento_mensal=regra_vencimento_mensal,
                dia_vencimento_mensal=dia_vencimento_mensal,
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
        comprovante=None,
        comprovante_url: str = '',
        referencia_pagamento: str = '',
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

        saldo_atualizado = (
            conta.valor_saldo
            + (valor_juros or Decimal('0'))
            + (valor_multa or Decimal('0'))
            - (valor_desconto or Decimal('0'))
        )
        if saldo_atualizado <= Decimal('0'):
            raise DomainError('O desconto não pode zerar ou ultrapassar o saldo do título.')
        if valor_pago > saldo_atualizado:
            raise DomainError('O valor pago não pode superar o saldo atualizado do título.')

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
        pagamento = PagamentoContaPagar(
            filial=conta.filial,
            conta_pagar=conta,
            data_pagamento=data_pagamento,
            valor_pago=valor_pago,
            valor_juros=valor_juros or Decimal('0'),
            valor_multa=valor_multa or Decimal('0'),
            valor_desconto=valor_desconto or Decimal('0'),
            forma_pagamento=forma_pagamento,
            conta_bancaria=conta_bancaria,
            referencia_pagamento=referencia_pagamento,
            comprovante_url=comprovante_url,
            comprovante_arquivo=comprovante,
            comprovante_nome_original=getattr(comprovante, 'name', '') if comprovante else '',
            observacao=observacao,
            usuario=usuario,
        )
        pagamento.save()
        return conta

    @staticmethod
    @transaction.atomic
    def corrigir_valor(conta: ContaPagar, novo_valor: Decimal, pagamento=None):
        """Corrige o valor do titulo e mantem baixa e saldo coerentes."""
        conta = ContaPagar.objects.select_for_update().get(pk=conta.pk)
        novo_valor = Decimal(novo_valor).quantize(Decimal('0.01'))
        if novo_valor <= Decimal('0'):
            raise DomainError('O novo valor deve ser maior que zero.')
        if conta.status == StatusContaPagar.CANCELADO:
            raise DomainError('Nao e possivel alterar uma conta cancelada.')

        diferenca = novo_valor - conta.valor_original
        pagamento_ajustado = None
        if conta.status == StatusContaPagar.PAGO:
            pagamentos = conta.pagamentos.select_for_update()
            if pagamento is not None:
                pagamento_ajustado = pagamentos.filter(pk=pagamento.pk).first()
            if pagamento_ajustado is None:
                pagamento_ajustado = pagamentos.order_by(
                    '-data_pagamento', '-created_at', '-pk'
                ).first()
            if not pagamento_ajustado:
                raise DomainError('A conta esta paga, mas nao possui baixa para ajustar.')
            novo_pagamento = pagamento_ajustado.valor_pago + diferenca
            if novo_pagamento <= Decimal('0'):
                raise DomainError('A correcao deixaria a ultima baixa com valor invalido.')
            pagamento_ajustado.valor_pago = novo_pagamento
            pagamento_ajustado.save(update_fields=['valor_pago', 'updated_at'])
            conta.valor_pago += diferenca

        conta.valor_original = novo_valor
        conta.valor_final = max(
            novo_valor + conta.valor_juros + conta.valor_multa - conta.valor_desconto,
            Decimal('0'),
        )
        if conta.valor_final < conta.valor_pago:
            raise DomainError('O novo valor nao pode ser menor que o total ja pago.')

        conta.valor_saldo = conta.valor_final - conta.valor_pago
        if conta.valor_saldo == Decimal('0'):
            conta.status = StatusContaPagar.PAGO
        elif conta.data_vencimento < timezone.localdate():
            conta.status = StatusContaPagar.VENCIDO
        else:
            conta.status = StatusContaPagar.ABERTO
        conta.save(update_fields=[
            'valor_original', 'valor_final', 'valor_pago', 'valor_saldo', 'status', 'updated_at',
        ])
        return conta, pagamento_ajustado

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
