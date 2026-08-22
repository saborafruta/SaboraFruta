"""
Conferência por PESSOA e o aceite de entrega do cliente.

A conferência que já existia conta quantas peças de cada TAMANHO saíram, e é
o que fecha contra a ordem. Num pedido de time isso não basta: a contagem
por tamanho pode fechar e a camisa do Lucas ter ficado para trás, porque a
peça dele tem nome e número e não é intercambiável com nenhuma outra.

A TELA É PARA O CELULAR. Quem confere está de pé, ao lado da caixa, com o
telefone numa mão. Por isso a lista é grande, o toque marca a linha inteira
e o contador fica sempre à vista; o QR na tela da expedição existe para
abrir isto no aparelho sem digitar endereço.

O ACEITE É DO CLIENTE, e por isso mora numa página pública, alcançada pelo
código da expedição — a mesma ideia do link de aprovação do pedido. Quem
confere é a casa; quem assina o recebimento é quem recebeu.
"""
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.services.exceptions import DomainError

from .models import ConferenciaPessoa, Expedicao, PersonalizacaoIndividual
from .services import ExpedicaoService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _expedicao(request, pk) -> Expedicao:
    return get_object_or_404(
        Expedicao.objects.for_filial(_filial(request))
        .select_related('ordem__pedido__cliente'),
        pk=pk,
    )


def _pessoas(expedicao):
    """
    As pessoas do PEDIDO desta expedição, na ordem em que foram lançadas.

    Sem pedido não há pessoas: expedição de ordem avulsa existe e a tela
    precisa dizer isso, em vez de mostrar uma lista vazia que parece defeito.
    """
    pedido = expedicao.pedido
    if pedido is None:
        return PersonalizacaoIndividual.objects.none()
    return (
        PersonalizacaoIndividual.objects
        .filter(pedido=pedido)
        .select_related('item', 'tamanho')
        .order_by('ordem', 'id')
    )


class ConferenciaPessoasView(ModaBaseView):
    """A lista de pessoas para conferir, peça a peça."""

    area = 'expedicao'

    def get(self, request, pk):
        expedicao = _expedicao(request, pk)
        conferidas = set(
            expedicao.conferencia_pessoas.values_list('individual_id', flat=True)
        )
        pessoas = list(_pessoas(expedicao))

        return render(request, 'moda/conferencia_pessoas.html', {
            'title': f'Conferência — Expedição #{expedicao.numero:04d}',
            'expedicao': expedicao,
            'linhas': [
                {'pessoa': p, 'conferido': p.pk in conferidas} for p in pessoas
            ],
            'total': len(pessoas),
            'conferidas': sum(1 for p in pessoas if p.pk in conferidas),
            # Depois da separação a conferência não se mexe mais: o que foi
            # separado saiu da bancada, e reabrir aqui daria uma conferência
            # que não corresponde à caixa.
            'travada': expedicao.passou_por(Expedicao.Status.SEPARACAO),
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })


class ConferenciaPessoasSalvarView(ModaBaseView):
    """Grava a marcação — o que veio marcado fica, o resto sai."""

    area = 'expedicao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        expedicao = _expedicao(request, pk)
        if expedicao.passou_por(Expedicao.Status.SEPARACAO):
            messages.error(request, 'A conferência desta expedição já foi fechada.')
            return redirect(reverse('moda:conferencia-pessoas', args=[expedicao.pk]))

        validas = set(_pessoas(expedicao).values_list('id', flat=True))
        marcadas = {
            int(v) for v in request.POST.getlist('pessoa') if v.isdigit()
        } & validas

        # O FORMULÁRIO INTEIRO manda o estado, então o que sumiu foi
        # desmarcado. Apagar antes de criar evita ter de comparar linha a
        # linha, e a marcação é barata de refazer.
        expedicao.conferencia_pessoas.exclude(individual_id__in=marcadas).delete()
        ja_tem = set(
            expedicao.conferencia_pessoas.values_list('individual_id', flat=True)
        )
        quem = (request.user.get_full_name() or request.user.email or '')[:80]
        ConferenciaPessoa.objects.bulk_create([
            ConferenciaPessoa(
                expedicao=expedicao, individual_id=pk_pessoa, conferido_por=quem,
            )
            for pk_pessoa in marcadas - ja_tem
        ])

        total = len(validas)
        messages.success(
            request, f'Conferência gravada: {len(marcadas)} de {total} peça(s).',
        )
        return redirect(reverse('moda:conferencia-pessoas', args=[expedicao.pk]))


class ConferenciaQrView(ModaBaseView):
    """
    O QR que abre esta conferência no celular.

    Aponta para a tela INTERNA: quem confere é a casa, e a tela pede login
    como qualquer outra. O QR poupa a digitação do endereço, não a senha.
    """

    area = 'expedicao'

    def get(self, request, pk):
        import qrcode
        from io import BytesIO

        expedicao = _expedicao(request, pk)
        destino = request.build_absolute_uri(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )
        buffer = BytesIO()
        qrcode.make(destino).save(buffer, 'PNG')
        return HttpResponse(buffer.getvalue(), content_type='image/png')


# ══════════════════════════════════════════════════════════════════════
# ACEITE DE ENTREGA — página pública, sem login
# ══════════════════════════════════════════════════════════════════════

class EntregaPublicaView(View):
    """
    Onde o cliente confirma que recebeu.

    Achada pelo CÓDIGO da expedição, que já é um token opaco criado para
    leitura — não pelo número sequencial, que qualquer um adivinha. É a
    mesma ideia do link de aprovação do pedido: quem tem o endereço responde
    sobre aquela entrega e mais nada.

    A escrita é mínima: grava quem recebeu e a data, pelo mesmo serviço que
    a tela interna usa. Nada de preço, grade ou status de produção.
    """

    def get(self, request, codigo):
        expedicao = self._buscar(codigo)
        return self._tela(request, expedicao)

    def post(self, request, codigo):
        expedicao = self._buscar(codigo)

        nome = (request.POST.get('recebido_por') or '').strip()
        if not nome:
            return self._tela(request, expedicao, erro='Informe seu nome para confirmar.')

        try:
            ExpedicaoService.avancar(expedicao, None, {'recebido_por': nome})
        except DomainError as erro:
            return self._tela(request, expedicao, erro=str(erro))

        return redirect(reverse('moda_publico:entrega', args=[codigo]))

    @staticmethod
    def _buscar(codigo: str) -> Expedicao:
        # `all_objects` porque não há filial ativa numa requisição sem login
        # — o escopo aqui é o código, que já é de uma expedição só.
        if not codigo or len(codigo) < 10:
            raise Http404('Entrega não encontrada.')
        expedicao = (
            Expedicao.all_objects
            .select_related('ordem__pedido__cliente', 'filial', 'filial__empresa')
            .filter(codigo=codigo).first()
        )
        if expedicao is None or expedicao.cancelada:
            raise Http404('Entrega não encontrada.')
        return expedicao

    @staticmethod
    def _tela(request, expedicao, erro: str = ''):
        resposta = render(request, 'moda/publico/entrega.html', {
            'expedicao': expedicao,
            'pedido': expedicao.pedido,
            'empresa': expedicao.filial.empresa,
            'volumes': expedicao.volumes.all(),
            'entregue': expedicao.entregue,
            # Só dá para receber o que já saiu para entrega. Antes disso a
            # página mostra o andamento, e não um botão que vai recusar.
            'pode_receber': expedicao.status == Expedicao.Status.DESPACHO,
            'erro': erro,
            'agora': timezone.now(),
        })
        # Mesma blindagem do link do pedido: `same-origin` e não
        # `no-referrer`, senão o POST sai com `Origin: null` e o Django
        # recusa como CSRF -- foi o que já derrubou a aprovação do cliente.
        resposta['Cache-Control'] = 'private, no-store'
        resposta['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        resposta['Referrer-Policy'] = 'same-origin'
        return resposta


class PedidoConferenciaView(ModaBaseView):
    """
    O atalho do PEDIDO para a conferência — o botão que aparece em "Pronto".

    Quem está no pedido não deveria ter de saber que a expedição nasce da
    ORDEM, nem procurar em Expedição > Separação qual das expedições é
    daquele pedido. O caminho existia; o que faltava era a porta.

    Uma expedição: vai direto para a conferência dela. Mais de uma -- pedido
    com vários produtos, cada um com sua ordem -- leva para a lista, porque
    escolher por conta própria mandaria conferir a caixa errada.
    """

    area = 'comercial'

    def get(self, request, pk):
        from .models import PedidoProducao

        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .filter(ordem__pedido=pedido)
            .exclude(status=Expedicao.Status.CANCELADA)
            .order_by('numero')
        )

        if not expedicoes:
            messages.error(
                request,
                'Este pedido ainda não tem expedição aberta. Ela nasce da ordem '
                'de produção, depois que a etapa de qualidade é encerrada.',
            )
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        if len(expedicoes) == 1:
            return redirect(
                reverse('moda:conferencia-pessoas', args=[expedicoes[0].pk])
            )

        messages.info(
            request,
            f'Este pedido tem {len(expedicoes)} expedições — escolha qual conferir.',
        )
        return redirect(reverse('moda:expedicao-list'))


class ConferenciaFilaView(ModaBaseView):
    """
    A fila de quem confere — checagem antes de fechar o volume.

    Não é a lista de expedições com um filtro por status: aquela responde
    "onde está cada documento", e esta responde "o que falta conferir".
    Por isso cada linha traz as DUAS contagens que hoje existem — a de
    peças por tamanho, que fecha contra a ordem, e a de pessoas, que diz
    se a peça de cada um foi separada.

    AS DUAS PRECISAM APARECER JUNTAS porque uma pode fechar com a outra
    aberta: a contagem por tamanho bate e ainda assim a camisa do Lucas
    ficou para trás. Quem confere precisa ver isso sem abrir o documento.

    Entram as expedições em CONFERÊNCIA e as que acabaram de sair da
    produção: a fila de trabalho é o que chegou e o que está em cima da
    bancada, não o que já foi embalado.
    """

    area = 'expedicao'

    ETAPAS_DA_FILA = (
        Expedicao.Status.PRODUCAO_CONCLUIDA,
        Expedicao.Status.CONFERENCIA,
    )

    def get(self, request):
        expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .filter(status__in=self.ETAPAS_DA_FILA)
            .select_related('ordem__pedido__cliente', 'ordem__item')
            .prefetch_related('conferencia', 'conferencia_pessoas')
            .order_by('numero')
        )

        linhas = []
        for expedicao in expedicoes:
            pessoas = _pessoas(expedicao)
            total_pessoas = pessoas.count()
            conferidas = expedicao.conferencia_pessoas.count()
            linhas.append({
                'expedicao': expedicao,
                'esperado': expedicao.quantidade_esperada,
                'conferido': expedicao.quantidade_conferida,
                'divergencia': expedicao.divergencia_conferencia,
                'fecha': expedicao.conferencia_fecha,
                'total_pessoas': total_pessoas,
                'pessoas_conferidas': conferidas,
                # Só é pendência de pessoa quando HÁ pessoas: pedido sem
                # personalização individual não fica em falta por algo que
                # nunca existiu.
                'pessoas_faltando': (
                    total_pessoas - conferidas if total_pessoas else 0
                ),
            })

        return render(request, 'moda/conferencia_fila.html', {
            'title': 'Conferência',
            'linhas': linhas,
            'resumo': {
                'na_fila': len(linhas),
                'aguardando': sum(
                    1 for l in linhas
                    if l['expedicao'].status == Expedicao.Status.PRODUCAO_CONCLUIDA
                ),
                'divergentes': sum(1 for l in linhas if not l['fecha']),
                'pessoas_faltando': sum(1 for l in linhas if l['pessoas_faltando']),
            },
        })


class EmbalagemFilaView(ModaBaseView):
    """
    A fila de quem embala — volumes e etiquetas de envio.

    A pergunta desta bancada é uma só: TODA PEÇA CONFERIDA ESTÁ DENTRO DE
    UMA CAIXA? Por isso cada linha compara as peças nos volumes com as
    conferidas, e não com a grade da ordem: o que se embala é o que foi
    conferido, e a divergência contra a ordem é assunto da etapa anterior.

    Peça conferida fora de volume é a que fica na bancada e viaja no pedido
    seguinte -- some da caixa sem sumir do sistema, e o cliente reclama de
    falta enquanto a tela mostra tudo certo.

    VOLUME SEM PESO tem cartão próprio: transportadora cobra por peso, e
    volume sem peso informado vira cotação errada ou recusa na coleta.

    Entram as expedições em SEPARAÇÃO e em EMBALAGEM: o que foi separado
    espera caixa, e o que está em embalagem está na bancada.
    """

    area = 'expedicao'

    ETAPAS_DA_FILA = (
        Expedicao.Status.SEPARACAO,
        Expedicao.Status.EMBALAGEM,
    )

    def get(self, request):
        expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .filter(status__in=self.ETAPAS_DA_FILA)
            .select_related('ordem__pedido__cliente', 'ordem__item')
            .prefetch_related('volumes', 'conferencia')
            .order_by('numero')
        )

        linhas = []
        for expedicao in expedicoes:
            volumes = list(expedicao.volumes.all())
            nos_volumes = expedicao.pecas_nos_volumes
            conferidas = expedicao.quantidade_conferida
            linhas.append({
                'expedicao': expedicao,
                'volumes': volumes,
                'total_volumes': len(volumes),
                'nos_volumes': nos_volumes,
                'conferidas': conferidas,
                'fora_de_caixa': conferidas - nos_volumes,
                'fecha': expedicao.volumes_fecham,
                'peso': expedicao.peso_total,
                # Peso em branco é diferente de peso zero: o primeiro é
                # cadastro que falta, o segundo seria um volume sem massa.
                'sem_peso': sum(1 for v in volumes if v.peso_kg is None),
            })

        return render(request, 'moda/embalagem_fila.html', {
            'title': 'Embalagem',
            'linhas': linhas,
            'resumo': {
                'na_fila': len(linhas),
                'sem_volume': sum(1 for l in linhas if not l['total_volumes']),
                'nao_fecham': sum(1 for l in linhas if not l['fecha']),
                'sem_peso': sum(1 for l in linhas if l['sem_peso']),
            },
        })


class EntregaFilaView(ModaBaseView):
    """
    Saída e comprovação de entrega — a última bancada.

    Duas listas na mesma tela, e elas não são a mesma coisa: o que SAIU e
    ainda não teve confirmação, e o que já foi RECEBIDO. A primeira é
    trabalho em aberto; a segunda é comprovante.

    O SINAL QUE IMPORTA é há quantos dias a caixa saiu sem confirmação.
    Despachada ontem é normal; despachada há uma semana sem ninguém assinar
    quer dizer que ou o cliente não recebeu, ou recebeu e ninguém registrou
    -- e as duas hipóteses viram discussão no dia em que ele reclamar.

    Por isso cada linha em aberto traz o LINK do aceite pronto para copiar:
    a resposta mais comum para "saiu e não confirmou" é reenviar o link.
    """

    area = 'expedicao'

    def get(self, request):
        base = (
            Expedicao.objects.for_filial(_filial(request))
            .select_related('ordem__pedido__cliente', 'ordem__item')
            .prefetch_related('volumes')
        )
        hoje = timezone.localdate()

        a_caminho = []
        for expedicao in base.filter(status=Expedicao.Status.DESPACHO).order_by('numero'):
            saiu_em = (
                timezone.localtime(expedicao.data_despacho).date()
                if expedicao.data_despacho else None
            )
            a_caminho.append({
                'expedicao': expedicao,
                'saiu_em': saiu_em,
                'dias': (hoje - saiu_em).days if saiu_em else None,
                'link': request.build_absolute_uri(
                    reverse('moda_publico:entrega', args=[expedicao.codigo])
                ),
            })

        entregues = [
            {
                'expedicao': e,
                'recebido_em': e.data_entrega,
            }
            for e in base.filter(status=Expedicao.Status.ENTREGA).order_by('-data_entrega')[:50]
        ]

        return render(request, 'moda/entrega_fila.html', {
            'title': 'Entrega',
            'a_caminho': a_caminho,
            'entregues': entregues,
            'resumo': {
                'a_caminho': len(a_caminho),
                'entregues': len(entregues),
                # Uma semana é o corte: menos que isso ainda é trânsito
                # normal, mais que isso alguém precisa ligar.
                'atrasados': sum(
                    1 for l in a_caminho if l['dias'] is not None and l['dias'] >= 7
                ),
            },
        })
