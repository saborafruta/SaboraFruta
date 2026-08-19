"""
Carteira de clientes da confecção.

Duas permissões diferentes na mesma tela, e não é detalhe: VER a carteira é
do comercial da moda (`moda_comercial`); CRIAR cliente é do cadastro do ERP
(`cadastros`), porque o cliente não pertence ao vertical. Quem só vende
moda vê a lista e não cria — e isso é o certo, não uma limitação.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.cadastros.services.cliente_service import ClienteService
from apps.core.services.exceptions import DomainError

from .forms_cliente import ClienteRapidoForm
from .services.clientes import CarteiraService
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
