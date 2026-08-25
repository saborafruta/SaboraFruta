"""
As telas do processo: a fila do chão de fábrica e o apontamento da etapa.

A FILA É PARA QUEM ESTÁ NA LINHA. Ela mostra o que falta apontar, agrupado
por ordem, e o apontamento é feito ali mesmo — mandar a pessoa abrir a OP,
rolar até o processo e voltar é o atrito que faz o apontamento virar papel
na prancheta para digitar no fim do turno (e o fim do turno nunca tem o
número certo).
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.models import Usuario
from apps.core.services.exceptions import DomainError

from .models import ApontamentoEtapa, OrdemPolpa, Recurso
from .services import ProcessoService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _numero(valor):
    """Texto vindo do formulário vira número — vazio continua vazio."""
    valor = (valor or '').strip().replace(',', '.')
    if not valor:
        return None
    try:
        return Decimal(valor)
    except (InvalidOperation, ValueError):
        return None


class FilaEtapasView(PolpaBaseView):
    """O que falta apontar, ordem a ordem."""

    area = 'producao'
    # A tela do menu "Apontamento" é esta mesma, mostrando tudo — inclusive
    # o que já foi apontado, para conferir e corrigir.
    mostrar_todas = False

    def get(self, request):
        filtros = {
            'etapa': (request.GET.get('etapa') or '').strip(),
            'situacao': (request.GET.get('situacao') or '').strip(),
            'todas': self.mostrar_todas or request.GET.get('todas') == '1',
        }
        etapas = list(ProcessoService.fila(_filial(request), filtros)[:300])

        # AGRUPADO POR ORDEM: quem está na linha pega a próxima etapa da SUA
        # ordem, não a mais antiga do sistema.
        grupos: dict = {}
        for etapa in etapas:
            grupos.setdefault(etapa.ordem, []).append(etapa)

        return render(request, 'polpa/processo_fila.html', {
            'title': 'Etapas do processo',
            'grupos': [
                {'op': op, 'etapas': linhas} for op, linhas in grupos.items()
            ],
            'filtros': filtros,
            'etapas_disponiveis': ProcessoService.etapas_disponiveis(),
            'situacoes': ApontamentoEtapa.Situacao.choices,
            'recursos': Recurso.objects.for_filial(_filial(request)).filter(ativo=True),
            'operadores': Usuario.objects.filter(
                empresa=_filial(request).empresa, ativo=True,
            ),
            'pode_agir': request.user.tem_permissao('polpa_producao', 'editar'),
        })


class ApontamentoView(FilaEtapasView):
    """O mesmo quadro, mostrando também o que já foi apontado."""

    mostrar_todas = True


class ApontarEtapaView(PolpaBaseView):
    """Grava o que aconteceu numa etapa."""

    area = 'producao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        etapa = get_object_or_404(
            ApontamentoEtapa.objects.for_filial(_filial(request))
            .select_related('ordem', 'ordem__ordem'),
            pk=pk,
        )
        volta = redirect(request.POST.get('voltar') or reverse('polpa:processo-fila'))

        dados = {
            'quantidade_entrada': _numero(request.POST.get('quantidade_entrada')),
            'quantidade_saida': _numero(request.POST.get('quantidade_saida')),
            'volume_entrada': _numero(request.POST.get('volume_entrada')),
            'volume_saida': _numero(request.POST.get('volume_saida')),
            'temperatura': _numero(request.POST.get('temperatura')),
            'motivo_perda': (request.POST.get('motivo_perda') or '').strip()[:160],
            'observacao': (request.POST.get('observacao') or '').strip(),
            'situacao': (request.POST.get('situacao') or '').strip(),
        }
        # CAMPO VAZIO NÃO APAGA O QUE JÁ ESTAVA. Quem aponta a saída depois
        # da entrada manda o formulário com um dos dois em branco, e limpar
        # o outro apagaria a medição feita meia hora antes.
        dados = {k: v for k, v in dados.items() if v not in (None, '')}

        if request.POST.get('equipamento'):
            dados['equipamento'] = get_object_or_404(
                Recurso.objects.for_filial(_filial(request)),
                pk=request.POST['equipamento'],
            )
        if request.POST.get('operador'):
            dados['operador'] = get_object_or_404(
                Usuario.objects.filter(empresa=_filial(request).empresa),
                pk=request.POST['operador'],
            )

        try:
            ProcessoService.apontar(etapa, dados, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        if etapa.overrun is not None:
            # O OVERRUN VOLTA NA MENSAGEM porque é o número que quem opera a
            # batedeira ajusta na hora: fora da faixa, corrige na próxima
            # batida — não no relatório do mês.
            messages.success(
                request,
                f'{etapa.get_etapa_display()} apontada — overrun de '
                f'{etapa.overrun}%.',
            )
            return volta

        perda = etapa.perda
        if perda:
            messages.success(
                request,
                f'{etapa.get_etapa_display()} apontada — perda de {perda} '
                f'({etapa.perda_percentual}%).',
            )
        else:
            messages.success(request, f'{etapa.get_etapa_display()} apontada.')
        return volta


class ProcessoDaOrdemView(PolpaBaseView):
    """O processo de uma ordem: as etapas dela e onde a fruta se perde."""

    area = 'producao'

    def get(self, request, pk):
        op = get_object_or_404(
            OrdemPolpa.objects.for_filial(_filial(request))
            .select_related('ordem', 'ordem__produto_acabado', 'receita'),
            pk=pk,
        )
        # Idempotente: uma ordem aberta antes desta tela existir ganha as
        # etapas ao ser aberta aqui, sem duplicar o que já foi apontado.
        ProcessoService.preparar(op)

        return render(request, 'polpa/processo_ordem.html', {
            'title': f'Processo — {op.numero}',
            'op': op,
            'resumo': ProcessoService.resumo(op),
            # PREVISTO x REALIZADO do consumo: é a diferença que explica o
            # custo do lote ter estourado, e ela não aparece em lugar nenhum
            # se as duas colunas não ficarem lado a lado.
            'consumo': ProcessoService.consumo(op),
            'recursos': Recurso.objects.for_filial(_filial(request)).filter(ativo=True),
            'operadores': Usuario.objects.filter(
                empresa=_filial(request).empresa, ativo=True,
            ),
            'situacoes': ApontamentoEtapa.Situacao.choices,
            'pode_agir': request.user.tem_permissao('polpa_producao', 'editar'),
        })
