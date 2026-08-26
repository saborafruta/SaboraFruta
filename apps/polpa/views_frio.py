"""
As telas da cadeia de frio: temperatura, posições e os alertas.

A TELA DE TEMPERATURA É DE QUEM MEDE, e quem mede está de pé na antecâmara
com um termômetro na mão. Por isso o registro fica no topo, com a câmara
já escolhida — e o histórico logo abaixo, para conferir se o número faz
sentido contra os últimos.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .forms_posicao import LeituraForm, PosicaoForm
from .models import (
    ApontamentoEtapa, Camara, LeituraTemperatura, LoteArmazenado, Posicao,
    Recurso,
)
from .services import FrioService, TunelService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


class TemperaturaView(PolpaBaseView):
    """O painel das câmaras e o registro de uma leitura."""

    area = 'frio'

    def get(self, request):
        return render(request, 'polpa/temperatura.html', {
            'title': 'Registro de temperatura',
            'painel': FrioService.painel_temperatura(_filial(request)),
            'leituras': (
                LeituraTemperatura.objects.for_filial(_filial(request))
                .select_related('camara', 'medido_por')[:60]
            ),
            'form': LeituraForm(filial=_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'criar'),
        })

    def post(self, request):
        form = LeituraForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            messages.error(
                request,
                'Leitura não gravada: '
                + '; '.join(f'{c}: {e[0]}' for c, e in form.errors.items()),
            )
            return redirect(reverse('polpa:temperatura'))

        dados = form.cleaned_data
        try:
            leitura = FrioService.registrar_leitura(
                dados['camara'], dados['temperatura'], request.user,
                {
                    'medida_em': dados.get('medida_em'),
                    'observacao': dados.get('observacao') or '',
                },
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('polpa:temperatura'))

        if leitura.fora_da_faixa:
            # O DESVIO VOLTA NA TELA e vira notificação: quem mediu precisa
            # ver na hora, e quem não estava lá precisa ficar sabendo.
            messages.warning(
                request,
                f'{leitura.camara}: {leitura.temperatura}°C — '
                f'{leitura.desvio}°C fora da faixa. Um aviso foi disparado.',
            )
        else:
            messages.success(
                request, f'{leitura.camara}: {leitura.temperatura}°C registrados.',
            )
        return redirect(reverse('polpa:temperatura'))


class AlertasFrioView(PolpaBaseView):
    """Os quatro alertas, cada um com o que resolver."""

    area = 'frio'

    def get(self, request):
        return render(request, 'polpa/alertas_frio.html', {
            'title': 'Alertas da cadeia de frio',
            'alertas': FrioService.alertas(_filial(request)),
            'resumo': FrioService.resumo_alertas(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'editar'),
        })


class MapaCamaraView(PolpaBaseView):
    """As posições de uma câmara e o que está em cada uma."""

    area = 'frio'

    def get(self, request, pk):
        camara = get_object_or_404(
            Camara.objects.for_filial(_filial(request)), pk=pk,
        )
        return render(request, 'polpa/camara_mapa.html', {
            'title': f'Mapa — {camara.nome}',
            'camara': camara,
            'linhas': FrioService.mapa(camara),
            'leitura': FrioService.temperatura_atual(camara),
            'form': PosicaoForm(
                filial=_filial(request), initial={'camara': camara.pk},
            ),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'criar'),
        })


class PosicaoCreateView(PolpaBaseView):
    """Cadastra uma posição na câmara."""

    area = 'frio'
    permissao_acao = 'criar'

    def post(self, request, pk):
        camara = get_object_or_404(
            Camara.objects.for_filial(_filial(request)), pk=pk,
        )
        volta = redirect(reverse('polpa:camara-mapa', args=[camara.pk]))

        form = PosicaoForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            messages.error(
                request,
                'Posição não criada: '
                + '; '.join(
                    e[0] for e in form.errors.values()
                ),
            )
            return volta

        posicao = form.save()
        messages.success(request, f'Posição {posicao.codigo} criada.')
        return volta


class MoverLoteView(PolpaBaseView):
    """Move um lote de posição — e de câmara, quando for o caso."""

    area = 'frio'
    permissao_acao = 'editar'

    def post(self, request, pk):
        armazenado = get_object_or_404(
            LoteArmazenado.objects.for_filial(_filial(request))
            .select_related('lote', 'camara', 'posicao'),
            pk=pk,
        )
        volta = redirect(request.POST.get('voltar') or reverse('polpa:estoque-frio'))

        posicao = get_object_or_404(
            Posicao.objects.for_filial(_filial(request)).select_related('camara'),
            pk=request.POST.get('posicao'),
        )
        de = armazenado.onde or armazenado.camara.nome

        try:
            FrioService.mover(
                armazenado, posicao, request.user,
                (request.POST.get('motivo') or '').strip(),
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request,
            f'Lote {armazenado.lote.numero_lote}: {de} → '
            f'{posicao.camara.nome} {posicao.codigo}.',
        )
        return volta


class TunelView(PolpaBaseView):
    """
    O túnel de congelamento: fila, o que está dentro e o que já saiu.

    A TELA É DE QUEM ESTÁ NA PORTA DO TÚNEL, e a pergunta dele não é "como
    vai a ordem 123" — é "o que está aqui dentro e o que já passou do
    tempo". Por isso o que está dentro vem primeiro, com o relógio de cada
    carga, e as duas ações (pôr e tirar) ficam na própria linha: quem opera
    está de luva e não vai navegar até a ordem para apontar uma etapa.
    """

    area = 'frio'

    def get(self, request):
        painel = TunelService.painel(_filial(request))
        return render(request, 'polpa/tunel.html', {
            'title': 'Túnel de congelamento',
            **painel,
            'recursos': Recurso.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'editar'),
        })

    def post(self, request):
        volta = redirect(reverse('polpa:tunel'))
        if not request.user.tem_permissao('polpa_frio', 'editar'):
            messages.error(request, 'Sem permissão para movimentar o túnel.')
            return volta

        etapa = get_object_or_404(
            ApontamentoEtapa.objects.for_filial(_filial(request))
            .select_related('ordem', 'ordem__ordem'),
            pk=request.POST.get('etapa'),
        )
        acao = request.POST.get('acao')

        try:
            if acao == 'entrar':
                TunelService.entrar(etapa, {
                    'quantidade_entrada': _numero(request.POST.get('quantidade_entrada')),
                    'equipamento': _recurso(request, request.POST.get('equipamento')),
                    'observacao': (request.POST.get('observacao') or '').strip(),
                }, request.user)
                messages.success(
                    request, f'{etapa.ordem.numero} entrou no túnel.',
                )
            elif acao == 'sair':
                linha = TunelService.sair(etapa, {
                    'quantidade_saida': _numero(request.POST.get('quantidade_saida')),
                    'temperatura': _numero(request.POST.get('temperatura')),
                    'motivo_perda': (request.POST.get('motivo_perda') or '').strip(),
                    'observacao': (request.POST.get('observacao') or '').strip(),
                }, request.user)
                minutos = linha.duracao_minutos
                messages.success(
                    request,
                    f'{etapa.ordem.numero} saiu do túnel'
                    + (f' após {minutos} min.' if minutos is not None else '.'),
                )
                # A TEMPERATURA DE SAÍDA FORA DA FAIXA VOLTA NA TELA: é o
                # número que prova o congelamento, e descobrir o desvio no
                # relatório do mês é descobrir tarde demais.
                alvo = TunelService.alvo(etapa.ordem)
                if TunelService._fora_da_faixa(linha.temperatura, alvo):
                    messages.warning(
                        request,
                        f'Saiu a {linha.temperatura}°C — fora de {alvo["faixa"]} '
                        'que a receita manda.',
                    )
            else:
                messages.error(request, 'Ação desconhecida.')
        except DomainError as erro:
            messages.error(request, str(erro))

        return volta


def _numero(valor):
    """Decimal do que veio do formulário — vazio é `None`, não zero."""
    texto = (valor or '').strip().replace(',', '.')
    if not texto:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _recurso(request, pk):
    if not pk:
        return None
    return Recurso.objects.for_filial(_filial(request)).filter(pk=pk).first()
