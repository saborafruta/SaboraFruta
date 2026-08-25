"""Serviço de análises de qualidade — bloqueia/aprova lote conforme resultado."""
from django.db import transaction
from apps.qualidade.models import AnaliseQualidade
from apps.qualidade.constants.enums import ResultadoAnalise, AcaoReprovacao
from apps.estoque.models import LoteProduto


class AnaliseQualidadeService:

    @staticmethod
    @transaction.atomic
    def registrar(filial, lote, tipo_analise, parametros, responsavel,
                   resultado=ResultadoAnalise.PENDENTE,
                   acao_reprovacao="", observacao=""):
        from django.utils import timezone
        analise = AnaliseQualidade.objects.create(
            filial=filial, lote=lote,
            tipo_analise=tipo_analise, parametros=parametros,
            resultado=resultado,
            responsavel_tecnico=responsavel,
            data_analise=timezone.now(),
            acao_reprovacao=acao_reprovacao,
            observacao=observacao,
        )
        AnaliseQualidadeService._aplicar_resultado(analise)
        return analise

    @staticmethod
    @transaction.atomic
    def aprovar(analise: AnaliseQualidade):
        analise.resultado = ResultadoAnalise.APROVADO
        analise.save(update_fields=["resultado"])
        AnaliseQualidadeService._aplicar_resultado(analise)
        return analise

    @staticmethod
    @transaction.atomic
    def reprovar(analise: AnaliseQualidade,
                 acao=AcaoReprovacao.BLOQUEIO, motivo=""):
        analise.resultado = ResultadoAnalise.REPROVADO
        analise.acao_reprovacao = acao
        analise.observacao = motivo
        analise.save(update_fields=["resultado", "acao_reprovacao", "observacao"])
        AnaliseQualidadeService._aplicar_resultado(analise)
        return analise

    @staticmethod
    def _aplicar_resultado(analise: AnaliseQualidade):
        """
        Atualiza o status do lote conforme o resultado da análise.

        USA `LoteProduto.Status`, e não um enum próprio. Este método apontava
        para `apps.estoque.constants.enums.StatusLote`, que nunca existiu: o
        módulo inteiro estourava no import, então nem aprovar nem reprovar
        jamais rodaram. Não havia `StatusLote.APROVADO` para restaurar --
        lote liberado é ATIVO, que é o estado que o FEFO enxerga.

        RESSALVA TAMBÉM LIBERA. É o caso em que a qualidade aceita um desvio
        conscientemente; deixar o lote bloqueado transformaria a ressalva numa
        reprovação com outro nome.
        """
        if not analise.lote:
            return
        lote = analise.lote
        if analise.resultado in (
            ResultadoAnalise.APROVADO, ResultadoAnalise.APROVADO_COM_RESSALVA,
        ):
            lote.status = LoteProduto.Status.ATIVO
            lote.motivo_bloqueio = ""
        elif analise.resultado == ResultadoAnalise.REPROVADO:
            lote.status = LoteProduto.Status.BLOQUEADO
            lote.motivo_bloqueio = (
                f"Reprovado em análise #{analise.id}. "
                f"Ação: {analise.acao_reprovacao}. {analise.observacao}"
            ).strip()
        else:
            return
        lote.save(update_fields=["status", "motivo_bloqueio", "updated_at"])
