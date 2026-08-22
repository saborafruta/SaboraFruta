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

from .forms_arquivo import ArquivoPedidoForm
from .forms_cliente import ClienteRapidoForm
from .forms import (
    CorForm, GradeForm, ItemPedidoProducaoForm, PedidoProducaoForm,
    PersonalizacaoForm, PersonalizacaoIndividualForm, ProdutoModaForm,
    ValoresPedidoForm, VisualItemPedidoForm,
)
from .models import (
    ArquivoPedido, Cor, Grade, ItemGrade, ItemGradePedido, ItemPedidoProducao,
    PedidoProducao,
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


def contexto_do_pedido(request, pedido, **extra) -> dict:
    """
    Tudo que a tela do pedido precisa.

    Vive fora da view porque DUAS views renderizam esta tela: a de
    detalhe e a de adicionar item, quando o formulário volta com erro.
    Enquanto eram dois contextos, o segundo trazia um terço das chaves e
    a tela aparecia sem grade, sem valores e sem arquivos -- o que faz
    um erro de digitação parecer perda de dados.
    """
    filial = _filial(request)
    link_publico = request.build_absolute_uri(
        reverse('moda_publico:pedido', args=[pedido.token_publico])
    )
    itens = pedido.itens.select_related(
        'produto', 'modelo', 'cor', 'tecido', 'produto__tecido',
    ).prefetch_related('personalizacoes', 'visuais__mockup').all()
    contexto = {
        'title': f'Pedido #{pedido.numero:06d}',
        'pedido': pedido,
        'status_choices': PedidoProducao.Status.choices,
        'itens': itens,
        'total_pecas': sum(i.quantidade for i in itens),
        'form_item': ItemPedidoProducaoForm(filial=filial),
        # O produto ja' escolhido, para a caixa de busca voltar preenchida
        # quando o formulario recusa alguma coisa. Sem isso, um erro de
        # digitacao em OUTRO campo faria a pessoa procurar o produto de novo.
        'produto_escolhido_json': 'null',
        'form_arte': PersonalizacaoForm(),
        'form_arquivo': ArquivoPedidoForm(),
        'tipos_arte': Personalizacao.Tipo.choices,
        'tecnicas_arte': Personalizacao.Tecnica.choices,
        # Consultas do pedido: viram links de texto, e não mais cinco
        # botões do mesmo peso das ações que movem o pedido adiante.
        'atalhos': [
            ('Histórico', reverse('moda:pedido-historico', args=[pedido.pk])),
            ('Fluxo', reverse('moda:pedido-fluxo', args=[pedido.pk])),
            ('Aprovação', reverse('moda:pedido-aprovacao', args=[pedido.pk])),
            ('QR Code', reverse('moda:qr-etiqueta', args=[pedido.codigo_qr])),
        ],
        'arquivos': pedido.arquivos.select_related('enviado_por').all(),
        'form_visual': VisualItemPedidoForm(filial=filial),
        'tabela': GradePedidoService.montar_tabela(pedido),
        'tamanhos_disponiveis': Tamanho.objects.for_filial(filial).filter(ativo=True),
        # As grades cadastradas, para escolher no formulário do produto quais
        # tamanhos entram. `ItemGrade` já ordena por `ordem`, então o prefetch
        # devolve cada grade na ordem que vai para a ficha de produção.
        'grades_disponiveis': (
            Grade.objects.for_filial(filial).filter(ativo=True).prefetch_related(
                Prefetch('itens', queryset=ItemGrade.objects.select_related('tamanho'))
            )
        ),
        # Quais grades reabrem marcadas quando o formulário volta com erro.
        # Em GET o POST é vazio e cai em "todos os tamanhos", que é o
        # comportamento de sempre. Vai para o template por `json_script`,
        # que é escape seguro para dado que vira valor de JavaScript.
        'grades_escolhidas': [
            pk for pk in request.POST.getlist('grade_escolhida') if pk.isdigit()
        ],
        'individuais': pedido.individuais.select_related('item', 'tamanho').all(),
        'conferencia': IndividualService.conferir(pedido),
        # O QUE TRAVA A PRODUCAO, na propria tela do pedido. As onze
        # validacoes ja' existiam, mas so' na tela de fluxo: a pessoa
        # descobria o bloqueio ao clicar em emitir a OP -- uma ida e volta
        # por pendencia.
        'validacao': ValidacaoProducao.resumo(pedido),
        # As vagas de cada tamanho, por produto -- e' o que o campo de
        # tamanho da personalizacao filtra. A chave vai como TEXTO porque
        # e' assim que o `<select>` devolve o valor escolhido, e comparar
        # numero com texto no JavaScript daria lista sempre vazia.
        'vagas_json': {
            str(item_id): linhas
            for item_id, linhas in IndividualService.vagas(pedido).items()
        },
        'form_individual': PersonalizacaoIndividualForm(
            filial=filial, pedido=pedido,
        ),
        'form_valores': ValoresPedidoForm(
            instance=pedido, filial=filial,
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
    }

    contexto.update(extra)
    return contexto


class PedidoDetailView(ModaBaseView):
    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente', 'vendedor'),
            pk=pk,
        )
        return render(request, 'moda/pedido_detail.html',
                      contexto_do_pedido(request, pedido))

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
            # E com o contexto COMPLETO: antes vinham seis chaves, e a tela
            # aparecia sem grade, sem valores e sem arquivos, o que faz um
            # erro de digitação parecer perda de dados.
            return render(request, 'moda/pedido_detail.html', contexto_do_pedido(
                request, pedido, form_item=form, abrir_form_item=True,
            ))

        # UMA LINHA POR GRADE. Escolher Adulto e OverSize acrescenta dois
        # itens, não um: a quantidade mora em `ItemGradePedido`, com
        # `unique_together ('item','tamanho')`, e as grades compartilham os
        # MESMOS registros de Tamanho -- num item só, o "G" de uma apagaria
        # o "G" da outra. Sem grade escolhida, segue um item só, como antes.
        grades = self._grades_escolhidas(request, _filial(request))

        # Última posição: o item novo entra no fim da ficha, como numa lista
        # escrita à mão.
        ultima = pedido.itens.aggregate(models.Max('ordem'))['ordem__max'] or 0

        pks = []
        for posicao, grade in enumerate(grades or [None], start=1):
            # `form.save(commit=False)` devolve sempre a MESMA instância, e
            # é por isso que aqui se guarda a PK e não o objeto: guardar o
            # objeto colocaria a mesma referência na lista várias vezes, e
            # as duas grades acabariam gravando no mesmo item.
            #
            # Zerar a pk faz o `save()` seguinte INSERIR outra linha em vez
            # de reescrever a anterior.
            item = form.save(commit=False)
            item.pk = None
            item._state.adding = True
            item.pedido = pedido
            item.grade = grade
            item.ordem = ultima + posicao * 10
            item.save()
            pks.append(item.pk)

        criados = list(
            ItemPedidoProducao.objects.filter(pk__in=pks)
            .select_related('grade', 'produto').order_by('ordem')
        )
        item = criados[0]
        if len(criados) == 1:
            recado = [f'{item.nome_exibicao} adicionado ao pedido.']
        else:
            recado = [
                f'{len(criados)} itens adicionados ao pedido, um por grade: '
                f'{", ".join(i.grade.nome for i in criados)}.'
            ]
        if getattr(form, 'produto_importado', None) is not None:
            # Produto escolhido do cadastro do ERP: ele foi TRAZIDO para a
            # confecção agora. Dizer isso evita a dúvida de por que ele
            # passou a aparecer na tela de produtos de moda.
            recado.append(
                f'“{form.produto_importado.nome}” veio do cadastro de produtos '
                f'e agora também está no catálogo da confecção.'
            )
        recado += self._grade_inicial(request, pedido, criados)
        recado += self._arte_inicial(request, criados)

        messages.success(request, ' '.join(recado))
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

    @staticmethod
    def _grades_escolhidas(request, filial) -> list:
        """As grades marcadas no formulário, na ordem em que estão cadastradas."""
        ids = [pk for pk in request.POST.getlist('grade_escolhida') if pk.isdigit()]
        if not ids:
            return []
        return list(Grade.objects.for_filial(filial).filter(pk__in=ids, ativo=True))

    @staticmethod
    def _grade_inicial(request, pedido, itens) -> list:
        """
        A grade lançada JUNTO com o produto.

        Antes, a tabela de grade só existia depois de o item estar salvo --
        e quem estava com o cliente ao telefone ("5 G, 10 M, 3 P") tinha de
        adicionar o produto, procurar a tabela mais abaixo e digitar de novo.
        Aqui os dois viram um passo só.

        Os campos vêm nomeados de dois jeitos, e é o formato que diz de quem
        é a quantidade:

            grade_<tamanho>            -> item sem grade ("Todos os tamanhos")
            grade_<grade>_<tamanho>    -> item daquela grade

        Por isso o `isdigit()` no resto da chave separa os dois: com o
        prefixo curto, `grade_10_5` deixa `10_5`, que não é dígito e cai
        fora -- senão a quantidade de uma grade vazaria para o item errado.
        """
        quantidades = {}
        for item in itens:
            prefixo = f'grade_{item.grade_id}_' if item.grade_id else 'grade_'
            for chave, valor in request.POST.items():
                if not chave.startswith(prefixo):
                    continue
                tamanho_id = chave.removeprefix(prefixo)
                if not tamanho_id.isdigit():
                    continue
                try:
                    qtd = int(valor or 0)
                except (TypeError, ValueError):
                    continue
                if qtd > 0:
                    quantidades[(item.pk, int(tamanho_id))] = qtd

        if not quantidades:
            return []

        total = GradePedidoService.salvar_quantidades(pedido, quantidades)
        # A quantidade do item passa a ser a soma da grade -- é o serviço
        # que faz isso, e avisar aqui evita a leitura de que a quantidade
        # digitada foi ignorada.
        return [f'Grade lançada: {total} peça(s) no pedido.']

    @staticmethod
    def _arte_inicial(request, itens) -> list:
        """
        A arte anexada no mesmo passo, quando veio alguma.

        Vai em TODOS os itens criados: com duas grades marcadas, Adulto e
        OverSize são o mesmo produto com a mesma arte, e deixar a estampa
        só na primeira linha mandaria a segunda para a produção sem arte.
        """
        tem_arquivo = bool(request.FILES.get('arte_arquivo'))
        local = (request.POST.get('arte_local') or '').strip()
        if not tem_arquivo and not local:
            return []

        for item in itens:
            Personalizacao.objects.create(
                item=item,
                tipo=request.POST.get('arte_tipo') or Personalizacao.Tipo.ARTE,
                tecnica=request.POST.get('arte_tecnica') or Personalizacao.Tecnica.SUBLIMACAO,
                local=local,
                # O mesmo upload serve as duas gravações: `File.chunks()`
                # volta ao início do arquivo antes de ler, então a segunda
                # não sai vazia.
                arquivo=request.FILES.get('arte_arquivo'),
            )
        if len(itens) == 1:
            return ['Arte anexada ao item.']
        return [f'Arte anexada aos {len(itens)} itens.']


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


class PedidoArquivoAddView(ModaBaseView):
    """
    Anexa arquivo ao PEDIDO — a arte que chegou antes de existir item.

    Fica no pedido, e não no item, porque é o que o cliente manda no começo:
    o layout, a planilha de nomes, a foto da camisa do ano passado. Sem isto,
    pedido recém-criado não tinha onde guardar a arte, e ela ficava no
    celular de quem atendeu.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        destino = reverse('moda:pedido-detail', args=[pedido.pk])
        arquivos = request.FILES.getlist('arquivo')

        if not arquivos:
            messages.error(request, 'Escolha ao menos um arquivo.')
            return redirect(destino)

        form = ArquivoPedidoForm(request.POST)
        if not form.is_valid():
            for campo, erros in form.errors.items():
                messages.error(request, f'{campo}: {" ".join(erros)}')
            return redirect(destino)

        criados, recusados = [], []
        for arquivo in arquivos:
            # Um por um, e não em bloco: um arquivo com extensão recusada
            # não pode derrubar os outros quatro que vieram junto.
            individual = ArquivoPedidoForm(
                request.POST, {'arquivo': arquivo}, instance=ArquivoPedido(pedido=pedido),
            )
            if not individual.is_valid():
                recusados.append(arquivo.name)
                continue
            anexo = individual.save(commit=False)
            anexo.pedido = pedido
            anexo.enviado_por = request.user
            anexo.save()
            criados.append(anexo)

        if criados:
            messages.success(
                request,
                f'{len(criados)} arquivo(s) anexado(s) ao pedido #{pedido.numero:06d}.',
            )
        if recusados:
            messages.error(
                request,
                'Não foi possível anexar: ' + ', '.join(recusados)
                + ' — extensão não aceita.',
            )
        return redirect(destino)


class PedidoArquivoDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, arquivo_pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        anexo = get_object_or_404(ArquivoPedido, pk=arquivo_pk, pedido=pedido)
        nome = anexo.descricao or anexo.nome_arquivo
        anexo.delete()
        messages.success(request, f'“{nome}” removido do pedido.')
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))
