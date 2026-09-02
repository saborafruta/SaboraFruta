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

    LIMITE_RECORRENCIAS = 365

    @staticmethod
    def _ajustar_data_vencimento(
        data_vencimento: date,
        filial,
        ajustar_vencimento_dia_util: bool,
        antecipar_vencimento_dia_util: bool,
    ) -> date:
        if not ajustar_vencimento_dia_util:
            return data_vencimento
        return (
            dia_util_anterior_ou_mesmo(data_vencimento, filial)
            if antecipar_vencimento_dia_util
            else proximo_dia_util(data_vencimento, filial)
        )

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
        dias_semana_recorrencia: str = '',
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
        data_vencimento = ContaPagarService._ajustar_data_vencimento(
            data_vencimento,
            filial,
            ajustar_vencimento_dia_util,
            antecipar_vencimento_dia_util,
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
            dias_semana_recorrencia=dias_semana_recorrencia,
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
        dias_semana: list[str] | tuple[str, ...] | None = None,
        data_competencia: date | None = None,
        regra_vencimento_mensal: str = ContaPagar.RegraVencimentoMensal.DATA_INFORMADA,
        dia_vencimento_mensal: int | None = None,
        **dados,
    ) -> list[ContaPagar]:
        if quantidade < 2 or quantidade > ContaPagarService.LIMITE_RECORRENCIAS:
            raise DomainError(
                f'A recorrência deve gerar entre 2 e {ContaPagarService.LIMITE_RECORRENCIAS} títulos.'
            )
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
        dias = sorted({
            int(dia) for dia in (dias_semana or [])
            if str(dia).isdigit() and 0 <= int(dia) <= 6
        })
        vencimentos = ContaPagarService._gerar_vencimentos_recorrencia(
            quantidade=quantidade,
            frequencia=frequencia,
            data_vencimento=data_vencimento,
            intervalo_dias=intervalo_dias,
            dias_semana=dias,
            filial=dados['filial'],
            ajustar_vencimento_dia_util=dados.get('ajustar_vencimento_dia_util', False),
            antecipar_vencimento_dia_util=dados.get('antecipar_vencimento_dia_util', False),
        )
        for indice, (vencimento_original, vencimento_base) in enumerate(vencimentos):
            deslocamento_competencia = (
                relativedelta(days=(vencimento_original - data_vencimento).days)
                if frequencia == ContaPagar.FrequenciaRecorrencia.SEMANAL and dias
                else incremento * indice
            )
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
                data_competencia=(
                    data_competencia + deslocamento_competencia
                    if data_competencia else None
                ),
                parcela=indice + 1,
                total_parcelas=quantidade,
                grupo_recorrencia=grupo,
                frequencia_recorrencia=frequencia,
                intervalo_recorrencia_dias=(
                    intervalo_dias
                    if frequencia == ContaPagar.FrequenciaRecorrencia.PERSONALIZADA
                    else None
                ),
                dias_semana_recorrencia=(
                    ','.join(str(dia) for dia in dias)
                    if frequencia == ContaPagar.FrequenciaRecorrencia.SEMANAL and dias
                    else ''
                ),
                regra_vencimento_mensal=regra_vencimento_mensal,
                dia_vencimento_mensal=dia_vencimento_mensal,
            ))
        return contas

    @staticmethod
    def _gerar_vencimentos_recorrencia(
        *, quantidade: int, frequencia: str, data_vencimento: date,
        filial, intervalo_dias: int | None = None,
        dias_semana: list[int] | tuple[int, ...] | None = None,
        ajustar_vencimento_dia_util: bool = False,
        antecipar_vencimento_dia_util: bool = False,
    ) -> list[tuple[date, date]]:
        """Retorna datas de origem e finais sem repetir vencimentos ajustados."""
        incrementos = {
            ContaPagar.FrequenciaRecorrencia.DIARIA: relativedelta(days=1),
            ContaPagar.FrequenciaRecorrencia.SEMANAL: relativedelta(weeks=1),
            ContaPagar.FrequenciaRecorrencia.MENSAL: relativedelta(months=1),
            ContaPagar.FrequenciaRecorrencia.TRIMESTRAL: relativedelta(months=3),
            ContaPagar.FrequenciaRecorrencia.SEMESTRAL: relativedelta(months=6),
            ContaPagar.FrequenciaRecorrencia.ANUAL: relativedelta(years=1),
            ContaPagar.FrequenciaRecorrencia.PERSONALIZADA: relativedelta(
                days=intervalo_dias or 1
            ),
        }
        incremento = incrementos[frequencia]
        dias = set(dias_semana or [])
        resultados = []
        vencimentos_usados = set()
        cursor = data_vencimento
        indice = 0
        tentativas = 0
        limite_tentativas = max(quantidade * 400, 1000)
        while len(resultados) < quantidade and tentativas < limite_tentativas:
            if frequencia == ContaPagar.FrequenciaRecorrencia.SEMANAL and dias:
                candidato = cursor
                cursor += timedelta(days=1)
                if candidato.weekday() not in dias:
                    tentativas += 1
                    continue
            else:
                candidato = data_vencimento + (incremento * indice)
                indice += 1
            ajustado = ContaPagarService._ajustar_data_vencimento(
                candidato,
                filial,
                ajustar_vencimento_dia_util,
                antecipar_vencimento_dia_util,
            )
            if ajustado not in vencimentos_usados:
                resultados.append((candidato, ajustado))
                vencimentos_usados.add(ajustado)
            tentativas += 1
        if len(resultados) != quantidade:
            raise DomainError('Não foi possível gerar vencimentos únicos para esta recorrência.')
        return resultados

    @staticmethod
    @transaction.atomic
    def reprogramar_recorrencia(
        *, conta: ContaPagar, quantidade: int, frequencia: str,
        data_vencimento: date, data_competencia: date | None = None,
        intervalo_dias: int | None = None,
        dias_semana: list[str] | tuple[str, ...] | None = None,
        regra_vencimento_mensal: str = ContaPagar.RegraVencimentoMensal.DATA_INFORMADA,
        dia_vencimento_mensal: int | None = None,
        usuario=None,
    ) -> list[ContaPagar]:
        """Recria, de forma recuperável, a série a partir do título editado."""
        if quantidade < 2 or quantidade > ContaPagarService.LIMITE_RECORRENCIAS:
            raise DomainError(
                f'A recorrência deve ter entre 2 e {ContaPagarService.LIMITE_RECORRENCIAS} ocorrências.'
            )
        if conta.valor_pago > 0 or conta.status == StatusContaPagar.PAGO:
            raise DomainError('Não é possível reprogramar a partir de um título que já possui baixa.')
        if conta.valor_juros or conta.valor_multa or conta.valor_desconto:
            raise DomainError('Remova juros, multa ou desconto antes de reprogramar a recorrência.')

        grupo = conta.grupo_recorrencia or uuid.uuid4()
        serie = list(
            ContaPagar.all_objects.select_for_update()
            .filter(filial=conta.filial, grupo_recorrencia=grupo, excluido_em__isnull=True)
            .order_by('parcela', 'data_vencimento', 'pk')
        ) if conta.grupo_recorrencia else [
            ContaPagar.all_objects.select_for_update().get(pk=conta.pk)
        ]
        inicio = next((indice for indice, item in enumerate(serie) if item.pk == conta.pk), 0)
        anteriores = serie[:inicio]
        alvos = serie[inicio:]
        if any(item.valor_pago > 0 or item.status == StatusContaPagar.PAGO for item in alvos):
            raise DomainError('Há títulos com baixa nesta parte da série. Reprograme a partir do próximo título em aberto.')
        if any(item.valor_juros or item.valor_multa or item.valor_desconto for item in alvos):
            raise DomainError('Há títulos com ajustes de valor nesta parte da série. Corrija-os antes de reprogramar.')

        dias = sorted({
            int(dia) for dia in (dias_semana or [])
            if str(dia).isdigit() and 0 <= int(dia) <= 6
        })
        vencimentos = ContaPagarService._gerar_vencimentos_recorrencia(
            quantidade=quantidade,
            frequencia=frequencia,
            data_vencimento=data_vencimento,
            intervalo_dias=intervalo_dias,
            dias_semana=dias,
            filial=conta.filial,
            ajustar_vencimento_dia_util=conta.ajustar_vencimento_dia_util,
            antecipar_vencimento_dia_util=conta.antecipar_vencimento_dia_util,
        )
        incrementos_competencia = {
            ContaPagar.FrequenciaRecorrencia.DIARIA: relativedelta(days=1),
            ContaPagar.FrequenciaRecorrencia.SEMANAL: relativedelta(weeks=1),
            ContaPagar.FrequenciaRecorrencia.MENSAL: relativedelta(months=1),
            ContaPagar.FrequenciaRecorrencia.TRIMESTRAL: relativedelta(months=3),
            ContaPagar.FrequenciaRecorrencia.SEMESTRAL: relativedelta(months=6),
            ContaPagar.FrequenciaRecorrencia.ANUAL: relativedelta(years=1),
            ContaPagar.FrequenciaRecorrencia.PERSONALIZADA: relativedelta(
                days=intervalo_dias or 1
            ),
        }
        total = len(anteriores) + quantidade
        atualizadas = []
        campos_copiados = (
            'fornecedor', 'funcionario', 'tipo_lancamento', 'descricao_despesa',
            'documento_tipo', 'documento_id', 'documento_numero',
            'nota_fiscal_fornecedor', 'chave_acesso_nfe',
            'forma_pagamento_prevista', 'plano_contas', 'conta_contabil',
            'observacao', 'ajustar_vencimento_dia_util',
            'antecipar_vencimento_dia_util',
        )
        for indice, (vencimento_original, vencimento_final) in enumerate(vencimentos):
            if indice < len(alvos):
                item = alvos[indice]
                for campo in campos_copiados:
                    setattr(item, campo, getattr(conta, campo))
                item.valor_original = conta.valor_original
                item.valor_final = conta.valor_original
                item.valor_pago = Decimal('0')
                item.valor_saldo = conta.valor_original
            else:
                item = ContaPagar(
                    filial=conta.filial,
                    data_emissao=conta.data_emissao,
                    valor_original=conta.valor_original,
                    valor_final=conta.valor_original,
                    valor_pago=Decimal('0'),
                    valor_saldo=conta.valor_original,
                    usuario=usuario or conta.usuario,
                )
                for campo in campos_copiados:
                    setattr(item, campo, getattr(conta, campo))

            if regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.PRIMEIRO_DIA:
                vencimento_final = vencimento_final.replace(day=1)
            elif regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.ULTIMO_DIA:
                vencimento_final = vencimento_final.replace(
                    day=monthrange(vencimento_final.year, vencimento_final.month)[1]
                )
            elif regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.DIA_FIXO:
                if not dia_vencimento_mensal:
                    raise DomainError('Informe o dia fixo do vencimento mensal.')
                vencimento_final = vencimento_final.replace(
                    day=min(dia_vencimento_mensal, monthrange(vencimento_final.year, vencimento_final.month)[1])
                )
            elif regra_vencimento_mensal == ContaPagar.RegraVencimentoMensal.QUINTO_DIA_UTIL:
                inicio_mes = vencimento_final.replace(day=1) - timedelta(days=1)
                vencimento_final = adicionar_dias_uteis_bancarios(inicio_mes, 5, conta.filial)
            vencimento_final = ContaPagarService._ajustar_data_vencimento(
                vencimento_final,
                conta.filial,
                conta.ajustar_vencimento_dia_util,
                conta.antecipar_vencimento_dia_util,
            )
            item.data_vencimento = vencimento_final
            item.data_competencia = (
                data_competencia + (
                    relativedelta(days=(vencimento_original - data_vencimento).days)
                    if frequencia == ContaPagar.FrequenciaRecorrencia.SEMANAL and dias
                    else incrementos_competencia[frequencia] * indice
                )
                if data_competencia else None
            )
            item.parcela = len(anteriores) + indice + 1
            item.total_parcelas = total
            item.grupo_recorrencia = grupo
            item.frequencia_recorrencia = frequencia
            item.intervalo_recorrencia_dias = (
                intervalo_dias if frequencia == ContaPagar.FrequenciaRecorrencia.PERSONALIZADA else None
            )
            item.dias_semana_recorrencia = (
                ','.join(str(dia) for dia in dias)
                if frequencia == ContaPagar.FrequenciaRecorrencia.SEMANAL else ''
            )
            item.regra_vencimento_mensal = regra_vencimento_mensal
            item.dia_vencimento_mensal = dia_vencimento_mensal
            item.status = (
                StatusContaPagar.VENCIDO
                if item.data_vencimento < timezone.localdate()
                else StatusContaPagar.ABERTO
            )
            item.save()
            atualizadas.append(item)

        for item in alvos[quantidade:]:
            item.excluido_em = timezone.now()
            item.excluido_por = usuario
            item.motivo_exclusao = 'Removido ao reprogramar a recorrência.'
            item.save(update_fields=['excluido_em', 'excluido_por', 'motivo_exclusao', 'updated_at'])
        for item in anteriores:
            item.total_parcelas = total
            item.save(update_fields=['total_parcelas', 'updated_at'])
        return atualizadas

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
        tarifa_bancaria: Decimal | None = None,
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
        if tarifa_bancaria is not None and tarifa_bancaria < Decimal('0'):
            raise DomainError('A tarifa bancária não pode ser negativa.')

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
            conta.status = StatusContaPagar.PAGO_PARCIAL

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
            tarifa_bancaria=(
                tarifa_bancaria
                if tarifa_bancaria is not None
                else (forma_pagamento.tarifa_pagamento_fixa or Decimal('0'))
            ),
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
        from apps.financeiro.services.taxas_transacao_service import sincronizar_tarifa_pagamento
        sincronizar_tarifa_pagamento(pagamento)
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
        elif conta.valor_pago > Decimal('0'):
            conta.status = StatusContaPagar.PAGO_PARCIAL
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
