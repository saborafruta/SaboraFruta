"""
Registrar uma análise: abrir o checklist, preencher e fechar o laudo.

A METADE QUE NÃO TINHA ROTA. O app de qualidade tinha telas para CADASTRAR
parâmetros por produto e etapa — e nenhuma para registrar uma conferência.
Quem media Brix na balança não tinha onde escrever.

A TELA NÃO DECIDE O RESULTADO. Ela coleta valores e ações; quem julga é o
`ChecklistService`, comparando com a faixa do cadastro. Deixar a tela mandar
"aprovado" junto com os números repetiria o defeito que o serviço veio
corrigir: um laudo com Brix fora da faixa gravado como aprovado sem nada
reclamar.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import requer_permissao
from apps.estoque.models import LoteProduto
from apps.produtos.models import Produto
from apps.qualidade.constants.enums import ResultadoAnalise, TipoAnalise
from apps.qualidade.models import AnaliseQualidade
from apps.qualidade.services.checklist_service import ChecklistService


def _analise(request, pk) -> AnaliseQualidade:
    return get_object_or_404(
        AnaliseQualidade.objects.filter(filial=request.filial_ativa)
        .select_related('lote', 'lote__produto', 'responsavel_tecnico'),
        pk=pk,
    )


@requer_permissao('qualidade', 'ver')
def checklist_list(request):
    """As análises em andamento e as fechadas recentemente."""
    analises = (
        AnaliseQualidade.objects.filter(filial=request.filial_ativa)
        .select_related('lote', 'lote__produto', 'responsavel_tecnico')
        .prefetch_related('itens')
    )
    abertas = [a for a in analises if a.resultado == ResultadoAnalise.PENDENTE]
    fechadas = [a for a in analises if a.resultado != ResultadoAnalise.PENDENTE][:30]

    return render(request, 'qualidade/checklist_list.html', {
        'title': 'Checklists de qualidade',
        'abertas': abertas,
        'fechadas': fechadas,
        'etapas': TipoAnalise.choices,
        'pode_registrar': request.user.tem_permissao('qualidade', 'criar'),
    })


@require_POST
@requer_permissao('qualidade', 'criar')
def checklist_abrir(request):
    """
    Abre a análise de um produto numa etapa, com o checklist inteiro.

    O LOTE É OPCIONAL de propósito: no recebimento a fruta é conferida ANTES
    de virar lote — é a conferência que decide se ela entra. Exigir o lote
    aqui obrigaria a criar o lote para poder reprová-lo.
    """
    produto = get_object_or_404(
        Produto.objects.for_filial(request.filial_ativa),
        pk=request.POST.get('produto'),
    )
    etapa = request.POST.get('etapa')
    if etapa not in TipoAnalise.values:
        messages.error(request, 'Escolha a etapa da análise.')
        return redirect('qualidade:checklist_list')

    lote = None
    if request.POST.get('lote'):
        lote = LoteProduto.objects.filter(
            pk=request.POST['lote'], filial=request.filial_ativa,
        ).first()

    try:
        analise = ChecklistService.abrir(
            request.filial_ativa, produto, etapa, request.user, lote=lote,
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect('qualidade:checklist_list')

    return redirect('qualidade:checklist_detail', pk=analise.pk)


@requer_permissao('qualidade', 'ver')
def checklist_detail(request, pk):
    analise = _analise(request, pk)
    return render(request, 'qualidade/checklist_detail.html', {
        'title': f'Análise #{analise.pk}',
        'analise': analise,
        'itens': analise.itens.select_related('acao_responsavel').all(),
        'resumo': ChecklistService.resumo(analise),
        'situacoes': [
            (v, r) for v, r in analise.itens.model.Situacao.choices
        ],
        'pode_registrar': request.user.tem_permissao('qualidade', 'criar'),
        'encerrada': analise.resultado != ResultadoAnalise.PENDENTE,
    })


@require_POST
@requer_permissao('qualidade', 'criar')
def checklist_salvar(request, pk):
    """
    Grava o que foi conferido. Não fecha o laudo — fechar é outro botão.

    SEPARADO DE PROPÓSITO: a conferência acontece ao longo do turno, e um
    formulário que só grava ao fechar perderia tudo se alguém saísse da tela.
    """
    analise = _analise(request, pk)
    if analise.resultado != ResultadoAnalise.PENDENTE:
        messages.error(request, 'Esta análise já foi encerrada.')
        return redirect('qualidade:checklist_detail', pk=analise.pk)

    respostas = {}
    for item in analise.itens.all():
        respostas[item.pk] = {
            'valor': request.POST.get(f'valor_{item.pk}', ''),
            'situacao': request.POST.get(f'situacao_{item.pk}', ''),
            'observacao': request.POST.get(f'obs_{item.pk}', ''),
            'acao_corretiva': request.POST.get(f'acao_{item.pk}', ''),
        }
    ChecklistService.preencher(analise, respostas, usuario=request.user)
    messages.success(request, 'Conferência gravada.')
    return redirect('qualidade:checklist_detail', pk=analise.pk)


@require_POST
@requer_permissao('qualidade', 'aprovar')
def checklist_concluir(request, pk):
    """
    Fecha o laudo. Exige `aprovar`, e não `criar`.

    Medir é uma coisa; assinar que o lote pode sair é outra — e é a assinatura
    que libera ou bloqueia material. O sistema de permissões já separa quem
    executa de quem aprova.
    """
    analise = _analise(request, pk)
    if analise.resultado != ResultadoAnalise.PENDENTE:
        messages.error(request, 'Esta análise já foi encerrada.')
        return redirect('qualidade:checklist_detail', pk=analise.pk)

    try:
        analise = ChecklistService.concluir(
            analise, request.user,
            acao_reprovacao=request.POST.get('acao_reprovacao', ''),
            observacao=request.POST.get('observacao', ''),
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect('qualidade:checklist_detail', pk=analise.pk)

    if analise.resultado == ResultadoAnalise.REPROVADO:
        messages.warning(
            request,
            'Laudo REPROVADO. O lote foi bloqueado com o motivo dos itens '
            'não conformes.',
        )
    else:
        messages.success(request, 'Laudo aprovado e lote liberado.')
    return redirect('qualidade:checklist_detail', pk=analise.pk)
