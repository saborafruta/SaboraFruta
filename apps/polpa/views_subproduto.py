"""
Registrar o que saiu da batida além do produto.

FICA NA TELA DA ORDEM, e não numa tela própria de "resíduos". Quem sabe que
saíram 500 kg de casca é quem estava na linha, no dia, olhando aquela ordem —
mandá-lo procurar outro menu é como o apontamento deixa de ser feito.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import requer_permissao
from apps.polpa.models import OrdemPolpa, Subproduto
from apps.polpa.services.perdas import PerdasService
from apps.polpa.services.subproduto import SubprodutoService
from apps.polpa.views import PolpaBaseView


def _ordem(request, pk) -> OrdemPolpa:
    return get_object_or_404(
        OrdemPolpa.objects.for_filial(request.filial_ativa)
        .select_related('ordem', 'receita'),
        pk=pk,
    )


@require_POST
@requer_permissao('polpa_producao', 'editar')
def subproduto_registrar(request, pk):
    op = _ordem(request, pk)
    try:
        subproduto = SubprodutoService.registrar(op, request.POST, request.user)
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect('polpa:ordem-detail', pk=op.pk)

    messages.success(
        request,
        f'{subproduto.quantidade} {subproduto.unidade} de '
        f'{subproduto.rotulo} — {subproduto.get_destino_display()}.',
    )
    # O CRÉDITO NO ESTOQUE É NOTÍCIA SEPARADA. Ele muda saldo de um produto
    # que não é o da ordem, e enfiá-lo na mesma frase faria a pessoa achar que
    # a batida rendeu mais.
    if subproduto.creditado:
        messages.info(
            request,
            f'{subproduto.quantidade} {subproduto.unidade} de '
            f'{subproduto.produto_estoque} entraram no estoque.',
        )
    elif subproduto.pendente_de_credito:
        messages.warning(
            request,
            'O material não entrou no estoque — registre pela tela do produto '
            'ou tente de novo.',
        )
    return redirect('polpa:ordem-detail', pk=op.pk)


@require_POST
@requer_permissao('polpa_producao', 'excluir')
def subproduto_excluir(request, pk, subproduto_pk):
    """
    Apaga um registro. Só o que NÃO creditou estoque.

    Apagar um que já entrou deixaria o saldo com material sem origem — e o
    caminho certo para isso é um ajuste de estoque, que tem rastro próprio.
    """
    op = _ordem(request, pk)
    subproduto = get_object_or_404(
        Subproduto.objects.filter(ordem=op), pk=subproduto_pk,
    )
    if subproduto.creditado:
        messages.error(
            request,
            'Este subproduto já entrou no estoque. Para desfazer, use um '
            'ajuste de estoque — apagar aqui deixaria o saldo sem origem.',
        )
        return redirect('polpa:ordem-detail', pk=op.pk)

    subproduto.delete()
    messages.success(request, 'Registro removido.')
    return redirect('polpa:ordem-detail', pk=op.pk)


class PerdasView(PolpaBaseView):
    """
    O que saiu da fruta e nao virou produto — subproduto e perda, lado a lado.

    E' TELA DE LEITURA, e o registro continua na ordem. Quem sabe que sairam
    500 kg de casca e' quem estava na linha, no dia, olhando aquela ordem;
    mandar essa pessoa procurar outro menu e' como o apontamento deixa de ser
    feito. Aqui se CONSULTA o que ja' foi apontado.

    AS DUAS METADES FICAM SEPARADAS, e a separacao e' dinheiro: subproduto tem
    destino e pode ate' render caixa; perda sumiu e so' deixa custo. Somar os
    dois num numero so' juntaria bagaco vendido com polpa derramada.

    Junto numa tela porque a pergunta e' "onde foi parar a fruta que nao virou
    polpa", e ela nao se responde olhando metade: o rendimento que falta esta'
    repartido entre as duas.
    """

    area = 'producao'

    def get(self, request):
        filial = request.filial_ativa
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'tipo': (request.GET.get('tipo') or '').strip(),
            'destino': (request.GET.get('destino') or '').strip(),
        }
        subprodutos = list(PerdasService.subprodutos(filial, filtros)[:200])
        # Tipo e destino sao do subproduto; filtrar perda por eles devolveria
        # vazio sempre e daria a impressao de que nao ha' perda registrada.
        perdas = (
            []
            if filtros['tipo'] or filtros['destino']
            else list(PerdasService.perdas(filial, filtros)[:200])
        )
        return render(request, 'polpa/perdas.html', {
            'title': 'Perdas e refugo',
            'subprodutos': subprodutos,
            'perdas': perdas,
            'filtros': filtros,
            'tem_filtro': any(filtros.values()),
            'so_subproduto': bool(filtros['tipo'] or filtros['destino']),
            'tipos': Subproduto.Tipo.choices,
            'destinos': Subproduto.Destino.choices,
            'resumo': PerdasService.resumo(subprodutos, perdas),
        })
