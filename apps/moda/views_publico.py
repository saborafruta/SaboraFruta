"""
Acesso público do cliente ao pedido: a página e o PDF.

Nenhuma das duas views herda `ModaBaseView` — é deliberado, e são as únicas
do vertical assim. Quem abre é o cliente, pelo link que recebeu; exigir
login aqui tornaria o envio inútil.

O QUE IMPEDE CHEGAR A OUTRO PEDIDO, concretamente:

  1. a busca é só por token, e o token não é derivável do número do pedido;
  2. não existe listagem, busca nem paginação — nenhuma rota pública aceita
     qualquer outro parâmetro além do token;
  3. a página não tem UM ÚNICO link para outro pedido, para o sistema ou
     para o login: o que não está na tela não pode ser seguido;
  4. token inexistente responde 404, igual a token de pedido apagado — a
     resposta não distingue "não existe" de "existe e você não pode".

O que a página mostra é o combinado com o cliente: pedido, produtos,
quantidade, grade, arte, prazo e status. Observação interna do pedido e do
item ficam de fora — são recado entre a equipe, e o cliente não é o
destinatário delas.
"""
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.core.middleware.audit import get_client_ip

from .models import AprovacaoPedido, ArquivoPedido, PedidoProducao
from .services.pedido_pdf import PedidoPdfService

# Status internos que não fazem sentido para quem está do lado de fora.
# Cancelado aparece; orçamento não — pedido que ainda é proposta não deveria
# ter link circulando POR ACIDENTE.
#
# MAS ORÇAMENTO LIBERADO ABRE. A tela de aprovação entrega o link e manda
# enviar ao cliente; se a página recusasse, o vendedor mandaria um endereço
# que responde 'Not Found' -- foi o que aconteceu. Liberação interna é uma
# decisão explícita de alguém com permissão de aprovar: é ela que distingue
# a proposta que vazou da proposta que a casa quis mostrar.
STATUS_OCULTOS = ('orcamento',)


def _pode_abrir(pedido) -> bool:
    if pedido.status not in STATUS_OCULTOS:
        return True
    aprovacao = getattr(pedido, 'aprovacao', None)
    return bool(aprovacao and aprovacao.liberado)

# A regra de o que sai do escritório mora no MODEL: a página do link e o PDF
# são dois leitores do mesmo acervo, e duas listas divergiriam.
from .models.arquivo import TIPOS_VISIVEIS_AO_CLIENTE  # noqa: E402


def _blindar(resposta) -> None:
    """
    Cabeçalhos de uma URL-capacidade.

    `no-store` porque o documento traz dados do cliente e não pode ficar em
    cache de proxy; `noindex` porque um link vazado para rede social ou
    e-mail acabaria em buscador, e aí o token deixaria de ser segredo.

    `same-origin` E NÃO `no-referrer` NO REFERRER-POLICY, apesar de o
    segundo parecer mais seguro. Pela especificação do Fetch, requisição
    que não é GET nem HEAD, saindo de uma página com política
    `no-referrer`, manda `Origin: null` -- e o Django compara o `Origin`
    com o host da requisição, não bate, e RECUSA como falha de CSRF.

    Era isso que impedia o cliente de aprovar: ele clicava em APROVAR
    PEDIDO e recebia "a página ficou aberta tempo demais", que é o texto
    da falha de CSRF, tendo acabado de abrir a página. O GET funcionava, e
    por isso o link parecia bom até a hora de responder.

    `same-origin` guarda o que interessa: o endereço com o token não vai no
    Referer para site nenhum de fora. Para o próprio domínio ele vai --
    e é o próprio domínio que já tem o token.
    """
    resposta['Cache-Control'] = 'private, no-store'
    resposta['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    resposta['Referrer-Policy'] = 'same-origin'


def _buscar(token: str) -> PedidoProducao:
    """
    O pedido daquele token, ou 404.

    `all_objects` porque não há filial ativa numa requisição sem login — e o
    escopo aqui é o token, que já é de um pedido só.
    """
    if not token or len(token) < 16:
        # Token curto demais nem chega ao banco: é tentativa de adivinhação,
        # e responder rápido evita usar o banco como oráculo de tempo.
        raise Http404('Pedido não encontrado.')

    pedido = (
        PedidoProducao.all_objects
        .select_related('cliente', 'filial', 'filial__empresa', 'aprovacao')
        .prefetch_related(
            'itens__produto', 'itens__modelo', 'itens__cor', 'itens__tecido',
            'itens__grade__tamanho', 'itens__personalizacoes',
            'arquivos',
            'itens__visuais__mockup',
        )
        .filter(token_publico=token)
        .first()
    )
    if pedido is None or not _pode_abrir(pedido):
        raise Http404('Pedido não encontrado.')
    return pedido


def _prazo(pedido) -> str:
    """
    "em 12 dias", "hoje", "12 dias em atraso" — ou vazio.

    Sai daqui e não do template porque o número cru é negativo
    quando atrasa: "-12 dia(s)" na tela do cliente nao diz nada.
    """
    dias = pedido.dias_para_entrega
    if dias is None:
        return ''
    if dias == 0:
        return 'hoje'
    if dias > 0:
        return f'em {dias} dia' + ('s' if dias > 1 else '')
    atraso = -dias
    return f'{atraso} dia' + ('s' if atraso > 1 else '') + ' em atraso'


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PedidoOnlineView(View):
    """
    A página que o cliente abre pelo link.

    `ensure_csrf_cookie` porque o formulário de aprovação só é desenhado em
    parte das visitas (pedido liberado, ainda sem resposta) -- e sem ele o
    cookie de CSRF só nascia nessas. Quem abriu a página numa hora e
    respondeu em outra podia cair num 403 sem ter feito nada de errado.
    """

    def get(self, request, token):
        pedido = _buscar(token)

        etapas = [
            (valor, rotulo) for valor, rotulo in PedidoProducao.Status.choices
            if valor not in STATUS_OCULTOS and valor != 'cancelado'
        ]
        valores = [v for v, _r in etapas]
        atual = valores.index(pedido.status) if pedido.status in valores else -1

        resposta = render(request, 'moda/publico/pedido.html', {
            'pedido': pedido,
            'itens': pedido.itens.all(),
            'empresa': pedido.filial.empresa,
            'etapas': [
                {'label': rotulo, 'passou': i <= atual, 'atual': i == atual}
                for i, (_v, rotulo) in enumerate(etapas)
            ],
            'cancelado': pedido.status == 'cancelado',
            'prazo': _prazo(pedido),
            # A aprovação só aparece depois que a casa liberou o pedido: até
            # lá o cliente estaria aprovando um documento que ainda pode
            # mudar de preço.
            'aprovacao': getattr(pedido, 'aprovacao', None),
            # ARTE DO PEDIDO. Só os tipos que o cliente deve ver: a arte e
            # a referência que ele mesmo mandou. Documento e 'outro' ficam
            # de fora -- contrato e recado interno não são para este lado.
            'artes': [
                a for a in pedido.arquivos.all()
                if a.tipo in TIPOS_VISIVEIS_AO_CLIENTE
            ],
        })
        _blindar(resposta)
        return resposta


class PedidoPdfPublicoView(View):
    """O mesmo pedido, em PDF."""

    def get(self, request, token):
        pedido = _buscar(token)
        base = f'{request.scheme}://{request.get_host()}'
        pdf = PedidoPdfService.gerar(pedido, base_url=base)

        resposta = HttpResponse(pdf, content_type='application/pdf')
        # Inline: o cliente abre no navegador do celular sem baixar nada,
        # que é o comportamento esperado de um link no WhatsApp.
        resposta['Content-Disposition'] = (
            f'inline; filename="PED-{pedido.numero:06d}.pdf"'
        )
        _blindar(resposta)
        return resposta


class PedidoResponderView(View):
    """
    O aceite (ou o pedido de ajuste) do cliente — o passo 10 do fluxo.

    ÚNICA ESCRITA PÚBLICA DO VERTICAL, e o raio dela é mínimo de propósito:
    grava a resposta numa tabela própria e não toca em preço, grade, arte
    nem status de produção. Quem tem o link responde sobre O PRÓPRIO pedido
    e mais nada — a busca continua sendo só por token.

    CSRF normal do Django: o formulário sai da página com `{% csrf_token %}`
    e o cookie de sessão vem junto, como no cardápio digital. Não há
    `csrf_exempt` aqui.
    """

    def post(self, request, token):
        pedido = _buscar(token)
        aprovacao = getattr(pedido, 'aprovacao', None)

        # Sem liberação interna não há o que responder: o pedido ainda não
        # foi apresentado ao cliente.
        if aprovacao is None or not aprovacao.liberado:
            raise Http404('Pedido não encontrado.')

        resposta = request.POST.get('resposta')
        if resposta not in (AprovacaoPedido.Resposta.APROVADO,
                            AprovacaoPedido.Resposta.AJUSTE):
            raise Http404('Resposta inválida.')

        # O NOME É A ASSINATURA do aceite. O formulário já o exige; chegar
        # aqui vazio é chamada fora da tela, e gravar um aceite anônimo não
        # serve no dia em que alguém pergunta quem aprovou.
        nome = (request.POST.get('nome') or '').strip()
        if not nome:
            return redirect(
                reverse('moda_publico:pedido', args=[token]) + '?falta=nome'
            )

        aprovacao.responder(
            resposta=resposta,
            nome=nome,
            ip=get_client_ip(request),
            motivo=request.POST.get('motivo', '')[:2000],
        )
        # Redirect depois do POST: sem isso, atualizar a página reenviaria a
        # resposta, e o cliente veria "aprovado" virar "ajuste" sem entender.
        return redirect(reverse('moda_publico:pedido', args=[token]))
