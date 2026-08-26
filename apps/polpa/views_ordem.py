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
from apps.produtos.models import Produto

from .models import Camara, FichaProduto, OrdemPolpa, Subproduto
from .services import CustoService, OrdemPolpaService, SubprodutoService
from .views import PolpaBaseView


ZERO = Decimal('0')


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


class BatidasView(PolpaBaseView):
    """
    Cada batelada com sua formulacao, rendimento e lote de saida.

    NAO EXISTE REGISTRO POR BATIDA no sistema, e esta tela nao inventa um. O
    que existe e' a ORDEM: ela aponta a receita, guarda planejado e produzido,
    e gera um lote. Quantas batidas ela vale sai de dividir o planejado pelo
    que a receita rende por execucao -- a mesma conta que a tela de abrir ordem
    mostra ao vivo.

    Registrar batida a batida seria outra coisa: exigiria o operador apontar
    cada execucao separada, e so' faz sentido quando a fabrica precisa
    diferenciar a batida 2 da 3 -- rastrear um defeito que apareceu no meio da
    ordem, por exemplo. Nao e' o que existe hoje, e fingir que existe daria uma
    tela que mostra numeros que ninguem registrou.

    E' A MESMA `fila()` da lista de ordens, filtrada -- e nao uma consulta
    paralela: duas consultas da mesma coisa divergem no dia em que alguem
    acrescenta uma situacao.

    SO' O QUE JA' VIROU EXECUCAO. Ordem planejada e ordem liberada ainda nao
    sao batelada: nada foi misturado, nao ha' rendimento nem lote. Enche-las
    aqui faria a tela prometer producao que nao aconteceu.
    """

    area = 'producao'

    EXECUTADAS = (
        OrdemPolpa.Situacao.EM_PRODUCAO,
        OrdemPolpa.Situacao.PAUSADA,
        OrdemPolpa.Situacao.QUALIDADE,
        OrdemPolpa.Situacao.PRODUZIDA,
    )

    def get(self, request):
        filial = _filial(request)
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'situacao': (request.GET.get('situacao') or '').strip(),
        }
        ordens = OrdemPolpaService.fila(filial, filtros)
        if not filtros['situacao']:
            ordens = ordens.filter(situacao__in=self.EXECUTADAS)
        ordens = ordens.order_by('-ordem__data_inicio_prevista', '-id')[:200]

        linhas = [self._linha(op) for op in ordens]
        produzidas = [l for l in linhas if l['rendimento'] is not None]
        return render(request, 'polpa/batida_list.html', {
            'title': 'Batidas',
            'linhas': linhas,
            'filtros': filtros,
            'tem_filtro': bool(filtros['busca'] or filtros['situacao']),
            'situacoes': [
                (s.value, s.label)
                for s in OrdemPolpa.Situacao
                if s in self.EXECUTADAS
            ],
            'resumo': {
                'bateladas': len(linhas),
                'batidas': sum(l['batidas'] for l in linhas),
                'rendimento_medio': (
                    sum(l['rendimento'] for l in produzidas) / len(produzidas)
                    if produzidas else None
                ),
                'abaixo': sum(1 for l in produzidas if l['abaixo']),
            },
        })

    @staticmethod
    def _linha(op) -> dict:
        """
        Uma batelada na tela.

        `batidas` e' DERIVADO, e por isso vem com o divisor ao lado na tela:
        numero calculado sem mostrar de onde veio e' numero que ninguem
        confere. Receita sem `quantidade_produzida` devolve zero em vez de
        estourar -- e a tela mostra travessao, que e' honesto: nao da' para
        dividir por um rendimento que ninguem preencheu.
        """
        import math

        por_batida = op.receita.ficha.quantidade_produzida or ZERO
        planejada = op.quantidade_planejada or ZERO
        batidas = (
            math.ceil(planejada / por_batida) if por_batida > ZERO else 0
        )
        rendimento = op.rendimento_lote
        esperado = op.receita.rendimento_esperado
        return {
            'op': op,
            'por_batida': por_batida,
            'batidas': batidas,
            'rendimento': rendimento,
            # ABAIXO DO ESPERADO e' o que se procura numa tela de bateladas:
            # a batelada que rendeu menos do que a receita promete e' onde o
            # dinheiro sumiu, e ela some no meio das outras sem a marca.
            'abaixo': bool(
                rendimento is not None and esperado and rendimento < esperado
            ),
            'esperado': esperado,
        }


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

    GRUPOS = (
        ('O que produzir', '', ('receita', 'quantidade_planejada')),
        (
            'Quando e quem',
            'Datas e responsável podem ficar em branco — a ordem nasce assim '
            'e se ajusta quando a produção começa.',
            ('responsavel', 'data_inicio_prevista', 'data_fim_prevista'),
        ),
    )

    @staticmethod
    def _tela(request, form):
        import json

        receitas = form.fields['receita'].queryset
        agrupados = [
            nome
            for _titulo, _dica, campos in OrdemFormView.GRUPOS
            for nome in campos
        ]
        return render(request, 'polpa/ordem_form.html', {
            'title': 'Nova ordem de produção',
            'form': form,
            # SEM RECEITA ATIVA não há o que produzir, e um select vazio não
            # diz por quê — a pessoa conclui que a tela está quebrada.
            'tem_receita': receitas.exists(),
            'grupos': [
                (titulo, dica,
                 [form[nome] for nome in campos if nome in form.fields])
                for titulo, dica, campos in OrdemFormView.GRUPOS
            ],
            'campos_agrupados': agrupados,
            # QUANTAS BATIDAS a quantidade pedida vira. É o número que diz se a
            # ordem é razoável — "3 batidas" e "47 batidas" são conversas
            # diferentes, e hoje só se descobria depois de gravar. Sai do mesmo
            # `quantidade_produzida` da ficha que a produção usa para dividir a
            # ordem, então a tela e o serviço não discordam.
            'rende_por_batida': json.dumps({
                str(r.pk): str(r.ficha.quantidade_produzida or 0)
                for r in receitas
            }),
            'produto_da_receita': json.dumps({
                str(r.pk): str(r.ficha.produto_acabado) for r in receitas
            }),
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
            # AS CÂMARAS NO ENCERRAMENTO: é ali que se decide onde o lote
            # vai ficar, com o produto ainda na mão de quem produziu.
            'camaras': Camara.objects.for_filial(_filial(request)).filter(ativo=True),
            # CUSTO PREVISTO CONTRA REALIZADO. O `op.ordem.custo_total` que a
            # tela já mostrava soma fruta e pote na mesma linha e não tem com
            # o que ser comparado — um número sozinho não diz se foi caro.
            'custo': CustoService.comparar(op),
            # SUBPRODUTOS: o que saiu da batida além do produto. Fica aqui, e
            # não numa tela de "resíduos", porque quem sabe que saíram 500 kg
            # de casca é quem estava na linha olhando esta ordem.
            'subprodutos': SubprodutoService.resumo(op),
            'tipos_subproduto': Subproduto.Tipo.choices,
            'destinos_subproduto': Subproduto.Destino.choices,
            # Só o que NÃO é acabado: um subproduto que aponta para o próprio
            # produto da ordem creditaria a batida duas vezes.
            'produtos_subproduto': Produto.objects.for_filial(
                _filial(request),
            ).exclude(ficha_polpa__classe=FichaProduto.Classe.ACABADO)[:200],
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

        camara = None
        if request.POST.get('camara'):
            camara = get_object_or_404(
                Camara.objects.for_filial(_filial(request)),
                pk=request.POST['camara'],
            )

        try:
            OrdemPolpaService.concluir(
                op, request.user, quantidade, peso_saida,
                request.POST.get('numero_lote') or '',
                camara=camara,
                armazenagem={
                    'endereco': request.POST.get('endereco') or '',
                    'temperatura_entrada': (
                        Decimal(request.POST['temperatura_entrada'])
                        if (request.POST.get('temperatura_entrada') or '').strip()
                        else None
                    ),
                },
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
