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
from django.db import transaction
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


def _quem(usuario) -> str:
    """
    O nome de quem confere, num lugar só.

    `get_full_name()` NÃO existe neste `Usuario` -- e era o que estava aqui:
    qualquer conferência salva estourava `AttributeError` antes de gravar. O
    campo do modelo é `nome`, e o e-mail entra como último recurso para a
    coluna nunca ficar vazia sem motivo.
    """
    return (getattr(usuario, 'nome', '') or getattr(usuario, 'email', '') or '')[:80]


def _linhas_de_quantidade(expedicao) -> list[dict]:
    """
    O que deveria estar na caixa, tamanho a tamanho, com o que já foi contado.

    POR QUANTIDADE E POR PESSOA SÃO PERGUNTAS DIFERENTES, e por isso as duas
    aparecem. Pedido de uniforme com nome e número precisa da lista de
    pessoas; pedido de 20 camisas lisas -- que é a maioria -- não tem pessoa
    nenhuma, e antes caía numa tela vazia dizendo "não tem personalização",
    como se não houvesse o que conferir.
    """
    conferido = {i.tamanho_id: i.quantidade for i in expedicao.conferencia.all()}
    linhas = []
    for celula in expedicao.grade_esperada:
        if not celula.quantidade:
            continue
        linhas.append({
            'tamanho': celula.tamanho,
            'esperado': celula.quantidade,
            'conferido': conferido.get(celula.tamanho_id, 0),
        })
    return linhas


def _artes(expedicao) -> list[dict]:
    """
    A arte do pedido, para conferir a peça CONTRA o que foi aprovado.

    Sem isso a conferência é só contagem: o número fecha e ninguém percebeu
    que o escudo saiu na manga errada. Quem confere está com a peça na mão --
    o que falta na tela é o desenho ao lado dela.

    Vem das DUAS origens, porque as duas existem: o acervo do pedido (o
    layout que o cliente mandou, a referência) e a arte APLICADA em cada
    item, que é a que diz técnica e local. Mostrar só uma faria a tela
    parecer vazia justamente nos pedidos em que a outra foi usada.

    Só o que o navegador desenha vira miniatura. CDR, AI e PDF viram link --
    um <img> apontando para eles dá um quadrado quebrado, que é pior do que
    assumir que não há prévia.
    """
    pedido = expedicao.pedido
    if pedido is None:
        return []

    artes = []
    for arquivo in pedido.arquivos.all():
        if not arquivo.arquivo:
            continue
        artes.append({
            'url': arquivo.arquivo.url,
            'titulo': arquivo.descricao or arquivo.nome_arquivo,
            'detalhe': arquivo.get_tipo_display(),
            'imagem': arquivo.pode_pre_visualizar,
            'tamanho': arquivo.tamanho_legivel,
        })

    # A expedição é de UMA ordem, e a ordem é de UM item. Misturar aqui as
    # artes aplicadas aos outros produtos do mesmo pedido fazia a pessoa
    # comparar a peça desta caixa com a personalização de outra caixa.
    item = expedicao.ordem.item
    for personalizacao in item.personalizacoes.all():
        if not personalizacao.arquivo:
            continue
        artes.append({
            'url': personalizacao.arquivo.url,
            'titulo': personalizacao.nome_arquivo,
            'detalhe': f'{item.nome_exibicao} · {personalizacao}',
            'imagem': personalizacao.pode_pre_visualizar,
            'tamanho': '',
        })

    # Imagem primeiro: a miniatura é o que serve para comparar, e empurrar
    # os links para o fim deixa a comparação visível sem rolar.
    artes.sort(key=lambda a: not a['imagem'])
    return artes


def _pessoas(expedicao):
    """
    As pessoas do PRODUTO desta expedição, do menor tamanho para o maior.

    Sem pedido não há pessoas: expedição de ordem avulsa existe e a tela
    precisa dizer isso, em vez de mostrar uma lista vazia que parece defeito.
    """
    pedido = expedicao.pedido
    if pedido is None:
        return PersonalizacaoIndividual.objects.none()
    return (
        PersonalizacaoIndividual.objects
        .filter(pedido=pedido, item=expedicao.ordem.item)
        .select_related('item', 'tamanho')
        .order_by('tamanho__ordem', 'tamanho__sigla', 'ordem', 'id')
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

        quantidades = _linhas_de_quantidade(expedicao)
        demais_expedicoes = []
        pedido_id = expedicao.ordem.pedido_id
        if pedido_id:
            demais_expedicoes = list(
                Expedicao.objects.for_filial(_filial(request))
                .filter(ordem__pedido_id=pedido_id)
                .exclude(pk=expedicao.pk)
                .exclude(status=Expedicao.Status.CANCELADA)
                .select_related('ordem__item')
                .order_by('numero')
            )
        linhas = [{'pessoa': p, 'conferido': p.pk in conferidas} for p in pessoas]
        pessoas_por_tamanho = {}
        for linha in linhas:
            pessoas_por_tamanho.setdefault(linha['pessoa'].tamanho_id, []).append(linha)
        for quantidade in quantidades:
            quantidade['pessoas'] = pessoas_por_tamanho.get(quantidade['tamanho'].pk, [])

        return render(request, 'moda/conferencia_pessoas.html', {
            'title': f'Conferência — Expedição #{expedicao.numero:04d}',
            'expedicao': expedicao,
            'linhas': linhas,
            'total': len(pessoas),
            'conferidas': sum(1 for p in pessoas if p.pk in conferidas),
            'quantidades': quantidades,
            'demais_expedicoes': demais_expedicoes,
            'artes': _artes(expedicao),
            'esperado_total': sum(l['esperado'] for l in quantidades),
            'conferido_total': sum(l['conferido'] for l in quantidades),
            # Depois da separação a conferência não se mexe mais: o que foi
            # separado saiu da bancada, e reabrir aqui daria uma conferência
            # que não corresponde à caixa.
            'travada': expedicao.passou_por(Expedicao.Status.SEPARACAO),
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })


class ConferenciaPessoasSalvarView(ModaBaseView):
    """
    Grava a conferência inteira num POST só: quantidade, pessoas e assinatura.

    UM FORMULÁRIO, e não três. Quem confere está de pé ao lado da caixa com
    o cliente esperando; salvar em três passos é três chances de sair da
    tela no meio e deixar metade registrada. O que veio marcado fica, o que
    não veio sai.
    """

    area = 'expedicao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        expedicao = _expedicao(request, pk)
        volta = redirect(reverse('moda:conferencia-pessoas', args=[expedicao.pk]))

        if expedicao.passou_por(Expedicao.Status.SEPARACAO):
            messages.error(request, 'A conferência desta expedição já foi fechada.')
            return volta

        contadas = self._quantidades(request, expedicao)
        marcadas = self._pessoas(request, expedicao)
        assinou = self._assinatura(request, expedicao)

        partes = []
        if contadas is not None:
            partes.append(f'{contadas} peça(s) contada(s)')
        if marcadas is not None:
            partes.append(f'{marcadas} pessoa(s) conferida(s)')
        if assinou:
            partes.append(f'recebimento assinado por {expedicao.recebido_por}')

        if partes:
            messages.success(request, 'Conferência gravada: ' + ', '.join(partes) + '.')
        return volta

    # ── Por quantidade ───────────────────────────────────────────────────

    @staticmethod
    def _quantidades(request, expedicao):
        """
        As caixinhas por tamanho. Devolve o total contado, ou None se a tela
        não mandou nenhuma -- que é diferente de mandar tudo zerado.

        SÓ TAMANHOS DA GRADE entram. O `name` do campo vem do HTML, e um
        `qtd_999` forjado gravaria conferência de um tamanho que não está
        neste pedido.
        """
        validos = {c.tamanho_id for c in expedicao.grade_esperada}
        quantidades = {}
        for chave, valor in request.POST.items():
            if not chave.startswith('qtd_'):
                continue
            id_tamanho = chave[4:]
            if not id_tamanho.isdigit() or int(id_tamanho) not in validos:
                continue
            valor = (valor or '').strip()
            quantidades[int(id_tamanho)] = int(valor) if valor.isdigit() else 0

        if not quantidades:
            return None

        try:
            return ExpedicaoService.conferir(
                expedicao, quantidades,
                {'conferido_por': _quem(request.user)},
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return None

    # ── Por pessoa ───────────────────────────────────────────────────────

    @staticmethod
    def _pessoas(request, expedicao):
        validas = set(_pessoas(expedicao).values_list('id', flat=True))
        if not validas:
            return None

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
        quem = _quem(request.user)
        ConferenciaPessoa.objects.bulk_create([
            ConferenciaPessoa(
                expedicao=expedicao, individual_id=pk_pessoa, conferido_por=quem,
            )
            for pk_pessoa in marcadas - ja_tem
        ])
        return len(marcadas)

    # ── Assinatura ───────────────────────────────────────────────────────

    @staticmethod
    def _assinatura(request, expedicao) -> bool:
        """
        Só grava se o cliente assinou de fato.

        Nome sem traço TAMBÉM vale: em entrega no balcão o cliente às vezes
        só confere e diz o nome, e recusar isso empurraria a pessoa a
        rabiscar qualquer coisa para o botão liberar -- que é pior registro
        do que um nome honesto.
        """
        nome = (request.POST.get('recebido_por') or '').strip()
        traco = request.POST.get('assinatura') or ''
        if not nome and not traco:
            return False
        if not nome:
            messages.error(request, 'Informe o nome de quem está recebendo.')
            return False

        try:
            ExpedicaoService.assinar(
                expedicao, nome, traco,
                documento=request.POST.get('assinado_documento') or '',
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return False
        return True


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

    Abre diretamente a conferência. O QR e as opções de impressão são um
    recurso separado da OP, para não interromper quem já está com o cliente.
    """

    area = 'comercial'

    def get(self, request, pk):
        from .models import PedidoProducao

        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente'),
            pk=pk,
        )
        expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .filter(ordem__pedido=pedido)
            .exclude(status=Expedicao.Status.CANCELADA)
            .select_related(
                'ordem__item__produto', 'ordem__item__grade_tamanho',
            )
            .order_by('numero')
        )

        itens_com_expedicao = {e.ordem.item_id for e in expedicoes}
        itens_do_pedido = set(pedido.itens.values_list('id', flat=True))
        if itens_com_expedicao != itens_do_pedido:
            # ANTES ISTO ERA UM BECO: a mensagem explicava por que não dava e
            # devolvia a pessoa ao pedido, sem caminho nenhum. Agora ela vê o
            # que está travando e decide — o sistema informa, não impede.
            return PedidoConferenciaForcarView().post(request, pk)

        if len(expedicoes) == 1:
            return redirect(reverse('moda:conferencia-pessoas', args=[expedicoes[0].pk]))

        # Cada produto tem a sua expedição. Sem escolher uma caixa ao acaso,
        # a fila já abre as conferências disponíveis diretamente.
        return redirect(reverse('moda:conferencia-fila'))

    @staticmethod
    def _perguntar(request, pedido):
        """Explica as pendências quando ainda não existe expedição."""
        from .services.validacao import OK, ValidacaoProducao

        checagens = ValidacaoProducao.checar(pedido)
        return render(request, 'moda/conferencia_forcar.html', {
            'title': f'Conferir pedido #{pedido.numero:06d}',
            'pedido': pedido,
            'bloqueios': [c for c in checagens if c.bloqueia],
            'avisos': [c for c in checagens if not c.bloqueia and c.situacao != OK],
            'pode_forcar': request.user.tem_permissao('moda', 'aprovar'),
        })


class PedidoConferenciaQrView(ModaBaseView):
    """QR e impressão das caixas: aberto só pelo botão próprio da OP."""

    area = 'comercial'

    def get(self, request, pk):
        from .models import PedidoProducao

        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente'),
            pk=pk,
        )
        expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .filter(ordem__pedido=pedido)
            .exclude(status=Expedicao.Status.CANCELADA)
            .select_related('ordem__item__produto', 'ordem__item__grade_tamanho')
            .order_by('numero')
        )
        if not expedicoes:
            messages.error(request, 'Ainda não há uma expedição para imprimir o QR Code.')
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        return render(request, 'moda/conferencia_qr.html', {
            'title': f'Conferir pedido #{pedido.numero:06d}',
            'pedido': pedido,
            'expedicoes': expedicoes,
        })

class PedidoConferenciaForcarView(ModaBaseView):
    """
    Abre a conferência por cima das pendências — o desvio, com dono.

    Existe porque o caminho normal é longo e nem toda casa o percorre
    inteiro: há pedido que sai da costura direto para a caixa, e a tela que
    só sabia dizer "não" empurrava essa pessoa para o Django Admin, onde não
    há validação nenhuma nem registro de quem fez o quê.

    O desvio deixa RASTRO: a observação da expedição e a da ordem dizem que
    foram abertas por cima de N pendências, com o nome de quem mandou e a
    lista do que foi ignorado. Um atalho sem rastro é o que ninguém consegue
    explicar seis meses depois.
    """

    area = 'comercial'
    # `aprovar`, e não `criar`: pular a validação da produção é a mesma
    # gravidade de liberar um pedido, e quem não pode liberar não pode
    # contornar a liberação por outra porta.
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        from .models import PedidoProducao
        from .services import ExpedicaoService
        from .services.validacao import ValidacaoProducao

        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        bloqueios = [c for c in ValidacaoProducao.checar(pedido) if c.bloqueia]
        marca = self._marca(request.user, bloqueios)

        try:
            with transaction.atomic():
                ordens = self._ordens(pedido, request.user, marca)
                expedicoes = [
                    ExpedicaoService.criar(
                        pedido.filial, ordem, usuario=request.user,
                        forcar=True, observacao=marca,
                    )
                    for ordem in ordens
                ]
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        messages.warning(
            request,
            f'Conferência aberta por cima de {len(bloqueios)} pendência(s). '
            f'O registro ficou na expedição, com o seu nome.',
        )
        todas_expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .filter(ordem__pedido=pedido)
            .exclude(status=Expedicao.Status.CANCELADA)
            .order_by('numero')
        )
        if todas_expedicoes:
            return redirect(
                reverse('moda:conferencia-pessoas', args=[todas_expedicoes[0].pk])
            )
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

    @staticmethod
    def _marca(usuario, bloqueios) -> str:
        """O texto que fica gravado — o rastro do desvio."""
        if not bloqueios:
            return ''
        itens = '; '.join(c.label for c in bloqueios)
        quem = getattr(usuario, 'nome', None) or getattr(usuario, 'email', '?')
        return (
            f'Conferência aberta por {quem} por cima de {len(bloqueios)} '
            f'pendência(s): {itens}.'
        )

    @staticmethod
    def _ordens(pedido, usuario, marca):
        """
        As ordens do pedido — as que existem, ou as que forem criadas.

        Reaproveita a ordem já aberta em vez de emitir outra: o pedido pode
        estar travado só na expedição, e emitir de novo colocaria a mesma
        peça duas vezes na fila da fábrica.
        """
        from .models import OrdemProducao
        from .services import OrdemProducaoService

        existentes = list(
            OrdemProducao.objects.filter(pedido=pedido)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
        )
        itens_com_ordem = {ordem.item_id for ordem in existentes}
        tem_item_sem_ordem = pedido.itens.exclude(pk__in=itens_com_ordem).exists()
        if tem_item_sem_ordem:
            criadas = OrdemProducaoService.gerar_do_pedido(
                pedido, usuario=usuario, forcar=True,
            )
        else:
            criadas = []
        if marca:
            for ordem in criadas:
                ordem.observacoes = (
                    f'{ordem.observacoes}\n{marca}'.strip()
                    if ordem.observacoes else marca
                )
                ordem.save(update_fields=['observacoes'])
        ordens = existentes + criadas
        ordens_com_expedicao = set(
            Expedicao.objects.filter(ordem__in=ordens)
            .exclude(status=Expedicao.Status.CANCELADA)
            .values_list('ordem_id', flat=True)
        )
        return [ordem for ordem in ordens if ordem.pk not in ordens_com_expedicao]


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
