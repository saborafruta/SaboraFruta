"""
Expedição (grupo Expedição).

A leitura de código tem rota própria e redireciona para o documento
encontrado: assim o leitor de código de barras — que digita e dá Enter —
funciona em qualquer tela do módulo sem JavaScript nenhum.
"""
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import Expedicao, OrdemProducao, Tamanho, Volume
from .services.expedicao import ExpedicaoService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _expedicao(request, pk) -> Expedicao:
    return get_object_or_404(
        Expedicao.objects.for_filial(_filial(request)).select_related(
            'ordem', 'ordem__pedido', 'ordem__pedido__cliente', 'ordem__item',
        ).prefetch_related(
            'conferencia__tamanho', 'volumes', 'ordem__item__grade__tamanho',
            'ordem__etapas',
        ),
        pk=pk,
    )


class ExpedicaoListView(ModaBaseView):
    def get(self, request):
        expedicoes = list(
            Expedicao.objects.for_filial(_filial(request))
            .select_related('ordem', 'ordem__pedido__cliente', 'ordem__item')
            .prefetch_related('volumes', 'conferencia')
        )

        status = (request.GET.get('status') or '').strip()
        visiveis = (
            [e for e in expedicoes if e.status == status]
            if status in Expedicao.Status.values else expedicoes
        )

        return render(request, 'moda/expedicao_list.html', {
            'title': 'Expedição',
            'expedicoes': visiveis,
            'resumo': ExpedicaoService.resumo(expedicoes),
            'candidatas': self._candidatas(request, expedicoes),
            'status_escolhido': status,
            'status_choices': Expedicao.Status.choices,
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })

    @staticmethod
    def _candidatas(request, expedicoes):
        """
        Ordens prontas para expedir: qualidade encerrada e sem expedição
        aberta. Oferecer ordem que ainda está na fábrica faria abrir
        documento para peça que não existe na doca.
        """
        ocupadas = {
            e.ordem_id for e in expedicoes
            if e.status not in (Expedicao.Status.ENTREGA, Expedicao.Status.CANCELADA)
        }
        prontas = []
        for ordem in (
            OrdemProducao.objects.for_filial(_filial(request))
            .exclude(status=OrdemProducao.Status.CANCELADA)
            .select_related('pedido__cliente', 'item')
            .prefetch_related('etapas')
        ):
            if ordem.pk in ocupadas:
                continue
            qualidade = next(
                (e for e in ordem.etapas.all() if e.etapa == 'qualidade'), None,
            )
            if qualidade is not None and qualidade.encerrada:
                prontas.append(ordem)
        return prontas


class ExpedicaoBuscarView(ModaBaseView):
    """Leitura de código de barras/QR — o campo que o leitor alimenta."""

    def get(self, request):
        resultado = ExpedicaoService.buscar(_filial(request), request.GET.get('codigo'))

        if resultado['achou']:
            expedicao = resultado['expedicao']
            messages.success(
                request,
                f'Expedição #{expedicao.numero:04d} — encontrada pelo {resultado["via"]}.',
            )
            return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))

        messages.error(request, resultado['erro'])
        return redirect(reverse('moda:expedicao-list'))


class ExpedicaoCriarView(ModaBaseView):
    permissao_acao = 'criar'

    def post(self, request, pk):
        ordem = get_object_or_404(
            OrdemProducao.objects.for_filial(_filial(request)).prefetch_related('etapas'),
            pk=pk,
        )
        try:
            expedicao = ExpedicaoService.criar(_filial(request), ordem, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:expedicao-list'))

        messages.success(request, f'Expedição #{expedicao.numero:04d} aberta.')
        return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))


class ExpedicaoDetailView(ModaBaseView):
    def get(self, request, pk):
        expedicao = _expedicao(request, pk)
        conferido = {i.tamanho_id: i.quantidade for i in expedicao.conferencia.all()}

        # Tamanhos da grade da ordem; sem grade, os da filial. A conferência
        # segue o que foi produzido, não o catálogo inteiro.
        celulas = list(expedicao.grade_esperada)
        if celulas:
            linhas = [
                {'tamanho': c.tamanho, 'esperado': c.quantidade,
                 'conferido': conferido.get(c.tamanho_id, 0)}
                for c in celulas
            ]
        else:
            linhas = [
                {'tamanho': t, 'esperado': 0, 'conferido': conferido.get(t.pk, 0)}
                for t in Tamanho.objects.for_filial(_filial(request)).filter(ativo=True)
            ]

        proxima = None
        if not expedicao.cancelada and not expedicao.entregue:
            proxima = expedicao.ETAPAS[expedicao.posicao + 1]

        return render(request, 'moda/expedicao_detail.html', {
            'title': f'Expedição #{expedicao.numero:04d}',
            'expedicao': expedicao,
            # Booleanos prontos: o template do Django nao chama metodo com
            # argumento, e espalhar a regra em `{% if status == 'x' or ... %}`
            # significaria repetir a ordem das etapas em dois lugares.
            'conferencia_travada': expedicao.passou_por(Expedicao.Status.SEPARACAO),
            'volumes_travados': expedicao.passou_por(Expedicao.Status.DESPACHO),
            'proxima_label': dict(Expedicao.Status.choices)[proxima] if proxima else '',
            'etapas': [
                {'valor': s, 'label': dict(Expedicao.Status.choices)[s],
                 'passou': expedicao.passou_por(s), 'atual': expedicao.status == s}
                for s in Expedicao.ETAPAS
            ],
            'linhas': linhas,
            'volumes': expedicao.volumes.all(),
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })


class ExpedicaoConferirView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        expedicao = _expedicao(request, pk)

        quantidades = {}
        for chave, bruto in request.POST.items():
            if not chave.startswith('tam_'):
                continue
            try:
                quantidades[int(chave.removeprefix('tam_'))] = int(bruto or 0)
            except ValueError:
                continue

        try:
            total = ExpedicaoService.conferir(expedicao, quantidades, request.POST)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            if expedicao.conferencia_fecha:
                messages.success(request, f'Conferência fecha: {total} peça(s).')
            else:
                falta = expedicao.divergencia_conferencia
                messages.warning(
                    request,
                    f'Conferência gravada, mas {"faltam" if falta > 0 else "sobram"} '
                    f'{abs(falta)} peça(s) em relação à ordem.',
                )
        return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))


class ExpedicaoAvancarView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        expedicao = _expedicao(request, pk)
        try:
            proxima = ExpedicaoService.avancar(expedicao, request.user, request.POST)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(
                request,
                f'Expedição #{expedicao.numero:04d} → '
                f'{dict(Expedicao.Status.choices)[proxima]}.',
            )
        return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))


class VolumeCriarView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        expedicao = _expedicao(request, pk)
        try:
            volume = ExpedicaoService.criar_volume(expedicao, request.POST)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, f'Volume {volume.numero} criado.')
        return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))


class VolumeRemoverView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, volume_pk):
        expedicao = _expedicao(request, pk)
        volume = get_object_or_404(Volume, pk=volume_pk, expedicao=expedicao)
        try:
            ExpedicaoService.remover_volume(volume)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, 'Volume removido.')
        return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))


class ExpedicaoCancelarView(ModaBaseView):
    permissao_acao = 'cancelar'

    def post(self, request, pk):
        expedicao = _expedicao(request, pk)
        try:
            ExpedicaoService.cancelar(expedicao, (request.POST.get('motivo') or '').strip())
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, f'Expedição #{expedicao.numero:04d} cancelada.')
        return redirect(reverse('moda:expedicao-detail', args=[expedicao.pk]))


class EtiquetaView(ModaBaseView):
    """
    QR Code de um volume, em PNG.

    Gerado sob demanda e não guardado: a imagem é derivada do código, e um
    arquivo salvo seria mais uma coisa para sincronizar sem ganho nenhum.
    """

    def get(self, request, pk, volume_pk):
        import qrcode

        expedicao = _expedicao(request, pk)
        volume = get_object_or_404(Volume, pk=volume_pk, expedicao=expedicao)

        imagem = qrcode.make(volume.codigo)
        resposta = HttpResponse(content_type='image/png')
        imagem.save(resposta, 'PNG')
        return resposta
