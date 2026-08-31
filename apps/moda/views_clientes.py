"""
Carteira de clientes da confecção.

Duas permissões diferentes na mesma tela, e não é detalhe: VER a carteira é
do comercial da moda (`moda_comercial`); CRIAR cliente é do cadastro do ERP
(`cadastros`), porque o cliente não pertence ao vertical. Quem só vende
moda vê a lista e não cria — e isso é o certo, não uma limitação.
"""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.cadastros.services.cliente_service import ClienteService
from apps.cadastros.models import Cliente
from apps.core.services.exceptions import DomainError

from .forms_cliente import ClienteRapidoForm
from .services.clientes import BuscaClientes, CarteiraService
from .views import ModaBaseView

# Quantos nomes por página. A carteira de uma confecção passa de mil, e
# mandar tudo de uma vez trava o navegador do tablet.
POR_PAGINA = 60


class CarteiraClientesView(ModaBaseView):
    """A lista, com os números do vertical em cada linha."""

    area = 'comercial'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        so_com_pedido = request.GET.get('com_pedido') == '1'

        consulta = CarteiraService.listar(
            request.filial_ativa, busca=busca, so_com_pedido=so_com_pedido,
        )
        clientes = list(consulta[:POR_PAGINA])
        total = consulta.count()

        return render(request, 'moda/clientes.html', {
            'title': 'Clientes',
            'clientes': clientes,
            'resumo': CarteiraService.resumo(clientes),
            'busca': busca,
            'so_com_pedido': so_com_pedido,
            'total': total,
            # A lista é cortada: dizer isso evita a conclusão de que o
            # cliente que não apareceu não existe.
            'cortada': total > len(clientes),
            'por_pagina': POR_PAGINA,
            'form': ClienteRapidoForm(),
            'pode_criar': request.user.tem_permissao('cadastros', 'criar'),
        })


class ClienteRapidoCreateView(ModaBaseView):
    """
    Cadastro rápido, sem sair da tela.

    A permissão cobrada é a de CADASTROS, não a da moda: criar cliente mexe
    na base do ERP inteiro, e quem pode vender não necessariamente pode
    incluir gente nela.
    """

    area = 'comercial'
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'

    def post(self, request):
        form = ClienteRapidoForm(request.POST)
        destino = reverse('moda:clientes')

        if not form.is_valid():
            # Erro por erro, com o nome do campo: "corrija os erros" sozinho
            # manda o usuário procurar o que está errado num formulário que
            # ele acabou de fechar.
            for campo, erros in form.errors.items():
                rotulo = form.fields[campo].label if campo in form.fields else campo
                messages.error(request, f'{rotulo}: {" ".join(erros)}')
            return redirect(destino)

        try:
            cliente = ClienteService.criar(
                form.cleaned_data, request.user, request.filial_ativa,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(destino)

        messages.success(
            request,
            f'Cliente "{cliente.nome_display}" cadastrado. '
            f'Complete endereço e condições no cadastro geral quando precisar.',
        )
        # Volta com o nome na busca: o motivo de cadastrar é usar em seguida,
        # e devolver a lista inteira faria procurar de novo o que acabou de
        # ser criado.
        return redirect(f'{destino}?q={cliente.cpf_cnpj}')


class ClienteBuscaView(ModaBaseView):
    """
    Busca de clientes por digitação, em JSON.

    NO SERVIDOR, e não filtrando uma lista embutida na página: a base de
    clientes do ERP passa de mil nomes, e mandar todos em toda abertura do
    formulário de pedido pesaria em cada carregamento para economizar uma
    consulta rápida.

    Devolve telefone e contato junto com o nome — é o que preenche os campos
    de contato assim que o cliente é escolhido.
    """

    area = 'comercial'
    permissao_acao = 'ver'

    def get(self, request):
        clientes = BuscaClientes.procurar(
            request.filial_ativa, request.GET.get('q') or '',
        )
        return JsonResponse({
            'clientes': [BuscaClientes.como_dicionario(c) for c in clientes],
        })


class ClienteRapidoJsonView(ModaBaseView):
    """
    O mesmo cadastro rápido, respondendo JSON.

    EXISTE PORQUE O PEDIDO ESTÁ SENDO DIGITADO. A versão que redireciona
    serve à carteira, onde não há nada a perder; aqui, recarregar a página
    jogaria fora tudo que o vendedor já preencheu no pedido — que é
    exatamente o que faz alguém desistir e digitar o nome do cliente na
    observação.
    """

    area = 'comercial'
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'

    def post(self, request):
        form = ClienteRapidoForm(request.POST)

        if not form.is_valid():
            # Erro por campo, e não uma frase geral: o formulário continua
            # aberto na tela, e cada mensagem tem onde aparecer.
            return JsonResponse({
                'ok': False,
                'erros': {c: [str(e) for e in erros]
                          for c, erros in form.errors.items()},
            }, status=400)

        try:
            cliente = ClienteService.criar(
                form.cleaned_data, request.user, request.filial_ativa,
            )
        except DomainError as erro:
            # CPF/CNPJ repetido cai aqui. É regra de negócio recusando, não
            # falha do sistema.
            return JsonResponse({'ok': False, 'erro': str(erro)}, status=400)

        return JsonResponse({
            'ok': True,
            'cliente': BuscaClientes.como_dicionario(cliente),
        })


class ClienteRapidoUpdateJsonView(ModaBaseView):
    """Edita o cadastro selecionado sem descartar o orçamento em digitação."""

    area = 'comercial'
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def get_object(self, request, pk):
        return get_object_or_404(
            Cliente.objects.for_filial(request.filial_ativa), pk=pk,
        )

    def get(self, request, pk):
        cliente = self.get_object(request, pk)
        form = ClienteRapidoForm(instance=cliente)
        return JsonResponse({
            'ok': True,
            'campos': {nome: form[nome].value() for nome in form.fields},
        })

    def post(self, request, pk):
        cliente = self.get_object(request, pk)
        form = ClienteRapidoForm(request.POST, instance=cliente)
        if not form.is_valid():
            return JsonResponse({'ok': False, 'erros': dict(form.errors)}, status=400)
        try:
            cliente = ClienteService.atualizar(cliente, form.cleaned_data)
        except DomainError as erro:
            return JsonResponse({'ok': False, 'erro': str(erro)}, status=400)
        return JsonResponse({
            'ok': True, 'cliente': BuscaClientes.como_dicionario(cliente),
        })
