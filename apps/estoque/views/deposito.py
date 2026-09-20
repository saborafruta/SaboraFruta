"""Cadastro de depósitos e transferência interna entre eles (sem NF-e)."""
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views import View

from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.core.tenant_context import tenant_atomic
from apps.estoque.forms import AviamentoRapidoForm, DepositoForm, TransferenciaInternaForm
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


def _painel_aviamentos(request, deposito, form_rapido=None):
    """
    Dados do painel "Aviamentos" da tela do depósito: o catálogo da filial
    com o saldo NESTE depósito e o total geral, mais o formulário de
    cadastro rápido. Só existe com o vertical Moda ativo (é dele o catálogo).
    """
    from apps.moda.models import Aviamento
    from apps.moda.permissoes import pode_na_area

    filial = request.filial_ativa
    catalogo = list(
        Aviamento.objects.for_filial(filial)
        .select_related('produto_estoque__unidade_medida')
        .order_by('tipo', 'nome')
    )
    produto_ids = [a.produto_estoque_id for a in catalogo if a.produto_estoque_id]
    saldos = {}
    if produto_ids:
        for row in (
            Estoque.objects.filter(filial=filial, produto_id__in=produto_ids)
            .values('produto_id', 'deposito_id')
            .annotate(total=Sum('quantidade_atual'))
        ):
            por_deposito = saldos.setdefault(row['produto_id'], {})
            por_deposito[row['deposito_id']] = row['total'] or Decimal('0')

    for aviamento in catalogo:
        por_deposito = saldos.get(aviamento.produto_estoque_id, {})
        aviamento.saldo_aqui = por_deposito.get(deposito.pk, Decimal('0'))
        aviamento.saldo_total = sum(por_deposito.values(), Decimal('0'))

    pode_cadastrar = (
        request.user.tem_permissao('estoque', 'criar')
        and pode_na_area(request.user, 'comercial', 'criar')
    )
    return {
        'aviamentos': catalogo,
        'deposito_tem_aviamento': bool(
            {valor for valor, _ in Aviamento.Tipo.choices} & set(deposito.tipos_material or [])
        ),
        'pode_cadastrar_aviamento': pode_cadastrar,
        'form_aviamento': form_rapido or AviamentoRapidoForm(
            filial=filial, empresa=empresa_operacional(request),
            initial={
                'tipo': request.GET.get('tipo', ''),
                'unidade_medida': request.GET.get('un', ''),
            },
        ),
    }


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
            **_painel_aviamentos(request, deposito),
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
            **_painel_aviamentos(request, deposito),
        })


class DepositoAviamentoCreateView(PermissaoRequiredMixin, View):
    """
    Cadastro rápido de aviamento a partir da tela do depósito.

    Cria o item do catálogo (`moda.Aviamento`), o produto de estoque enxuto
    ligado a ele e, se informado, o saldo inicial NESTE depósito. Depois
    volta para a mesma tela, com tipo e unidade preservados — a ideia é
    cadastrar uma fileira de aviamentos sem sair daqui.
    """

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'
    template_name = 'estoque/deposito/form.html'

    def post(self, request, pk):
        from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE
        from apps.moda.models import Aviamento
        from apps.moda.permissoes import pode_na_area
        from apps.moda.views_apoio import criar_produto_materia_prima

        filial = request.filial_ativa
        deposito = get_object_or_404(Deposito.objects.filter(filial=filial), pk=pk)
        if not pode_na_area(request.user, 'comercial', 'criar'):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('estoque:deposito-update', pk=deposito.pk)

        form = AviamentoRapidoForm(
            request.POST, filial=filial, empresa=empresa_operacional(request),
        )
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': DepositoForm(instance=deposito, filial=filial),
                'deposito': deposito,
                'title': f'Editar depósito — {deposito.nome}',
                **_painel_aviamentos(request, deposito, form_rapido=form),
            })

        dados = form.cleaned_data
        with tenant_atomic():
            unidade = form.obter_unidade()
            dados['unidade_medida'] = unidade
            produto = criar_produto_materia_prima(filial, dados, 'Cadastro de Aviamentos')
            aviamento = Aviamento.objects.create(
                filial=filial, nome=dados['nome'], tipo=dados['tipo'],
                tipo_personalizado=dados.get('tipo_personalizado') or '',
                codigo=dados.get('codigo') or '',
                unidade=form.sigla_do_aviamento(unidade),
                produto_estoque=produto,
            )
            quantidade = dados.get('quantidade_inicial')
            if quantidade:
                MovimentacaoService.ajustar_manual(
                    produto_id=produto.pk, filial_id=filial.pk,
                    quantidade_nova=quantidade, usuario_id=request.user.pk,
                    justificativa=(
                        'Saldo inicial informado ao cadastrar o aviamento '
                        f'no depósito {deposito.nome}.'
                    ),
                    deposito_id=deposito.pk,
                )
        registrar_auditoria(
            request=request, modulo='estoque', acao='criar', objeto=aviamento,
            descricao=f'Aviamento {aviamento.nome} cadastrado pelo depósito {deposito.nome}',
        )
        messages.success(request, f'Aviamento "{aviamento.nome}" cadastrado.')
        tipo_volta = (
            f'{form.PREFIXO_TIPO_PERSONALIZADO}{aviamento.tipo_personalizado}'
            if aviamento.tipo_personalizado else aviamento.tipo
        )
        destino = reverse('estoque:deposito-update', args=[deposito.pk])
        return redirect(
            f'{destino}?{urlencode({"tipo": tipo_volta, "un": unidade.pk})}#aviamentos'
        )


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
