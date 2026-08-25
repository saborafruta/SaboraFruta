"""
As telas da ordem de produção.

A FILA ABRE NO QUE ESTÁ ABERTO. Quem entra aqui quer saber o que está
rodando, o que parou e o que atrasou — a OP encerrada é consulta, e enchia
a primeira tela com o que já acabou.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .forms_ordem import OrdemPolpaForm
from .models import OrdemPolpa
from .services import CustoService, OrdemPolpaService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _ordem(request, pk) -> OrdemPolpa:
    return get_object_or_404(
        OrdemPolpa.objects.for_filial(_filial(request))
        .select_related(
            'ordem', 'ordem__produto_acabado', 'ordem__lote_gerado',
            'ordem__ficha_tecnica', 'receita', 'responsavel',
        ),
        pk=pk,
    )


class OrdemListView(PolpaBaseView):
    """A fila da produção."""

    area = 'producao'

    def get(self, request):
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'situacao': (request.GET.get('situacao') or '').strip(),
            # Sem filtro escolhido, mostra só o que está em aberto: a OP
            # encerrada é consulta, e enchia a tela com o que já acabou.
            'abertas': not request.GET.get('situacao') and request.GET.get('todas') != '1',
        }
        ordens = OrdemPolpaService.fila(_filial(request), filtros)

        return render(request, 'polpa/ordem_list.html', {
            'title': 'Ordens de produção',
            'ordens': ordens[:200],
            'filtros': filtros,
            'todas': request.GET.get('todas') == '1',
            'situacoes': OrdemPolpa.Situacao.choices,
            'painel': OrdemPolpaService.painel(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_producao', 'criar'),
        })


class OrdemFormView(PolpaBaseView):
    """Abre uma ordem a partir de uma receita ativa."""

    area = 'producao'
    permissao_acao = 'criar'

    def get(self, request):
        form = OrdemPolpaForm(filial=_filial(request))
        return self._tela(request, form)

    def post(self, request):
        form = OrdemPolpaForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            return self._tela(request, form)

        try:
            op = OrdemPolpaService.criar(
                _filial(request), form.cleaned_data['receita'],
                form.cleaned_data, request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return self._tela(request, form)

        messages.success(request, f'Ordem {op.numero} aberta, em planejamento.')
        return redirect(reverse('polpa:ordem-detail', args=[op.pk]))

    @staticmethod
    def _tela(request, form):
        return render(request, 'polpa/ordem_form.html', {
            'title': 'Nova ordem de produção',
            'form': form,
            # SEM RECEITA ATIVA não há o que produzir, e um select vazio não
            # diz por quê — a pessoa conclui que a tela está quebrada.
            'tem_receita': form.fields['receita'].queryset.exists(),
        })


class OrdemDetailView(PolpaBaseView):
    """A ordem inteira: necessidade, andamento e encerramento."""

    area = 'producao'

    def get(self, request, pk):
        op = _ordem(request, pk)
        necessidade = OrdemPolpaService.necessidade(op)

        return render(request, 'polpa/ordem_detail.html', {
            'title': op.numero,
            'op': op,
            'necessidade': necessidade,
            # OS DOIS GRUPOS COM O RÓTULO já montados: a tabela desenha os
            # dois num laço só, e a separação (quem separa a fruta não é
            # quem separa o pote) fica dita uma vez.
            'bloco_necessidade': [
                ('Ingredientes', necessidade['ingredientes']),
                ('Embalagem', necessidade['embalagens']),
            ],
            'validade_prevista': OrdemPolpaService.validade_do_lote(op),
            # CUSTO PREVISTO CONTRA REALIZADO. O `op.ordem.custo_total` que a
            # tela já mostrava soma fruta e pote na mesma linha e não tem com
            # o que ser comparado — um número sozinho não diz se foi caro.
            'custo': CustoService.comparar(op),
            'etapas': op.receita.etapas.all(),
            'proximos': op.proximos,
            'situacoes': OrdemPolpa.Situacao.choices,
            'pode_agir': request.user.tem_permissao('polpa_producao', 'editar'),
            'pode_encerrar': request.user.tem_permissao('polpa_producao', 'aprovar'),
        })


class MoverView(PolpaBaseView):
    """Muda a situação da ordem."""

    area = 'producao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        op = _ordem(request, pk)
        destino = (request.POST.get('destino') or '').strip()
        volta = redirect(reverse('polpa:ordem-detail', args=[pk]))

        try:
            if destino == OrdemPolpa.Situacao.LIBERADA:
                necessidade = OrdemPolpaService.liberar(op, request.user)
                if necessidade['faltas']:
                    # AVISA E LIBERA. A fruta chega durante o dia; travar
                    # aqui faria a fábrica registrar a OP depois de produzir,
                    # que é o mesmo que não registrar.
                    faltando = ', '.join(
                        f'{f["produto"]} ({f["falta"]} {f["unidade"]})'
                        for f in necessidade['faltas'][:5]
                    )
                    messages.warning(
                        request,
                        f'Ordem {op.numero} liberada. Falta em estoque: {faltando}.',
                    )
                else:
                    messages.success(
                        request,
                        f'Ordem {op.numero} liberada — todo o insumo está em estoque.',
                    )
            else:
                OrdemPolpaService.mover(
                    op, destino, request.user,
                    {'motivo': request.POST.get('motivo') or ''},
                )
                messages.success(
                    request, f'Ordem {op.numero}: {op.get_situacao_display()}.',
                )
        except DomainError as erro:
            messages.error(request, str(erro))
        return volta


class ConcluirView(PolpaBaseView):
    """Fecha a produção: consome insumo, cria o lote e dá a validade."""

    area = 'producao'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        op = _ordem(request, pk)
        volta = redirect(reverse('polpa:ordem-detail', args=[pk]))

        try:
            quantidade = Decimal(request.POST.get('quantidade') or '0')
            peso = request.POST.get('peso_saida') or ''
            peso_saida = Decimal(peso) if peso.strip() else None
        except (InvalidOperation, ValueError):
            messages.error(request, 'Quantidade inválida.')
            return volta

        try:
            OrdemPolpaService.concluir(
                op, request.user, quantidade, peso_saida,
                request.POST.get('numero_lote') or '',
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        op.refresh_from_db()
        lote = op.lote
        if lote and lote.data_validade:
            messages.success(
                request,
                f'Ordem {op.numero} produzida. Lote {lote.numero_lote} criado, '
                f'com validade até {lote.data_validade:%d/%m/%Y}.',
            )
        elif lote:
            # A AUSÊNCIA DA VALIDADE É DITA em voz alta: o lote existe, mas
            # sem vencimento — e é agora que dá para arrumar o cadastro,
            # não quando a etiqueta já foi impressa.
            messages.warning(
                request,
                f'Ordem {op.numero} produzida e lote {lote.numero_lote} criado, '
                'mas SEM validade: o produto não tem prazo cadastrado.',
            )
        else:
            messages.success(request, f'Ordem {op.numero} produzida.')
        return volta
