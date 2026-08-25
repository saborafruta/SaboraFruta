"""
Registrar o que saiu da batida além do produto.

FICA NA TELA DA ORDEM, e não numa tela própria de "resíduos". Quem sabe que
saíram 500 kg de casca é quem estava na linha, no dia, olhando aquela ordem —
mandá-lo procurar outro menu é como o apontamento deixa de ser feito.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import requer_permissao
from apps.polpa.models import OrdemPolpa, Subproduto
from apps.polpa.services.subproduto import SubprodutoService


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
