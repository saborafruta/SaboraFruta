"""
Telas do Roteiro de Produção (grupo Engenharia).

A tela de Operações é o catálogo da fábrica e entra pelo mecanismo genérico
de cadastros (`views_apoio.CADASTROS`). Aqui ficam as do roteiro em si, que
precisam de tela de detalhe com etapas ordenadas, herança do catálogo e os
totais de tempo, custo e gargalo.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import OperacaoRoteiroForm, RoteiroForm
from .models import OperacaoRoteiro, Roteiro
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _roteiro_da_filial(request, pk) -> Roteiro:
    return get_object_or_404(
        Roteiro.objects.for_filial(_filial(request))
        .select_related('produto')
        .prefetch_related('etapas__operacao'),
        pk=pk,
    )


class RoteiroListView(ModaBaseView):
    """Lista dos roteiros. Entregue no endereço do menu (sequencia-producao)."""

    def get(self, request):
        roteiros = (
            Roteiro.objects.for_filial(_filial(request))
            .select_related('produto')
            .prefetch_related('etapas__operacao')
        )
        return render(request, 'moda/roteiro_list.html', {
            'title': 'Roteiro de Produção',
            'roteiros': roteiros,
        })


class RoteiroFormView(ModaBaseView):
    permissao_acao = 'editar'

    def get(self, request, pk=None):
        roteiro = _roteiro_da_filial(request, pk) if pk else None
        return self._render(request, RoteiroForm(
            instance=roteiro, filial=_filial(request),
        ), roteiro)

    def post(self, request, pk=None):
        roteiro = _roteiro_da_filial(request, pk) if pk else None
        form = RoteiroForm(request.POST, instance=roteiro, filial=_filial(request))

        if not form.is_valid():
            return self._render(request, form, roteiro)

        novo = form.save(commit=False)
        novo.filial = _filial(request)
        novo.save()

        messages.success(request, f'Roteiro de {novo.produto.nome} salvo.')
        return redirect(reverse('moda:roteiro-detail', args=[novo.pk]))

    @staticmethod
    def _render(request, form, roteiro):
        return render(request, 'moda/roteiro_form.html', {
            'title': 'Editar roteiro' if roteiro else 'Novo roteiro',
            'form': form,
            'roteiro': roteiro,
        })


class RoteiroDetailView(ModaBaseView):
    """O roteiro completo: etapas na ordem, com tempo, custo e gargalo."""

    def get(self, request, pk):
        roteiro = _roteiro_da_filial(request, pk)
        return render(request, 'moda/roteiro_detail.html', self.contexto(request, roteiro))

    @staticmethod
    def contexto(request, roteiro, form_etapa=None) -> dict:
        etapas = list(roteiro.etapas.all())
        gargalo = roteiro.gargalo
        return {
            'title': f'Roteiro {roteiro.produto.codigo}',
            'roteiro': roteiro,
            'produto': roteiro.produto,
            'etapas': etapas,
            'gargalo_id': gargalo.pk if gargalo else None,
            'outros_roteiros': (
                Roteiro.objects.for_filial(_filial(request))
                .select_related('produto').exclude(pk=roteiro.pk)
            ),
            'form_etapa': form_etapa or OperacaoRoteiroForm(
                filial=_filial(request), roteiro=roteiro,
            ),
        }


class EtapaCreateView(ModaBaseView):
    """Acrescenta uma operação ao roteiro."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        roteiro = _roteiro_da_filial(request, pk)
        form = OperacaoRoteiroForm(
            request.POST, filial=_filial(request), roteiro=roteiro,
        )

        if not form.is_valid():
            return render(
                request, 'moda/roteiro_detail.html',
                RoteiroDetailView.contexto(request, roteiro, form_etapa=form),
            )

        etapa = form.save(commit=False)
        etapa.roteiro = roteiro
        # Sem sequência informada, entra no fim com folga de 10 — a mesma
        # numeração espaçada do catálogo, para dar onde encaixar depois.
        if not etapa.sequencia:
            ultima = max((e.sequencia for e in roteiro.etapas.all()), default=0)
            etapa.sequencia = ultima + 10
        etapa.save()

        messages.success(request, f'{etapa.operacao.nome} entrou no roteiro.')
        return redirect(reverse('moda:roteiro-detail', args=[roteiro.pk]))


class EtapaUpdateView(ModaBaseView):
    """
    Ajusta sequência, tempo, custo e capacidade direto na tabela.

    Campo em branco volta a herdar da operação — por isso vazio grava
    `None`, e não zero: zero é uma exceção legítima ("aqui esta etapa não
    custa nada") e precisa ser distinguível de "usa o padrão".
    """

    permissao_acao = 'editar'

    HERDAVEIS = ('tempo_padrao', 'custo', 'capacidade')
    CAMPOS = ('sequencia',) + HERDAVEIS

    def post(self, request, pk):
        roteiro = _roteiro_da_filial(request, pk)
        etapas = {e.pk: e for e in roteiro.etapas.all()}
        alterados = []

        for chave, bruto in request.POST.items():
            if not chave.startswith('et_'):
                continue
            partes = chave.removeprefix('et_').split('-')
            if len(partes) != 2:
                continue
            try:
                etapa = etapas[int(partes[0])]
            except (ValueError, KeyError):
                continue

            campo = partes[1]
            if campo not in self.CAMPOS:
                continue

            texto = (bruto or '').strip()
            if not texto:
                # Só os herdáveis aceitam vazio; sequência vazia seria uma
                # etapa sem posição na fila.
                if campo not in self.HERDAVEIS:
                    continue
                valor = None
            else:
                try:
                    valor = Decimal(texto.replace(',', '.'))
                except InvalidOperation:
                    continue
                if valor < 0:
                    continue
                if campo == 'sequencia':
                    valor = int(valor)

            if getattr(etapa, campo) != valor:
                setattr(etapa, campo, valor)
                if etapa not in alterados:
                    alterados.append(etapa)

        if alterados:
            OperacaoRoteiro.objects.bulk_update(alterados, list(self.CAMPOS))
            messages.success(request, f'{len(alterados)} etapa(s) atualizada(s).')
        else:
            messages.info(request, 'Nada mudou.')

        return redirect(reverse('moda:roteiro-detail', args=[roteiro.pk]))


class EtapaDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, etapa_pk):
        roteiro = _roteiro_da_filial(request, pk)
        etapa = get_object_or_404(OperacaoRoteiro, pk=etapa_pk, roteiro=roteiro)
        nome = etapa.operacao.nome
        etapa.delete()
        messages.success(request, f'{nome} saiu do roteiro.')
        return redirect(reverse('moda:roteiro-detail', args=[roteiro.pk]))


class RoteiroCopiarView(ModaBaseView):
    """
    Copia as etapas de outro roteiro para este.

    É o caminho real de uso: a segunda camisa da casa tem quase o mesmo
    roteiro da primeira, com uma etapa a mais ou a menos. Montar as quinze
    etapas de novo à mão é o tipo de trabalho que faz o roteiro não ser
    preenchido.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        destino = _roteiro_da_filial(request, pk)

        try:
            origem = Roteiro.objects.for_filial(_filial(request)).prefetch_related(
                'etapas'
            ).get(pk=int(request.POST.get('origem') or 0))
        except (Roteiro.DoesNotExist, ValueError):
            messages.error(request, 'Roteiro de origem inválido.')
            return redirect(reverse('moda:roteiro-detail', args=[destino.pk]))

        if origem.pk == destino.pk:
            messages.error(request, 'Escolha um roteiro diferente para copiar.')
            return redirect(reverse('moda:roteiro-detail', args=[destino.pk]))

        # Operações que o destino já tem ficam como estão: sobrescrever
        # apagaria os ajustes que alguém fez de propósito neste produto.
        ja_tem = {e.operacao_id for e in destino.etapas.all()}
        novas = [
            OperacaoRoteiro(
                roteiro=destino,
                operacao_id=e.operacao_id,
                sequencia=e.sequencia,
                tempo_padrao=e.tempo_padrao,
                custo=e.custo,
                capacidade=e.capacidade,
                maquina=e.maquina,
                responsavel=e.responsavel,
                observacao=e.observacao,
            )
            for e in origem.etapas.all()
            if e.operacao_id not in ja_tem
        ]

        if not novas:
            messages.info(request, 'Este roteiro já tem todas as etapas do outro.')
        else:
            OperacaoRoteiro.objects.bulk_create(novas)
            messages.success(
                request,
                f'{len(novas)} etapa(s) copiada(s) de {origem.produto.nome}.',
            )

        return redirect(reverse('moda:roteiro-detail', args=[destino.pk]))
