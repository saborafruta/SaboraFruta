"""
Telas do encaixe (grupo Produção).

O encaixe é cadastro, não apontamento: fica no menu junto do corte porque é
lá que ele é usado, mas vive por conta própria — o mesmo risco serve a todos
os enfestos daquele modelo naquela largura.
"""
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import EncaixeForm
from .models import Encaixe
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _encaixe(request, pk) -> Encaixe:
    return get_object_or_404(
        Encaixe.objects.for_filial(_filial(request))
        .select_related('produto', 'modelo', 'tecido')
        .prefetch_related('cortes__ordem'),
        pk=pk,
    )


class EncaixeListView(ModaBaseView):
    """Os riscos cadastrados, do melhor aproveitamento para o pior."""

    def get(self, request):
        encaixes = list(
            Encaixe.objects.for_filial(_filial(request))
            .select_related('produto', 'modelo', 'tecido')
        )

        busca = (request.GET.get('q') or '').strip()
        if busca:
            alvo = busca.lower()
            encaixes = [
                e for e in encaixes
                if alvo in e.nome.lower()
                or (e.produto and alvo in e.produto.nome.lower())
            ]

        # Ordenados pelo aproveitamento, do maior para o menor: a pergunta
        # que se faz nesta lista é "quais riscos estão desperdiçando
        # tecido", e a resposta são os últimos. Os não medidos vão para o
        # fim, porque zero ali não é aproveitamento ruim — é ausência.
        medidos = sorted(
            [e for e in encaixes if e.medido],
            key=lambda e: e.aproveitamento, reverse=True,
        )
        nao_medidos = [e for e in encaixes if not e.medido]

        return render(request, 'moda/encaixe_list.html', {
            'title': 'Encaixes',
            'encaixes': medidos + nao_medidos,
            'busca': busca,
            'resumo': self.resumo(medidos, len(nao_medidos)),
        })

    @staticmethod
    def resumo(medidos, pendentes) -> dict:
        """
        Média dos aproveitamentos, ponderada pela área utilizada.

        Mesma razão do controle de corte: um risco grande com aproveitamento
        ruim pesa mais no tecido gasto do que um pequeno bem-feito, e a média
        simples inverteria essa leitura.
        """
        base = sum((e.area_utilizada for e in medidos), Decimal('0'))
        media = (
            (sum((e.aproveitamento * e.area_utilizada for e in medidos), Decimal('0')) / base)
            .quantize(Decimal('0.1'))
            if base else Decimal('0')
        )
        return {
            'total': len(medidos) + pendentes,
            'medidos': len(medidos),
            'pendentes': pendentes,
            'aproveitamento': media,
            'perda': (Decimal('100') - media).quantize(Decimal('0.1')) if media else Decimal('0'),
            # `max`/`min` e nao medidos[0]/[-1]: depender da ordem da lista
            # faria o resumo mentir na primeira vez que alguem chamasse este
            # metodo sem ordenar antes -- e nada na assinatura avisa disso.
            'melhor': max(medidos, key=lambda e: e.aproveitamento, default=None),
            'pior': min(medidos, key=lambda e: e.aproveitamento, default=None),
        }


class EncaixeFormView(ModaBaseView):
    permissao_acao = 'editar'

    def get(self, request, pk=None):
        encaixe = _encaixe(request, pk) if pk else None
        return self._render(request, EncaixeForm(
            instance=encaixe, filial=_filial(request),
        ), encaixe)

    def post(self, request, pk=None):
        encaixe = _encaixe(request, pk) if pk else None
        form = EncaixeForm(
            request.POST, request.FILES, instance=encaixe, filial=_filial(request),
        )

        if not form.is_valid():
            return self._render(request, form, encaixe)

        novo = form.save(commit=False)
        novo.filial = _filial(request)
        novo.save()

        messages.success(
            request,
            f'{novo.nome} salvo — aproveitamento {novo.aproveitamento}%.'
            if novo.medido else
            f'{novo.nome} salvo. Informe a área útil para o aproveitamento ser calculado.',
        )
        return redirect(reverse('moda:encaixe-detail', args=[novo.pk]))

    @staticmethod
    def _render(request, form, encaixe):
        return render(request, 'moda/encaixe_form.html', {
            'title': 'Editar encaixe' if encaixe else 'Novo encaixe',
            'form': form,
            'encaixe': encaixe,
        })


class EncaixeDetailView(ModaBaseView):
    def get(self, request, pk):
        encaixe = _encaixe(request, pk)
        return render(request, 'moda/encaixe_detail.html', {
            'title': encaixe.nome,
            'encaixe': encaixe,
            'alertas': encaixe.alertas,
            'cortes': encaixe.cortes.select_related('ordem').all(),
        })
