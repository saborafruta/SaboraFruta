"""
Leitor de QR, imagem do QR e etiqueta para imprimir.

O FLUXO DO CHÃO DE FÁBRICA é o que desenhou este arquivo: o operador aponta
a câmera do celular para a ficha, o próprio sistema operacional abre o
navegador, e ele cai NA OP. Sem app, sem digitar número, sem procurar na
lista. Por isso o QR guarda uma URL — é o único conteúdo que a câmera
nativa de qualquer aparelho sabe abrir sozinha.

A URL é `/q/<codigo>/`, curta de propósito e fora de `/moda/`: menos
caracteres é menos densidade no desenho, e um QR menos denso é lido de mais
longe, com a câmera suja e a luz ruim que a fábrica tem.
"""
import io

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE

from .services.qr import DOCUMENTOS, limpar, resolver
from .views import ModaBaseView


def url_do_qr(request, objeto) -> str:
    """O endereço absoluto que vai dentro do desenho."""
    return request.build_absolute_uri(
        reverse('qr:abrir', args=[objeto.codigo_qr]),
    )


class QrAbrirView(View):
    """
    O destino do escaneamento: descobre o documento e manda para a tela dele.

    NÃO herda `ModaBaseView` de propósito, e é a única exceção do vertical
    junto com as views públicas. O `PermissaoRequiredMixin` manda o anônimo
    para o login SEM `next`, e aqui isso arruinaria o fluxo inteiro: o
    operador escaneia, faz login e cai no painel, tendo que procurar a OP na
    mão — exatamente o que o QR existe para evitar. Com `redirect_to_login`
    ele volta para o código depois de entrar.

    "Mostrar as informações autorizadas" acontece sozinho: esta view só
    redireciona. Quem decide o que ele vê é a tela de destino, com a mesma
    permissão de sempre. O QR é atalho para a porta, não uma porta nova.
    """

    def get(self, request, codigo):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        documento, objeto = resolver(codigo)
        if documento is None:
            return _nao_encontrado(request, codigo)

        if not request.user.tem_permissao('moda', documento.acao):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('core:dashboard')

        # Documento de outra filial: a tela de destino devolveria 404, o que
        # é certo mas mudo. Quem tem acesso àquela filial precisa ouvir que
        # só falta trocar; quem não tem não pode nem saber que o documento
        # existe.
        ativa = getattr(request, 'filial_ativa', None)
        if ativa is None or objeto.filial_id != ativa.pk:
            if not request.user.pode_acessar_filial(objeto.filial):
                return _nao_encontrado(request, codigo)
            messages.warning(
                request,
                f'{documento.label} {objeto} é da filial {objeto.filial} — '
                f'troque de filial para abrir.',
            )
            return redirect('core:selecionar-filial')

        rota, args = documento.url(objeto)
        return redirect(reverse(rota, args=args))


def _nao_encontrado(request, codigo):
    """
    Tela de código inválido, e não 404 cru.

    O operador que escaneou uma etiqueta velha ou digitou errado precisa de
    um caminho de volta — o 404 do sistema não tem nenhum.
    """
    resposta = render(request, 'moda/qr_invalido.html', {
        'title': 'Código não encontrado',
        'codigo': limpar(codigo),
        'documentos': DOCUMENTOS,
    }, status=404)
    return resposta


class QrImagemView(ModaBaseView):
    """
    O desenho do QR em PNG.

    Gerado sob demanda e não guardado: a imagem é derivada do código, e um
    arquivo salvo seria mais uma coisa para sincronizar sem ganho nenhum.
    Mesmo desenho da etiqueta de volume da expedição.
    """

    def get(self, request, codigo):
        import qrcode

        documento, objeto = resolver(codigo)
        if documento is None:
            raise Http404('Código não encontrado.')
        _exigir_filial(request, objeto)

        imagem = qrcode.make(url_do_qr(request, objeto))
        buffer = io.BytesIO()
        imagem.save(buffer, format='PNG')
        return HttpResponse(buffer.getvalue(), content_type='image/png')


class QrEtiquetaView(ModaBaseView):
    """
    A etiqueta pronta para imprimir e colar no fardo, na ficha ou na capa da OP.

    Página própria em vez de modal: o que vai para a parede da fábrica é
    impresso, e imprimir a tela do pedido inteira gastaria cinco folhas para
    entregar um quadrado.
    """

    def get(self, request, codigo):
        documento, objeto = resolver(codigo)
        if documento is None:
            raise Http404('Código não encontrado.')
        _exigir_filial(request, objeto)

        rota, args = documento.url(objeto)
        return render(request, 'moda/qr_etiqueta.html', {
            'title': f'QR Code — {documento.label}',
            'documento': documento,
            'objeto': objeto,
            'codigo': objeto.codigo_qr,
            'url_destino': reverse(rota, args=args),
            'linhas': _linhas(documento, objeto),
        })


class QrEscanearView(ModaBaseView):
    """
    Busca pelo código digitado ou lido por leitor de mão.

    A câmera do celular resolve o caso comum, mas o chão de fábrica também
    tem leitor de bancada (que se comporta como teclado) e etiqueta rasgada
    que só dá para ler o número. Sem este campo, esses dois casos não teriam
    saída nenhuma.
    """

    def get(self, request):
        codigo = request.GET.get('codigo', '')
        if not codigo:
            return render(request, 'moda/qr_escanear.html', {
                'title': 'Escanear código',
                'documentos': DOCUMENTOS,
            })
        return redirect(reverse('qr:abrir', args=[limpar(codigo)]))


def _exigir_filial(request, objeto) -> None:
    """Documento de outra filial não existe para quem está aqui."""
    ativa = getattr(request, 'filial_ativa', None)
    if ativa is None or objeto.filial_id != ativa.pk:
        raise Http404('Código não encontrado.')


def _linhas(documento, objeto) -> list[tuple[str, str]]:
    """
    O que a etiqueta escreve embaixo do desenho.

    Sem isto a etiqueta seria um quadrado anônimo, e quem está com a ficha
    na mão precisa saber o que ela é ANTES de escanear — inclusive para
    decidir se vale escanear.
    """
    if documento.prefixo == 'PED':
        return [
            ('Pedido', f'#{objeto.numero:06d}'),
            ('Cliente', str(objeto.cliente)),
            ('Entrega', f'{objeto.data_prevista_entrega:%d/%m/%Y}'
                        if objeto.data_prevista_entrega else 'a combinar'),
        ]
    if documento.prefixo == 'OP':
        return [
            ('Ordem', objeto.numero),
            ('Produto', objeto.descricao_produto),
            ('Quantidade', f'{objeto.quantidade} peças'),
            ('Prazo', f'{objeto.prazo:%d/%m/%Y}' if objeto.prazo else 'sem prazo'),
        ]
    if documento.prefixo == 'FT':
        return [
            ('Produto', str(objeto.produto)),
            ('Versão', f'v{objeto.versao}'),
            ('Situação', objeto.get_status_display()),
        ]
    return [
        ('Corte', f'#{objeto.numero:04d}'),
        ('Ordem', objeto.ordem.numero),
        ('Lote do tecido', objeto.lote or '—'),
        ('Quantidade', f'{objeto.quantidade} peças'),
    ]
