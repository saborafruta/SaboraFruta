"""Serviço de análises de qualidade — bloqueia/aprova lote conforme resultado."""
from django.db import transaction
from apps.qualidade.models import AnaliseQualidade
from apps.qualidade.constants.enums import ResultadoAnalise, AcaoReprovacao
from apps.estoque.models import LoteProduto


class PainelQualidadeService:
    """
    A fila da qualidade: o que falta analisar e o que ficou barrado.

    NAO E' UMA SEGUNDA CONSULTA sobre `AnaliseQualidade`. E' a mesma tabela,
    recortada -- e o recorte vive aqui, e nao na view, porque a mesma pergunta
    vai ser feita pelo painel do vertical mais tarde e duas consultas divergem
    no dia em que alguem acrescenta um resultado.

    O QUE ESTA TELA PROCURA e' o lote REPROVADO SEM ACAO. Reprovar e' metade da
    decisao: a outra metade e' dizer o que fazer com o material -- bloquear,
    descartar, reprocessar, devolver. Sem isso o lote fica parado sem dono, e
    ninguem sabe se pode mexer nele. E' a unica linha que a tela destaca.
    """

    @staticmethod
    def fila(filial, filtros: dict | None = None):
        from django.db.models import Q

        filtros = filtros or {}
        qs = (
            AnaliseQualidade.objects.for_filial(filial)
            .select_related(
                'lote', 'lote__produto', 'ordem_producao', 'responsavel_tecnico',
            )
            .prefetch_related('itens')
        )
        if filtros.get('resultado'):
            qs = qs.filter(resultado=filtros['resultado'])
        if filtros.get('tipo'):
            qs = qs.filter(tipo_analise=filtros['tipo'])
        if filtros.get('busca'):
            termo = filtros['busca']
            qs = qs.filter(
                Q(lote__numero_lote__icontains=termo)
                | Q(lote__produto__descricao__icontains=termo)
                | Q(ordem_producao__numero__icontains=termo)
                | Q(observacao__icontains=termo)
            )
        return qs

    @staticmethod
    def resumo(analises) -> dict:
        """
        Somado sobre o que a tela JA' CARREGOU, e nao numa consulta nova: com
        filtro aplicado, as duas dariam numeros diferentes para a mesma tela.
        """
        analises = list(analises)
        R = ResultadoAnalise
        reprovadas = [a for a in analises if a.resultado == R.REPROVADO]
        return {
            'total': len(analises),
            'pendentes': sum(1 for a in analises if a.resultado == R.PENDENTE),
            'aprovadas': sum(1 for a in analises if a.resultado == R.APROVADO),
            'ressalva': sum(
                1 for a in analises if a.resultado == R.APROVADO_COM_RESSALVA
            ),
            'reprovadas': len(reprovadas),
            # REPROVADA SEM ACAO e' o lote barrado que ninguem decidiu o que
            # fazer -- fica parado sem dono, e ninguem sabe se pode mexer.
            'sem_acao': sum(1 for a in reprovadas if not a.acao_reprovacao),
        }

    @staticmethod
    def linha(analise) -> dict:
        """Uma analise na tela, com o estado do checklist dela."""
        itens = list(analise.itens.all())
        nao_conformes = [i for i in itens if i.situacao == 'nao_conforme']
        return {
            'analise': analise,
            'itens': len(itens),
            'nao_conformes': len(nao_conformes),
            'pendentes': sum(1 for i in itens if i.situacao == 'pendente'),
            'sem_acao_corretiva': sum(
                1 for i in nao_conformes if not i.acao_corretiva.strip()
            ),
            'barrada_sem_decisao': (
                analise.resultado == ResultadoAnalise.REPROVADO
                and not analise.acao_reprovacao
            ),
        }


class NaoConformidadeService:
    """
    Os desvios registrados, com a acao tomada e quem tomou.

    O DESVIO SEM ACAO E' O MOTIVO DESTA TELA. `ItemAnalise` ja' guarda a acao
    corretiva com responsavel e data desde que foi criado, mas nada listava os
    que ficaram em branco -- e desvio anotado sem tratativa e' pior que desvio
    nao anotado: da' a impressao de que alguem cuidou.
    """

    @staticmethod
    def fila(filial, filtros: dict | None = None):
        from django.db.models import Q

        from apps.qualidade.models import ItemAnalise

        filtros = filtros or {}
        qs = (
            ItemAnalise.objects
            .filter(
                analise__filial=filial,
                situacao=ItemAnalise.Situacao.NAO_CONFORME,
            )
            .select_related(
                'analise', 'analise__lote', 'analise__lote__produto',
                'analise__ordem_producao', 'analise__responsavel_tecnico',
                'acao_responsavel',
            )
            .order_by('-analise__data_analise', 'ordem')
        )
        # SEM ACAO PRIMEIRO quando ninguem pediu outra coisa: e' a fila de
        # trabalho, e o que ja' foi tratado e' consulta.
        if filtros.get('situacao') == 'tratadas':
            qs = qs.exclude(acao_corretiva='')
        elif filtros.get('situacao') != 'todas':
            qs = qs.filter(acao_corretiva='')
        if filtros.get('busca'):
            termo = filtros['busca']
            qs = qs.filter(
                Q(nome_parametro__icontains=termo)
                | Q(analise__lote__numero_lote__icontains=termo)
                | Q(analise__lote__produto__descricao__icontains=termo)
                | Q(acao_corretiva__icontains=termo)
            )
        return qs

    @staticmethod
    def resumo(filial) -> dict:
        """
        Conta sobre TODOS os desvios da filial, e nao sobre o que o filtro
        deixou passar: o topo responde "quanto falta", e um total que muda com
        o filtro nao responde isso.
        """
        from apps.qualidade.models import ItemAnalise

        itens = list(
            ItemAnalise.objects.filter(
                analise__filial=filial,
                situacao=ItemAnalise.Situacao.NAO_CONFORME,
            )
        )
        sem_acao = [i for i in itens if not i.acao_corretiva.strip()]
        return {
            'total': len(itens),
            'sem_acao': len(sem_acao),
            'tratadas': len(itens) - len(sem_acao),
            'obrigatorios_sem_acao': sum(1 for i in sem_acao if i.obrigatorio),
        }


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
