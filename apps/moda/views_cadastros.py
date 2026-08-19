"""
Telas de cadastro do vertical Moda: Grades, Cores e Produtos.

São as três que fecham o ciclo até o SKU — grade define os tamanhos, cor
define as cores, e o produto cruza as duas para gerar as variantes.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import models, transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.exceptions import DadosInvalidosError

from apps.core.services.exceptions import DomainError

from .forms_cliente import ClienteRapidoForm
from .forms import (
    CorForm, GradeForm, ItemPedidoProducaoForm, PedidoProducaoForm,
    PersonalizacaoForm, PersonalizacaoIndividualForm, ProdutoModaForm,
    ValoresPedidoForm, VisualItemPedidoForm,
)
from .models import (
    Cor, Grade, ItemGrade, ItemGradePedido, ItemPedidoProducao, PedidoProducao,
    Personalizacao, PersonalizacaoIndividual, ProdutoCor, ProdutoModa,
    Tamanho, VisualItemPedido,
)
from .services.pedido_pdf import mensagem_whatsapp, whatsapp_numero
from .services.validacao import ValidacaoProducao
from .services import (
    FinanceiroPedidoService, GradePedidoService, IndividualService,
    VarianteService,
)
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _valores_js(pedido, itens) -> dict:
    """
    Números da seção de valores para o Alpine recalcular enquanto o usuário
    digita.

    Vai como float porque é só para a conta da tela: quem manda no valor
    gravado é o servidor, que refaz tudo em Decimal ao salvar. Assim o
    arredondamento do JavaScript nunca chega ao contas a receber.
    """
    return {
        'itens': [
            {'id': i.pk, 'qtd': i.quantidade, 'unit': float(i.valor_unitario or 0)}
            for i in itens
        ],
        'desconto': float(pedido.desconto or 0),
        'acrescimo': float(pedido.acrescimo or 0),
        'frete': float(pedido.frete or 0),
        'entrada': float(pedido.entrada or 0),
    }


# ══════════════════════════════════════════════════════════════════════
# GRADES
# ══════════════════════════════════════════════════════════════════════

class GradeListView(ModaBaseView):
    def get(self, request):
        grades = (
            Grade.objects.for_filial(_filial(request))
            .prefetch_related(Prefetch('itens', queryset=ItemGrade.objects.select_related('tamanho')))
            .annotate(qtd_produtos=Count('produtos', distinct=True))
        )
        return render(request, 'moda/grade_list.html', {
            'title': 'Grades de Tamanho',
            'grades': grades,
        })


class GradeFormView(ModaBaseView):
    permissao_acao = 'criar'

    def _obter(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(Grade.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk=None):
        grade = self._obter(request, pk)
        filial = _filial(request)
        return render(request, 'moda/grade_form.html', {
            'title': f'Grade {grade.nome}' if grade else 'Nova Grade',
            'grade': grade,
            'form': GradeForm(instance=grade, filial=filial),
            'tamanhos': Tamanho.objects.for_filial(filial).filter(ativo=True),
            'selecionados': (
                list(grade.itens.values_list('tamanho_id', flat=True)) if grade else []
            ),
        })

    def post(self, request, pk=None):
        grade = self._obter(request, pk)
        filial = _filial(request)
        form = GradeForm(request.POST, instance=grade, filial=filial)

        if not form.is_valid():
            return render(request, 'moda/grade_form.html', {
                'title': 'Nova Grade' if grade is None else f'Grade {grade.nome}',
                'grade': grade, 'form': form,
                'tamanhos': Tamanho.objects.for_filial(filial).filter(ativo=True),
                'selecionados': [int(i) for i in request.POST.getlist('tamanho')],
            })

        grade = form.save(commit=False)
        grade.filial = filial
        grade.save()

        # A ordem vem da ordem em que os checkboxes chegaram no POST, que é
        # a ordem da tela — é assim que o usuário monta PP, P, M, G.
        ids = [int(i) for i in request.POST.getlist('tamanho')]
        grade.itens.exclude(tamanho_id__in=ids).delete()
        for posicao, tamanho_id in enumerate(ids, start=1):
            ItemGrade.objects.update_or_create(
                grade=grade, tamanho_id=tamanho_id,
                defaults={'ordem': posicao * 10},
            )

        messages.success(request, f'Grade {grade.nome} salva com {len(ids)} tamanho(s).')
        return redirect(reverse('moda:grade-list'))


# ══════════════════════════════════════════════════════════════════════
# CORES
# ══════════════════════════════════════════════════════════════════════

class CorListView(ModaBaseView):
    def get(self, request):
        return render(request, 'moda/cor_list.html', {
            'title': 'Cores',
            'cores': Cor.objects.for_filial(_filial(request)),
        })


class CorFormView(ModaBaseView):
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        cor = get_object_or_404(Cor.objects.for_filial(_filial(request)), pk=pk) if pk else None
        return render(request, 'moda/cor_form.html', {
            'title': f'Cor {cor.nome}' if cor else 'Nova Cor',
            'cor': cor,
            'form': CorForm(instance=cor, filial=_filial(request)),
        })

    def post(self, request, pk=None):
        cor = get_object_or_404(Cor.objects.for_filial(_filial(request)), pk=pk) if pk else None
        form = CorForm(request.POST, instance=cor, filial=_filial(request))
        if not form.is_valid():
            return render(request, 'moda/cor_form.html', {
                'title': 'Nova Cor' if cor is None else f'Cor {cor.nome}',
                'cor': cor, 'form': form,
            })
        cor = form.save(commit=False)
        cor.filial = _filial(request)
        cor.save()
        messages.success(request, f'Cor {cor.nome} ({cor.sigla}) salva.')
        return redirect(reverse('moda:cor-list'))


# ══════════════════════════════════════════════════════════════════════
# PRODUTOS
# ══════════════════════════════════════════════════════════════════════

class ProdutoListView(ModaBaseView):
    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        produtos = (
            ProdutoModa.objects.for_filial(_filial(request))
            .select_related('categoria', 'colecao', 'tecido', 'grade', 'marca')
            .annotate(qtd_variantes=Count('variantes', distinct=True))
        )
        if busca:
            produtos = produtos.filter(
                Q(nome__icontains=busca) | Q(codigo__icontains=busca)
                | Q(referencia__icontains=busca)
            )
        return render(request, 'moda/produto_list.html', {
            'title': 'Produtos de Moda',
            'produtos': produtos,
            'busca': busca,
        })


class ProdutoFormView(ModaBaseView):
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk) if pk else None
        return render(request, 'moda/produto_form.html', {
            'title': f'{produto.codigo}' if produto else 'Novo Produto',
            'produto': produto,
            'form': ProdutoModaForm(instance=produto, filial=_filial(request)),
        })

    def post(self, request, pk=None):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk) if pk else None
        form = ProdutoModaForm(
            request.POST, request.FILES, instance=produto, filial=_filial(request),
        )
        if not form.is_valid():
            return render(request, 'moda/produto_form.html', {
                'title': 'Novo Produto' if produto is None else produto.codigo,
                'produto': produto, 'form': form,
            })
        produto = form.save(commit=False)
        produto.filial = _filial(request)
        produto.save()
        messages.success(request, f'Produto {produto.codigo} salvo.')
        return redirect(reverse('moda:produto-detail', args=[produto.pk]))


class ProdutoDetailView(ModaBaseView):
    def get(self, request, pk):
        produto = get_object_or_404(
            ProdutoModa.objects.for_filial(_filial(request))
            .select_related('categoria', 'colecao', 'linha', 'modelo', 'marca', 'tecido', 'grade'),
            pk=pk,
        )
        # Montada aqui, e não com 15 `{% if %}` no template: composição,
        # gramatura, gola e manga são properties derivadas de outros
        # cadastros, e a tela só precisa do par rótulo/valor.
        ficha = [
            ('Código', produto.codigo),
            ('Referência', produto.referencia),
            ('Status', produto.get_status_display()),
            ('Categoria', produto.categoria),
            ('Coleção', produto.colecao),
            ('Linha', produto.linha),
            ('Marca', produto.marca),
            ('Modelo', produto.modelo),
            ('Gola', produto.gola),
            ('Manga', produto.manga),
            ('Tecido', produto.tecido),
            ('Composição', produto.composicao),
            ('Gramatura', f'{produto.gramatura} g/m²' if produto.gramatura else ''),
            ('Grade', produto.grade),
        ]
        return render(request, 'moda/produto_detail.html', {
            'title': produto.codigo,
            'produto': produto,
            'ficha': ficha,
            'cores_produto': produto.cores.select_related('cor').all(),
            'variantes': produto.variantes.select_related('produto_cor__cor', 'tamanho').all(),
            'cores_disponiveis': Cor.objects.for_filial(_filial(request)).filter(ativo=True),
            'previa': VarianteService.previa(produto),
        })


class ProdutoCorAddView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk)
        cor_id = request.POST.get('cor')
        if cor_id:
            cor = get_object_or_404(Cor.objects.for_filial(_filial(request)), pk=cor_id)
            _, criou = ProdutoCor.objects.get_or_create(produto=produto, cor=cor)
            messages.success(
                request,
                f'Cor {cor.nome} adicionada.' if criou else f'{cor.nome} já estava no produto.',
            )
        return redirect(reverse('moda:produto-detail', args=[produto.pk]))


class ProdutoGerarVariantesView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk)
        try:
            resultado = VarianteService.gerar(produto)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, resultado.mensagem)
        return redirect(reverse('moda:produto-detail', args=[produto.pk]))


# ══════════════════════════════════════════════════════════════════════
# PEDIDOS DE PRODUÇÃO
# ══════════════════════════════════════════════════════════════════════

class PedidoListView(ModaBaseView):
    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        status = (request.GET.get('status') or '').strip()
        # Filtro por cliente, vindo da carteira: por id e não por nome,
        # senão "Interfort" traria também "Interfort Filial 2".
        cliente_id = (request.GET.get('cliente') or '').strip()

        pedidos = (
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente', 'vendedor')
        )
        if busca:
            pedidos = pedidos.filter(
                Q(numero__icontains=busca)
                | Q(cliente__razao_social__icontains=busca)
                | Q(cliente__nome_fantasia__icontains=busca)
                | Q(contato_nome__icontains=busca)
            )
        if status:
            pedidos = pedidos.filter(status=status)
        if cliente_id.isdigit():
            pedidos = pedidos.filter(cliente_id=int(cliente_id))

        # Contagem por status para os atalhos do topo. Uma consulta só, em
        # vez de uma por status.
        contagem = dict(
            PedidoProducao.objects.for_filial(_filial(request))
            .values_list('status')
            .annotate(n=Count('id'))
        )

        # Choices e contagem casados aqui: o template Django nao indexa
        # dicionario por variavel de laco, entao juntar la exigiria filtro
        # customizado so pra isso.
        atalhos = [
            {'valor': valor, 'rotulo': rotulo, 'n': contagem.get(valor, 0)}
            for valor, rotulo in PedidoProducao.Status.choices
        ]

        return render(request, 'moda/pedido_list.html', {
            'title': 'Pedidos de Produção',
            'pedidos': pedidos,
            'busca': busca,
            'status_filtro': status,
            'atalhos': atalhos,
            'total': sum(contagem.values()),
        })


class PedidoFormView(ModaBaseView):
    permissao_acao = 'criar'

    def _obter(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk=None):
        pedido = self._obter(request, pk)
        # Cliente já escolhido quando se chega pela carteira: quem clicou
        # em "novo pedido" na linha de alguém não quer procurar esse
        # alguém de novo num select de mil nomes.
        inicial = {}
        cliente_id = (request.GET.get('cliente') or '').strip()
        if pedido is None and cliente_id.isdigit():
            inicial['cliente'] = int(cliente_id)

        form = PedidoProducaoForm(
            instance=pedido, filial=_filial(request), initial=inicial,
        )
        return render(request, 'moda/pedido_form.html',
                      self._contexto(request, pedido, form))

    def _contexto(self, request, pedido, form) -> dict:
        """O que a tela precisa além do formulário."""
        import json

        from apps.moda.services.clientes import BuscaClientes
        from apps.moda.services.historico import HistoricoService

        # O campo de cliente é uma caixa de busca, não um select: para ele
        # mostrar o nome de quem já está escolhido (edição, ou volta de um
        # erro de validação), a tela precisa do cliente, não só do id.
        escolhido = None
        bruto = form['cliente'].value()
        if bruto:
            achado = form.fields['cliente'].queryset.filter(pk=bruto).first()
            if achado is not None:
                escolhido = BuscaClientes.como_dicionario(achado)

        return {
            'title': f'Pedido #{pedido.numero:06d}' if pedido else 'Novo Pedido de Produção',
            'pedido': pedido,
            'form': form,
            'cliente_escolhido': escolhido,
            # Vai como texto JSON dentro do atributo do Alpine. NÃO é
            # `mark_safe`: o escape do Django transforma as aspas em
            # `&quot;`, que o navegador desfaz ao ler o atributo -- é o que
            # impede um nome de cliente com aspas de quebrar a tela.
            'cliente_escolhido_json': json.dumps(escolhido),
            # Cadastrar cliente mexe na base do ERP inteiro: é permissão de
            # `cadastros`, não da moda. Quem não tem, não vê o botão -- em
            # vez de vê-lo e levar um 'sem permissão' depois de digitar.
            'pode_criar_cliente': request.user.tem_permissao('cadastros', 'criar'),
            'form_cliente': ClienteRapidoForm(),
            'auditoria': HistoricoService.resumo_do_pedido(pedido) if pedido else None,
        }

    def post(self, request, pk=None):
        pedido = self._obter(request, pk)
        form = PedidoProducaoForm(request.POST, instance=pedido, filial=_filial(request))
        if not form.is_valid():
            return render(request, 'moda/pedido_form.html',
                          self._contexto(request, pedido, form))
        pedido = form.save(commit=False)
        pedido.filial = _filial(request)
        pedido.save()
        messages.success(request, f'Pedido #{pedido.numero:06d} salvo.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class PedidoDetailView(ModaBaseView):
    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente', 'vendedor'),
            pk=pk,
        )
        link_publico = request.build_absolute_uri(
            reverse('moda_publico:pedido', args=[pedido.token_publico])
        )
        itens = pedido.itens.select_related(
            'produto', 'modelo', 'cor', 'tecido', 'produto__tecido',
        ).prefetch_related('personalizacoes', 'visuais__mockup').all()
        return render(request, 'moda/pedido_detail.html', {
            'title': f'Pedido #{pedido.numero:06d}',
            'pedido': pedido,
            'status_choices': PedidoProducao.Status.choices,
            'itens': itens,
            'total_pecas': sum(i.quantidade for i in itens),
            'form_item': ItemPedidoProducaoForm(filial=_filial(request)),
            'form_arte': PersonalizacaoForm(),
            'form_visual': VisualItemPedidoForm(filial=_filial(request)),
            'tabela': GradePedidoService.montar_tabela(pedido),
            'tamanhos_disponiveis': Tamanho.objects.for_filial(_filial(request)).filter(ativo=True),
            'individuais': pedido.individuais.select_related('item', 'tamanho').all(),
            'conferencia': IndividualService.conferir(pedido),
            'form_individual': PersonalizacaoIndividualForm(
                filial=_filial(request), pedido=pedido,
            ),
            'form_valores': ValoresPedidoForm(
                instance=pedido, filial=_filial(request),
            ),
            'plano': FinanceiroPedidoService.planejar(pedido),
            'contas': FinanceiroPedidoService.contas_do_pedido(pedido),
            'valores_js': _valores_js(pedido, itens),
            'finalizado': request.GET.get('finalizado') == '1',
            'whatsapp_numero': whatsapp_numero(pedido),
            # O link do cliente e' a PAGINA, nao o PDF: no celular o PDF
            # abre no visualizador, e o status do pedido -- que e' o que ele
            # volta para consultar -- fica de fora. O PDF continua a um
            # toque, dentro da pagina.
            'link_publico': link_publico,
            'mensagem_whatsapp': mensagem_whatsapp(pedido, link_publico),
        })


class PedidoFinalizarView(ModaBaseView):
    """
    Fecha o orcamento e leva de volta a tela com o painel de conclusao.

    O painel aparece por querystring e nao por campo no banco: ele e' um
    MOMENTO ("acabei de finalizar"), nao um estado do pedido. Gravado, ele
    ficaria aparecendo para sempre em toda visita.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )

        if pedido.status == PedidoProducao.Status.CANCELADO:
            messages.error(request, 'Pedido cancelado nao pode ser finalizado.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        if not pedido.itens.exists():
            messages.error(request, 'Acrescente ao menos um produto antes de finalizar.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        if pedido.status == PedidoProducao.Status.ORCAMENTO:
            pedido.status = PedidoProducao.Status.CONFIRMADO
            pedido.save(update_fields=['status', 'updated_at'])

        return redirect(
            reverse('moda:pedido-detail', args=[pedido.pk]) + '?finalizado=1'
        )


class PedidoPdfView(ModaBaseView):
    """
    O PDF do pedido, gerado na hora.

    Inline e nao anexo: quem clica quer conferir antes de mandar para o
    cliente, e forcar download obriga a abrir o arquivo baixado so para
    olhar. O nome do arquivo continua bom para quando for salvo.
    """

    def get(self, request, pk):
        from django.http import HttpResponse

        from .services.pedido_pdf import PedidoPdfService

        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente', 'filial', 'filial__empresa',
                            'forma_pagamento', 'condicao_pagamento')
            .prefetch_related(
                'itens__produto', 'itens__modelo', 'itens__cor', 'itens__tecido',
                'itens__grade__tamanho', 'itens__personalizacoes',
                'itens__visuais__mockup', 'individuais__tamanho', 'individuais__item',
            ),
            pk=pk,
        )

        # A base da URL sai da propria requisicao: o QR precisa apontar para
        # o dominio de onde a folha foi impressa, e fixar isso num setting
        # daria link quebrado em ambiente de teste.
        base = f'{request.scheme}://{request.get_host()}'
        pdf = PedidoPdfService.gerar(pedido, base_url=base)

        resposta = HttpResponse(pdf, content_type='application/pdf')
        resposta['Content-Disposition'] = (
            f'inline; filename="pedido-{pedido.numero:06d}.pdf"'
        )
        return resposta


# Os status que colocam o pedido na mão da fábrica. Chegar em qualquer
# um deles é liberar produção, e cobra as onze validações.
LIBERAM_PRODUCAO = (
    PedidoProducao.Status.LIBERADO_PRODUCAO,
    PedidoProducao.Status.EM_PRODUCAO,
)


class PedidoStatusView(ModaBaseView):
    """Muda só o status — o caminho curto de quem está no chão de fábrica."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        novo = (request.POST.get('status') or '').strip()

        if novo not in PedidoProducao.Status.values:
            messages.error(request, 'Status inválido.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        if novo == pedido.status:
            messages.info(request, 'O pedido já estava nesse status.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        # O outro caminho até a produção: mudar o status à mão. Sem a
        # mesma trava aqui, bastaria escolher "Liberado para Produção" no
        # select para pular as onze validações inteiras.
        if novo in LIBERAM_PRODUCAO:
            try:
                ValidacaoProducao.exigir(pedido)
            except DomainError as erro:
                messages.error(request, str(erro))
                return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        anterior = pedido.get_status_display()
        pedido.status = novo
        pedido.save(update_fields=['status', 'updated_at'])
        messages.success(
            request,
            f'Pedido #{pedido.numero:06d}: {anterior} → {pedido.get_status_display()}.',
        )
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class PedidoValoresView(ModaBaseView):
    """
    Grava a seção de valores: os campos do cabeçalho e o preço de cada item.

    Os dois juntos num POST só porque na tela são um bloco só. Separar em
    dois formulários faria o usuário salvar duas vezes para ver o total
    fechar -- e o total é justamente o que ele veio conferir.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        form = ValoresPedidoForm(request.POST, instance=pedido, filial=_filial(request))

        if not form.is_valid():
            erros = '; '.join(
                f'{form.fields[c].label or c}: {e[0]}' for c, e in form.errors.items()
            )
            messages.error(request, f'Valores não salvos — {erros}')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        with transaction.atomic():
            form.save()
            self._salvar_precos(request, pedido)

        messages.success(request, 'Valores do pedido atualizados.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

    @staticmethod
    def _salvar_precos(request, pedido):
        """
        Lê os campos `valor_<id>` da tabela de itens.

        Só grava o que mudou e só o que pertence a ESTE pedido: os ids vêm
        do formulário, e aceitar um id de outro pedido deixaria alterar
        preço de fora da tela.
        """
        itens = {i.pk: i for i in pedido.itens.all()}
        alterados = []
        for chave, bruto in request.POST.items():
            if not chave.startswith('valor_'):
                continue
            try:
                item = itens[int(chave.removeprefix('valor_'))]
                valor = Decimal((bruto or '0').replace(',', '.'))
            except (ValueError, KeyError, InvalidOperation):
                continue
            if valor < 0 or valor == item.valor_unitario:
                continue
            item.valor_unitario = valor
            alterados.append(item)

        if alterados:
            ItemPedidoProducao.objects.bulk_update(alterados, ['valor_unitario'])


class PedidoFinanceiroGerarView(ModaBaseView):
    """Botão GERAR FINANCEIRO — cria as contas a receber do pedido."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente', 'condicao_pagamento', 'forma_pagamento'),
            pk=pk,
        )
        try:
            contas = FinanceiroPedidoService.gerar(pedido, usuario=request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(
                request,
                f'{len(contas)} conta{"s" if len(contas) > 1 else ""} a receber '
                f'gerada{"s" if len(contas) > 1 else ""} — '
                f'total R$ {pedido.valor_total:.2f}.',
            )
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class PedidoFinanceiroCancelarView(ModaBaseView):
    """Desfaz o financeiro para poder gerar de novo com os valores certos."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        try:
            quantidade = FinanceiroPedidoService.cancelar(pedido, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, f'{quantidade} conta(s) cancelada(s).')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class ItemPedidoCreateView(ModaBaseView):
    """Acrescenta um produto ao pedido — vários por pedido."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        form = ItemPedidoProducaoForm(request.POST, filial=_filial(request))

        if not form.is_valid():
            # Erros voltam na própria tela do pedido, com o formulário
            # preenchido — reabrir vazio faria o usuário digitar tudo de novo.
            itens = pedido.itens.select_related('produto', 'modelo', 'cor', 'tecido').all()
            return render(request, 'moda/pedido_detail.html', {
                'title': f'Pedido #{pedido.numero:06d}',
                'pedido': pedido,
                'status_choices': PedidoProducao.Status.choices,
                'itens': itens,
                'total_pecas': sum(i.quantidade for i in itens),
                'form_item': form,
                'abrir_form_item': True,
            })

        item = form.save(commit=False)
        item.pedido = pedido
        # Última posição: o item novo entra no fim da ficha, como numa lista
        # escrita à mão.
        ultima = pedido.itens.aggregate(models.Max('ordem'))['ordem__max'] or 0
        item.ordem = ultima + 10
        item.save()
        messages.success(request, f'{item.nome_exibicao} adicionado ao pedido.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class ItemPedidoDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        nome = item.nome_exibicao
        item.delete()
        messages.success(request, f'{nome} removido do pedido.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class PersonalizacaoCreateView(ModaBaseView):
    """Acrescenta uma arte ao item — várias por item."""

    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        form = PersonalizacaoForm(request.POST, request.FILES)

        if not form.is_valid():
            # Erro de upload não pode custar o resto do formulário: os erros
            # vão como mensagem, com o campo nomeado.
            for campo, erros in form.errors.items():
                rotulo = form.fields[campo].label if campo in form.fields else campo
                for erro in erros:
                    messages.error(request, f'{rotulo}: {erro}')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        arte = form.save(commit=False)
        arte.item = item
        arte.save()
        messages.success(request, f'Arte adicionada a {item.nome_exibicao}.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class PersonalizacaoDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk, arte_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        arte = get_object_or_404(Personalizacao, pk=arte_pk, item=item)
        arte.delete()
        messages.success(request, 'Arte removida.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class VisualCreateView(ModaBaseView):
    """Acrescenta uma vista (frente/costas) ao item."""

    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        form = VisualItemPedidoForm(
            request.POST, request.FILES, filial=_filial(request), item=item,
        )

        if not form.is_valid():
            for campo, erros in form.errors.items():
                rotulo = form.fields[campo].label if campo in form.fields else campo
                for erro in erros:
                    messages.error(request, f'{rotulo}: {erro}')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        visual = form.save(commit=False)
        visual.item = item
        visual.save()
        messages.success(
            request, f'{visual.get_posicao_display()} adicionada a {item.nome_exibicao}.',
        )
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class VisualDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk, visual_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        visual = get_object_or_404(VisualItemPedido, pk=visual_pk, item=item)
        nome = visual.get_posicao_display()
        visual.delete()
        messages.success(request, f'{nome} removida.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


# ══════════════════════════════════════════════════════════════════════
# GRADE DO PEDIDO (quantidade por tamanho)
# ══════════════════════════════════════════════════════════════════════

class GradePedidoSalvarView(ModaBaseView):
    """Grava a tabela inteira de uma vez."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)

        # Campos chegam como qtd_<item_id>_<tamanho_id>. Ler tudo antes de
        # gravar deixa o service salvar numa transação só.
        quantidades = {}
        for chave, valor in request.POST.items():
            if not chave.startswith('qtd_'):
                continue
            try:
                _, item_id, tamanho_id = chave.split('_')
                quantidades[(int(item_id), int(tamanho_id))] = int(valor or 0)
            except (ValueError, TypeError):
                # Campo malformado é ignorado em vez de derrubar a tela: o
                # resto da tabela continua válido.
                continue

        try:
            total = GradePedidoService.salvar_quantidades(pedido, quantidades)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Grade salva. Total do pedido: {total} peça(s).')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class GradeTamanhoAddView(ModaBaseView):
    """Acrescenta uma coluna (tamanho) à tabela."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)

        if not pedido.itens.exists():
            messages.error(request, 'Adicione um produto ao pedido antes de montar a grade.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        tamanho = get_object_or_404(
            Tamanho.objects.for_filial(_filial(request)), pk=request.POST.get('tamanho'),
        )
        criadas = GradePedidoService.adicionar_tamanho(pedido, tamanho)
        if criadas:
            messages.success(request, f'Tamanho {tamanho.sigla} adicionado à grade.')
        else:
            messages.info(request, f'{tamanho.sigla} já estava na grade.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class GradeTamanhoRemoveView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, tamanho_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        tamanho = get_object_or_404(Tamanho.objects.for_filial(_filial(request)), pk=tamanho_pk)
        GradePedidoService.remover_tamanho(pedido, tamanho)
        messages.success(request, f'Tamanho {tamanho.sigla} removido da grade.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class GradeAplicarDoProdutoView(ModaBaseView):
    """Monta as colunas a partir da grade cadastrada do produto do item."""

    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        try:
            criadas = GradePedidoService.aplicar_grade_do_produto(item)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'{criadas} tamanho(s) trazidos da grade do produto.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class GradeCopiarView(ModaBaseView):
    """Copia a grade de um item para outro do mesmo pedido."""

    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        origem = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        destino = get_object_or_404(
            ItemPedidoProducao, pk=request.POST.get('destino'), pedido=pedido,
        )
        try:
            copiadas = GradePedidoService.copiar_grade(origem, destino)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f'Grade de {origem.nome_exibicao} copiada para {destino.nome_exibicao} '
                f'({copiadas} tamanho(s)).',
            )
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class ItemDuplicarView(ModaBaseView):
    """Duplica a linha: mesmas especificações e mesma grade."""

    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoProducao, pk=item_pk, pedido=pedido)
        copia = GradePedidoService.duplicar_item(item)
        messages.success(
            request,
            f'{copia.nome_exibicao} duplicado. A arte e as imagens não vieram junto — '
            f'quase sempre mudam entre peças.',
        )
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


# ══════════════════════════════════════════════════════════════════════
# PERSONALIZAÇÃO POR PESSOA
# ══════════════════════════════════════════════════════════════════════

class IndividualFormView(ModaBaseView):
    """Adiciona ou edita uma pessoa da lista."""

    permissao_acao = 'editar'

    def post(self, request, pk, individual_pk=None):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        obj = (
            get_object_or_404(PersonalizacaoIndividual, pk=individual_pk, pedido=pedido)
            if individual_pk else None
        )
        form = PersonalizacaoIndividualForm(
            request.POST, instance=obj, filial=_filial(request), pedido=pedido,
        )

        if not form.is_valid():
            for campo, erros in form.errors.items():
                rotulo = form.fields[campo].label if campo in form.fields else campo
                for erro in erros:
                    messages.error(request, f'{rotulo}: {erro}')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        pessoa = form.save(commit=False)
        pessoa.pedido = pedido
        if not pessoa.ordem:
            pessoa.ordem = (pedido.individuais.count() + 1) * 10
        pessoa.save()
        messages.success(request, f'{pessoa.identificacao} salvo(a).')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class IndividualDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, individual_pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        pessoa = get_object_or_404(PersonalizacaoIndividual, pk=individual_pk, pedido=pedido)
        nome = pessoa.identificacao
        pessoa.delete()
        messages.success(request, f'{nome} removido(a) da lista.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))


class IndividualImportarView(ModaBaseView):
    """Importa a lista de pessoas de um CSV ou Excel."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoProducao.objects.for_filial(_filial(request)), pk=pk)
        arquivo = request.FILES.get('arquivo')

        if not arquivo:
            messages.error(request, 'Escolha um arquivo .csv ou .xlsx.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        try:
            resultado = IndividualService.importar(pedido, arquivo, arquivo.name)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        if resultado.criados:
            messages.success(request, f'{resultado.criados} pessoa(s) importada(s).')

        # Erros linha a linha: o usuário precisa saber QUAIS linhas falharam
        # para corrigir só elas, em vez de reenviar a planilha inteira.
        for erro in resultado.erros[:10]:
            messages.warning(request, f'Linha {erro["linha"]}: {erro["erro"]}')
        if len(resultado.erros) > 10:
            messages.warning(request, f'... e mais {len(resultado.erros) - 10} linha(s) com problema.')
        if not resultado.criados and not resultado.erros:
            messages.info(request, 'Nenhuma linha com dados foi encontrada no arquivo.')

        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))
