"""
View pública do PDF do pedido.

Sem `PermissaoRequiredMixin` e sem `ModaBaseView` — é deliberado, e é a
única view do vertical assim. Quem abre é o cliente, pelo link que recebeu
no WhatsApp; exigir login aqui tornaria o envio inútil.

O que ela expõe é o PDF de UM pedido, para quem tem o token daquele pedido.
Não há listagem, não há busca e não há como andar de um pedido para outro:
o token não é adivinhável e a view não aceita nenhum outro parâmetro.
"""
from django.http import Http404, HttpResponse
from django.views import View

from .models import PedidoProducao
from .services.pedido_pdf import PedidoPdfService


class PedidoPdfPublicoView(View):
    """Entrega o PDF de um pedido pelo token."""

    def get(self, request, token):
        pedido = (
            PedidoProducao.all_objects
            .select_related('cliente', 'filial', 'filial__empresa',
                            'forma_pagamento', 'condicao_pagamento')
            .prefetch_related(
                'itens__produto', 'itens__modelo', 'itens__cor', 'itens__tecido',
                'itens__grade__tamanho', 'itens__personalizacoes',
                'itens__visuais__mockup', 'individuais__tamanho', 'individuais__item',
            )
            .filter(token_publico=token)
            .first()
        )
        # 404 e não 403: um token errado não deve confirmar que o pedido
        # existe. "Não encontrado" é a resposta certa para as duas situações.
        if pedido is None or not token:
            raise Http404('Pedido não encontrado.')

        base = f'{request.scheme}://{request.get_host()}'
        pdf = PedidoPdfService.gerar(pedido, base_url=base)

        resposta = HttpResponse(pdf, content_type='application/pdf')
        # Inline: o cliente abre no navegador do celular sem baixar nada,
        # que é o comportamento esperado de um link no WhatsApp.
        resposta['Content-Disposition'] = (
            f'inline; filename="PED-{pedido.numero:06d}.pdf"'
        )
        # O documento traz dados do cliente: não deve ficar em cache de
        # proxy nem ser indexado se o link vazar para algum lugar público.
        resposta['Cache-Control'] = 'private, no-store'
        resposta['X-Robots-Tag'] = 'noindex, nofollow'
        return resposta
