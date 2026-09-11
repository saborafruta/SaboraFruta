"""Cadastro de depósitos e transferência interna entre eles (sem NF-e)."""
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.forms import DepositoForm, TransferenciaInternaForm
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.permissoes import permissoes_estoque
from apps.produtos.models import Produto


def _auditar_deposito(request, acao, deposito, descricao='', antes=None, depois=None):
    return registrar_auditoria(
        request=request,
        modulo='estoque',
        acao=acao,
        objeto=deposito,
        descricao=descricao or f'Depósito {deposito.nome}',
        antes=antes,
        depois=depois,
    )


class DepositoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/deposito/list.html'

    def get(self, request):
        from apps.moda.models import MaterialFicha
        tipo_labels = dict(MaterialFicha.Tipo.choices)

        filial = request.filial_ativa
        depositos = list(
            Deposito.objects.filter(filial=filial).order_by('-is_padrao', 'nome')
        )
        resumo = {
            row['deposito_id']: row
            for row in (
                Estoque.objects.filter(filial=filial)
                .values('deposito_id')
                .annotate(
                    itens=Count('id'),
                    total=Sum('quantidade_atual'),
                )
            )
        }
        for dep in depositos:
            linha = resumo.get(dep.pk, {})
            dep.itens_com_saldo = linha.get('itens', 0)
            dep.total_unidades = linha.get('total') or Decimal('0')
            dep.tipos_material_labels = [
                tipo_labels.get(valor, valor) for valor in (dep.tipos_material or [])
            ]

        return render(request, self.template_name, {
            'title': 'Depósitos',
            'depositos': depositos,
            'permissoes_estoque': permissoes_estoque(request),
        })


class DepositoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'criar'
    template_name = 'estoque/deposito/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': DepositoForm(filial=request.filial_ativa),
            'title': 'Novo depósito',
        })

    def post(self, request):
        form = DepositoForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            deposito = form.save(commit=False)
            deposito.filial = request.filial_ativa
            deposito.save()
            _auditar_deposito(
                request, 'criar', deposito,
                f'Depósito {deposito.nome} criado',
                depois=snapshot_modelo(deposito),
            )
            messages.success(request, f'Depósito "{deposito.nome}" criado.')
            return redirect('estoque:deposito-list')
        return render(request, self.template_name, {
            'form': form, 'title': 'Novo depósito',
        })


class DepositoUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'editar'
    template_name = 'estoque/deposito/form.html'

    def _get(self, request, pk):
        return get_object_or_404(
            Deposito.objects.filter(filial=request.filial_ativa), pk=pk,
        )

    def get(self, request, pk):
        deposito = self._get(request, pk)
        return render(request, self.template_name, {
            'form': DepositoForm(instance=deposito, filial=request.filial_ativa),
            'deposito': deposito,
            'title': f'Editar depósito — {deposito.nome}',
        })

    def post(self, request, pk):
        deposito = self._get(request, pk)
        antes = snapshot_modelo(deposito)
        form = DepositoForm(request.POST, instance=deposito, filial=request.filial_ativa)
        if form.is_valid():
            deposito = form.save()
            _auditar_deposito(
                request, 'editar', deposito,
                f'Depósito {deposito.nome} atualizado',
                antes=antes, depois=snapshot_modelo(deposito),
            )
            messages.success(request, 'Depósito atualizado.')
            return redirect('estoque:deposito-list')
        return render(request, self.template_name, {
            'form': form, 'deposito': deposito,
            'title': f'Editar depósito — {deposito.nome}',
        })


class EstoquePorDepositoJsonView(PermissaoRequiredMixin, View):
    """Saldo de um produto em cada depósito da filial — alimenta o painel
    ao lado da transferência interna, pra escolher "De" com o saldo à vista."""

    permissao_modulo = 'estoque'

    def get(self, request):
        filial = request.filial_ativa
        produto_id = request.GET.get('produto')
        if not produto_id:
            return JsonResponse({'ok': False, 'error': 'Informe o produto.'}, status=400)

        saldos = {
            row['deposito_id']: row
            for row in (
                Estoque.objects.filter(filial=filial, produto_id=produto_id)
                .values('deposito_id')
                .annotate(
                    atual=Sum('quantidade_atual'),
                    disponivel=Sum('quantidade_disponivel'),
                )
            )
        }
        depositos = Deposito.objects.filter(filial=filial, ativo=True).order_by(
            '-is_padrao', 'nome',
        )
        resultados = [
            {
                'id': dep.pk,
                'nome': dep.nome,
                'padrao': dep.is_padrao,
                'atual': float(saldos.get(dep.pk, {}).get('atual') or 0),
                'disponivel': float(saldos.get(dep.pk, {}).get('disponivel') or 0),
            }
            for dep in depositos
        ]
        return JsonResponse({'ok': True, 'results': resultados})


class TransferenciaInternaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'aprovar'
    template_name = 'estoque/deposito/transferencia_interna.html'

    def _contexto(self, request, form, produto=None):
        return {
            'title': 'Transferência interna entre depósitos',
            'form': form,
            'produto': produto,
            'cancel_url': reverse('estoque:deposito-list'),
        }

    def get(self, request):
        filial = request.filial_ativa
        produto = None
        produto_id = request.GET.get('produto')
        if produto_id:
            produto = Produto.objects.for_filial(filial).filter(pk=produto_id).first()
        form = TransferenciaInternaForm(
            filial=filial,
            initial={'produto': produto.pk if produto else None},
        )
        return render(request, self.template_name, self._contexto(request, form, produto))

    def post(self, request):
        filial = request.filial_ativa
        form = TransferenciaInternaForm(request.POST, filial=filial)
        produto = None
        if form.is_valid():
            produto = Produto.objects.for_filial(filial).filter(
                pk=form.cleaned_data['produto'],
            ).first()
            if not produto:
                form.add_error(None, 'Produto não encontrado nesta filial.')
            else:
                try:
                    MovimentacaoService.transferir_entre_depositos(
                        produto_id=produto.pk,
                        filial_id=filial.pk,
                        deposito_origem_id=form.cleaned_data['deposito_origem'].pk,
                        deposito_destino_id=form.cleaned_data['deposito_destino'].pk,
                        quantidade=form.cleaned_data['quantidade'],
                        usuario_id=request.user.pk,
                        observacao=form.cleaned_data.get('observacao', ''),
                    )
                    registrar_auditoria(
                        request=request, modulo='estoque', acao='transferir',
                        objeto=produto,
                        descricao=(
                            f'Transferência interna de {form.cleaned_data["quantidade"]} '
                            f'{produto.descricao}: {form.cleaned_data["deposito_origem"].nome} '
                            f'→ {form.cleaned_data["deposito_destino"].nome}'
                        ),
                    )
                    messages.success(
                        request,
                        f'{form.cleaned_data["quantidade"]} un. de "{produto.descricao}" '
                        f'movidas para {form.cleaned_data["deposito_destino"].nome}.',
                    )
                    return redirect('estoque:deposito-list')
                except DomainError as exc:
                    form.add_error(None, str(exc))
        return render(request, self.template_name, self._contexto(request, form, produto))
