"""Views do módulo Outras Movimentações de Estoque."""
from __future__ import annotations

import json as _json
from decimal import Decimal

from django.contrib import messages
from django.db import models as db_models
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.cadastros.models import Cliente, Fornecedor
from apps.core.models import Filial
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin
from apps.estoque.forms.outras_movimentacoes import DevolucaoClienteForm, DevolucaoFornecedorForm, SaidaEspecialForm
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.permissoes import permissoes_estoque
from apps.produtos.models import Produto
from apps.pdv.models import DevolucaoPDV, ItemDevolucaoPDV, VendaPDV

# CFOP fixos por tipo de operação
CFOP_MAP = {
    'bonificacao': '5910',
    'roubo': '5927',
    'perda': '5927',
    'deterioracao': '5928',
}

TIPOS_OUTRAS = {
    MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
    MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_FORNECEDOR,
    MovimentacaoEstoque.TipoOperacao.BONIFICACAO,
    MovimentacaoEstoque.TipoOperacao.ROUBO,
    MovimentacaoEstoque.TipoOperacao.PERDA,
    MovimentacaoEstoque.TipoOperacao.DETERIORACAO,
    MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
    MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_ENTRADA,
}


class OutrasMovimentacoesHubView(PermissaoRequiredMixin, View):
    """Hub com os 5 cards de operação e histórico recente."""

    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def get(self, request):
        filial = request.filial_ativa
        perms = permissoes_estoque(request)

        historico = (
            MovimentacaoEstoque.objects
            .filter(filial=filial, tipo_operacao__in=TIPOS_OUTRAS)
            .select_related('produto', 'usuario', 'lote', 'filial_destino')
            .order_by('-data_movimentacao')[:20]
        )

        return render(request, 'estoque/outras_movimentacoes/hub.html', {
            'title': 'Outras Movimentações',
            'historico': historico,
            **perms,
        })


class DevolucaoClienteView(PermissaoRequiredMixin, View):
    """Devolução de mercadoria pelo cliente — gera entrada no estoque e crédito."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def _get_filial(self, request):
        return request.filial_ativa

    def get(self, request):
        filial = self._get_filial(request)
        form = DevolucaoClienteForm(filial=filial)
        perms = permissoes_estoque(request)
        return render(request, 'estoque/outras_movimentacoes/devolucao.html', {
            'title': 'Devolução de Cliente',
            'form': form,
            'cancel_url': reverse('estoque:outras-mov-hub'),
            **perms,
        })

    @transaction.atomic
    def post(self, request):
        filial = self._get_filial(request)
        form = DevolucaoClienteForm(request.POST, filial=filial)

        if not form.is_valid():
            perms = permissoes_estoque(request)
            return render(request, 'estoque/outras_movimentacoes/devolucao.html', {
                'title': 'Devolução de Cliente',
                'form': form,
                'cancel_url': reverse('estoque:outras-mov-hub'),
                **perms,
            })

        data = form.cleaned_data
        try:
            mov = MovimentacaoService.registrar_movimentacao(
                produto_id=data['produto'].pk,
                filial_id=filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                quantidade=data['quantidade'],
                usuario_id=request.user.pk,
                lote_id=data['lote'].pk if data.get('lote') else None,
                valor_unitario=data.get('valor_unitario'),
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                documento_numero=data.get('documento_numero', ''),
                observacao=data['observacao'],
            )

            if data.get('gerar_credito') and data.get('valor_unitario'):
                from apps.financeiro.models.credito_cliente import CreditoCliente
                valor_total = data['valor_unitario'] * data['quantidade']
                CreditoCliente.objects.create(
                    filial=filial,
                    cliente=data['cliente'],
                    valor=valor_total,
                    valor_utilizado=Decimal('0'),
                    motivo='devolucao',
                    documento_numero=data.get('documento_numero', ''),
                    cfop=data['cfop'],
                    observacao=data['observacao'],
                    usuario=request.user,
                    status=CreditoCliente.Status.DISPONIVEL,
                )
                messages.success(
                    request,
                    f'Devolução registrada com sucesso (mov. #{mov.pk}). '
                    f'Crédito de R$ {valor_total:.2f} gerado para {data["cliente"]}.',
                )
            else:
                messages.success(
                    request,
                    f'Devolução de cliente registrada com sucesso (mov. #{mov.pk}).',
                )

        except DomainError as exc:
            messages.error(request, str(exc))
            perms = permissoes_estoque(request)
            return render(request, 'estoque/outras_movimentacoes/devolucao.html', {
                'title': 'Devolução de Cliente',
                'form': form,
                'cancel_url': reverse('estoque:outras-mov-hub'),
                **perms,
            })

        return redirect(reverse('estoque:outras-mov-hub'))


class DevolucaoFornecedorView(PermissaoRequiredMixin, View):
    """Devolucao de mercadoria ao fornecedor — saida do estoque com CFOP 52xx/62xx."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def _get_filial(self, request):
        return request.filial_ativa

    def get(self, request):
        filial = self._get_filial(request)
        form = DevolucaoFornecedorForm(filial=filial)
        perms = permissoes_estoque(request)
        return render(request, 'estoque/outras_movimentacoes/devolucao_fornecedor.html', {
            'title': 'Devolucao ao Fornecedor',
            'form': form,
            'cancel_url': reverse('estoque:outras-mov-hub'),
            **perms,
        })

    @transaction.atomic
    def post(self, request):
        filial = self._get_filial(request)
        form = DevolucaoFornecedorForm(request.POST, filial=filial)

        if not form.is_valid():
            perms = permissoes_estoque(request)
            return render(request, 'estoque/outras_movimentacoes/devolucao_fornecedor.html', {
                'title': 'Devolucao ao Fornecedor',
                'form': form,
                'cancel_url': reverse('estoque:outras-mov-hub'),
                **perms,
            })

        data = form.cleaned_data
        observacao = data['observacao']
        motivo_label = dict(DevolucaoFornecedorForm().fields['motivo'].choices).get(
            data['motivo'], data['motivo']
        )
        obs_completa = f"[{motivo_label}] {observacao}"
        if data.get('nota_fiscal_origem'):
            obs_completa = f"NF origem: {data['nota_fiscal_origem']} | {obs_completa}"

        try:
            movs = MovimentacaoService.registrar_saida_fefo(
                produto_id=data['produto'].pk,
                filial_id=filial.pk,
                quantidade=data['quantidade'],
                usuario_id=request.user.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_FORNECEDOR,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                documento_numero=data.get('documento_numero', ''),
            )

            messages.success(
                request,
                f'Devolucao ao fornecedor registrada com sucesso. '
                f'CFOP {data["cfop"]} — {len(movs)} lote(s) movimentado(s).',
            )

        except DomainError as exc:
            messages.error(request, str(exc))
            perms = permissoes_estoque(request)
            return render(request, 'estoque/outras_movimentacoes/devolucao_fornecedor.html', {
                'title': 'Devolucao ao Fornecedor',
                'form': form,
                'cancel_url': reverse('estoque:outras-mov-hub'),
                **perms,
            })

        return redirect(reverse('estoque:outras-mov-hub'))


class SaidaEspecialView(PermissaoRequiredMixin, View):
    """Saída especial: Bonificação, Roubo/Furto, Perda ou Deterioração."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def _get_filial(self, request):
        return request.filial_ativa

    def get(self, request):
        filial = self._get_filial(request)
        form = SaidaEspecialForm(filial=filial)
        perms = permissoes_estoque(request)
        return render(request, 'estoque/outras_movimentacoes/saida_especial.html', {
            'title': 'Saída Especial',
            'form': form,
            'cfop_map': CFOP_MAP,
            **perms,
        })

    @transaction.atomic
    def post(self, request):
        filial = self._get_filial(request)
        form = SaidaEspecialForm(request.POST, filial=filial)

        if not form.is_valid():
            perms = permissoes_estoque(request)
            return render(request, 'estoque/outras_movimentacoes/saida_especial.html', {
                'title': 'Saída Especial',
                'form': form,
                'cfop_map': CFOP_MAP,
                **perms,
            })

        data = form.cleaned_data
        tipo = data['tipo']

        tipo_operacao_map = {
            'bonificacao': MovimentacaoEstoque.TipoOperacao.BONIFICACAO,
            'roubo': MovimentacaoEstoque.TipoOperacao.ROUBO,
            'perda': MovimentacaoEstoque.TipoOperacao.PERDA,
            'deterioracao': MovimentacaoEstoque.TipoOperacao.DETERIORACAO,
        }
        tipo_operacao = tipo_operacao_map[tipo]
        cfop = CFOP_MAP[tipo]

        observacao = data['observacao']
        if data.get('documento_numero'):
            observacao = f"Doc.: {data['documento_numero']} — {observacao}"

        try:
            movs = MovimentacaoService.registrar_saida_fefo(
                produto_id=data['produto'].pk,
                filial_id=filial.pk,
                quantidade=data['quantidade'],
                usuario_id=request.user.pk,
                tipo_operacao=tipo_operacao,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                documento_numero=data.get('documento_numero', ''),
            )

            tipo_label = dict(SaidaEspecialForm.TIPO_CHOICES).get(tipo, tipo)
            messages.success(
                request,
                f'{tipo_label} registrada com sucesso. '
                f'CFOP {cfop} — {len(movs)} lote(s) movimentado(s).',
            )

        except DomainError as exc:
            messages.error(request, str(exc))
            perms = permissoes_estoque(request)
            return render(request, 'estoque/outras_movimentacoes/saida_especial.html', {
                'title': 'Saída Especial',
                'form': form,
                'cfop_map': CFOP_MAP,
                **perms,
            })

        return redirect(reverse('estoque:outras-mov-hub'))


# ────────────────────────────────────────────────────────────
# Endpoints JSON para busca typeahead (devolucao fornecedor)
# ────────────────────────────────────────────────────────────

class FornecedorSearchJsonView(PermissaoRequiredMixin, View):
    """Retorna fornecedores ativos que correspondem ao termo de busca (JSON)."""

    permissao_modulo = 'estoque'

    def get(self, request):
        q = request.GET.get('q', '').strip()
        filial = request.filial_ativa
        qs = (
            Fornecedor.objects
            .filter(filiais_vinculo__filial=filial, filiais_vinculo__ativo=True, ativo=True)
            .distinct()
        )
        if len(q) >= 2:
            qs = qs.filter(
                db_models.Q(razao_social__icontains=q)
                | db_models.Q(nome_fantasia__icontains=q)
                | db_models.Q(cpf_cnpj__icontains=q)
            )
        else:
            qs = qs.none()
        resultados = [
            {
                'id': f.pk,
                'label': f.nome_fantasia or f.razao_social,
                'detalhe': f.razao_social if f.nome_fantasia else '',
                'cnpj': f.cpf_cnpj or '',
            }
            for f in qs.order_by('razao_social')[:20]
        ]
        return JsonResponse({'results': resultados})


class ProdutoEstoqueSearchJsonView(PermissaoRequiredMixin, View):
    """Retorna produtos em estoque que correspondem ao termo de busca (JSON)."""

    permissao_modulo = 'estoque'

    def get(self, request):
        q = request.GET.get('q', '').strip()
        scope = request.GET.get('scope', 'filial')
        filial = request.filial_ativa
        if scope == 'empresa':
            empresa = request.user.empresa
            if hasattr(Produto.objects, 'for_empresa'):
                qs = Produto.objects.for_empresa(empresa).filter(ativo=True)
            else:
                qs = Produto.objects.filter(ativo=True)
        elif hasattr(Produto.objects, 'for_filial'):
            qs = Produto.objects.for_filial(filial).filter(ativo=True)
        else:
            qs = Produto.objects.filter(ativo=True)
        if len(q) >= 2 or q.isdigit():
            filtro = (
                db_models.Q(descricao__icontains=q)
                | db_models.Q(descricao_curta__icontains=q)
                | db_models.Q(codigo__icontains=q)
                | db_models.Q(codigo_barras__icontains=q)
            )
            if q.isdigit():
                filtro |= db_models.Q(pk=int(q))
            qs = qs.filter(filtro)
        else:
            qs = qs.none()
        resultados = [
            {
                'id': p.pk,
                'label': p.descricao,
                'detalhe': p.codigo or '',
                'unidade': str(p.unidade_medida) if p.unidade_medida_id else '',
                'controla_lote': p.controla_lote,
            }
            for p in qs.select_related('unidade_medida').order_by('descricao')[:25]
        ]
        return JsonResponse({'results': resultados})


# ────────────────────────────────────────────────────────────
# Endpoints JSON para busca typeahead (devolucao cliente)
# ────────────────────────────────────────────────────────────

class ClienteSearchJsonView(PermissaoRequiredMixin, View):
    """Retorna clientes ativos que correspondem ao termo de busca (JSON)."""

    permissao_modulo = 'estoque'

    def get(self, request):
        q = request.GET.get('q', '').strip()
        filial = request.filial_ativa
        qs = Cliente.objects.for_filial(filial).filter(ativo=True)
        if len(q) >= 2:
            qs = qs.filter(
                db_models.Q(razao_social__icontains=q)
                | db_models.Q(nome_fantasia__icontains=q)
                | db_models.Q(cpf_cnpj__icontains=q)
            )
        else:
            qs = qs.none()
        resultados = [
            {
                'id': c.pk,
                'label': c.nome_fantasia or c.razao_social,
                'detalhe': c.razao_social if c.nome_fantasia else '',
                'cpf_cnpj': c.cpf_cnpj or '',
            }
            for c in qs.order_by('razao_social')[:20]
        ]
        return JsonResponse({'results': resultados})


class LoteSearchJsonView(PermissaoRequiredMixin, View):
    """Retorna lotes ativos de um produto (JSON)."""

    permissao_modulo = 'estoque'

    def get(self, request):
        produto_id = request.GET.get('produto_id')
        filial = request.filial_ativa
        if not produto_id:
            return JsonResponse({'results': []})
        qs = LoteProduto.objects.filter(
            filial=filial,
            produto_id=produto_id,
            status=LoteProduto.Status.ATIVO,
        ).order_by('data_validade', 'numero_lote')
        resultados = [
            {
                'id': l.pk,
                'label': l.numero_lote + (
                    f' — Val: {l.data_validade.strftime("%d/%m/%Y")}' if l.data_validade else ''
                ),
                'quantidade_atual': float(l.quantidade_atual),
            }
            for l in qs[:30]
        ]
        return JsonResponse({'results': resultados})


class VendaDevolucaoJsonView(PermissaoRequiredMixin, View):
    """Localiza a venda original e devolve somente itens ainda devolviveis."""

    permissao_modulo = 'estoque'

    def get(self, request):
        numero = str(request.GET.get('numero') or '').strip().lstrip('#0') or '0'
        try:
            venda = (
                VendaPDV.objects.for_filial(request.filial_ativa)
                .select_related('cliente', 'documento_fiscal')
                .prefetch_related('itens__produto')
                .get(numero_venda=int(numero), status='finalizada')
            )
        except (ValueError, VendaPDV.DoesNotExist):
            return JsonResponse({'erro': 'Venda finalizada nao encontrada.'}, status=404)

        itens = []
        for item in venda.itens.all():
            devolvido = ItemDevolucaoPDV.objects.filter(item_venda=item).aggregate(
                total=db_models.Sum('quantidade_devolvida')
            )['total'] or Decimal('0')
            disponivel = item.quantidade - devolvido
            if disponivel > 0:
                itens.append({
                    'item_venda_id': item.pk,
                    'produto_id': item.produto_id,
                    'produto_nome': item.produto.descricao,
                    'lote_id': item.lote_id,
                    'quantidade': float(disponivel),
                    'quantidade_maxima': float(disponivel),
                    'valor_unitario': float(item.valor_unitario),
                })
        return JsonResponse({
            'ok': True,
            'venda_id': venda.pk,
            'numero_venda': venda.numero_venda,
            'cliente_id': venda.cliente_id,
            'cliente_nome': venda.cliente.nome_display if venda.cliente else 'Consumidor Final',
            'documento_numero': str(venda.documento_fiscal.numero) if venda.documento_fiscal_id else '',
            'chave_fiscal': venda.documento_fiscal.chave if venda.documento_fiscal_id else '',
            'itens': itens,
        })


class DevolucaoClienteApiView(PermissaoRequiredMixin, View):
    """API JSON: registra devolução de cliente com múltiplos produtos."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    @transaction.atomic
    def post(self, request):
        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        cliente_id = body.get('cliente_id')
        cfop = body.get('cfop', '1202')
        documento_numero = body.get('documento_numero', '')
        gerar_credito = bool(body.get('gerar_credito', True))
        observacao = (body.get('observacao') or '').strip()
        itens = body.get('itens', [])
        venda_id = body.get('venda_id')

        if not cliente_id:
            return JsonResponse({'erro': 'Selecione um cliente.'}, status=400)
        if not itens:
            return JsonResponse({'erro': 'Adicione ao menos um produto.'}, status=400)
        if not observacao:
            return JsonResponse({'erro': 'Informe a observação.'}, status=400)

        filial = request.filial_ativa
        try:
            cliente = Cliente.objects.for_filial(filial).get(pk=cliente_id, ativo=True)
        except Cliente.DoesNotExist:
            return JsonResponse({'erro': 'Cliente não encontrado.'}, status=400)

        venda_original = None
        itens_originais = {}
        if venda_id:
            try:
                venda_original = (
                    VendaPDV.objects.for_filial(filial)
                    .prefetch_related('itens')
                    .get(pk=venda_id, status='finalizada', cliente=cliente)
                )
            except VendaPDV.DoesNotExist:
                return JsonResponse({'erro': 'A venda original nao corresponde a este cliente.'}, status=400)
            itens_originais = {item.pk: item for item in venda_original.itens.all()}
            for i, item_data in enumerate(itens, 1):
                original = itens_originais.get(item_data.get('item_venda_id'))
                if not original or original.produto_id != item_data.get('produto_id'):
                    return JsonResponse({'erro': f'O item {i} nao pertence a venda original.'}, status=400)
                quantidade = Decimal(str(item_data.get('quantidade', 0)))
                devolvido = ItemDevolucaoPDV.objects.filter(item_venda=original).aggregate(
                    total=db_models.Sum('quantidade_devolvida')
                )['total'] or Decimal('0')
                if quantidade <= 0 or quantidade + devolvido > original.quantidade:
                    restante = original.quantidade - devolvido
                    return JsonResponse({'erro': f'Quantidade do item {i} supera o saldo devolvivel ({restante}).'}, status=400)

        movs = []
        credito_total = Decimal('0')

        for i, item_data in enumerate(itens, 1):
            produto_id = item_data.get('produto_id')
            lote_id = item_data.get('lote_id')
            try:
                quantidade = Decimal(str(item_data.get('quantidade', 0)))
            except Exception:
                return JsonResponse({'erro': f'Quantidade inválida no item {i}.'}, status=400)

            valor_unitario_raw = item_data.get('valor_unitario')
            valor_unitario = Decimal(str(valor_unitario_raw)) if valor_unitario_raw else None

            if not produto_id:
                return JsonResponse({'erro': f'Produto inválido no item {i}.'}, status=400)
            if quantidade <= 0:
                return JsonResponse({'erro': f'Quantidade deve ser maior que zero (item {i}).'}, status=400)

            if hasattr(Produto.objects, 'for_filial'):
                produto_qs = Produto.objects.for_filial(filial)
            else:
                produto_qs = Produto.objects
            try:
                produto = produto_qs.get(pk=produto_id, ativo=True)
            except Produto.DoesNotExist:
                return JsonResponse({'erro': f'Produto não encontrado (item {i}).'}, status=400)

            if produto.controla_lote and not lote_id:
                return JsonResponse(
                    {'erro': f'Informe o lote para "{produto.descricao}" (item {i}).'},
                    status=400,
                )

            lote = None
            if lote_id:
                try:
                    lote = LoteProduto.objects.get(pk=lote_id, produto=produto, filial=filial)
                except LoteProduto.DoesNotExist:
                    return JsonResponse({'erro': f'Lote inválido (item {i}).'}, status=400)

            try:
                mov = MovimentacaoService.registrar_movimentacao(
                    produto_id=produto.pk,
                    filial_id=filial.pk,
                    tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                    quantidade=quantidade,
                    usuario_id=request.user.pk,
                    lote_id=lote.pk if lote else None,
                    valor_unitario=valor_unitario,
                    documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                    documento_numero=documento_numero,
                    observacao=observacao,
                )
                movs.append(mov)
            except DomainError as exc:
                return JsonResponse({'erro': str(exc)}, status=400)

            if gerar_credito and valor_unitario:
                credito_total += valor_unitario * quantidade

        if gerar_credito and credito_total > 0:
            from apps.financeiro.models.credito_cliente import CreditoCliente
            CreditoCliente.objects.create(
                filial=filial,
                cliente=cliente,
                valor=credito_total,
                valor_utilizado=Decimal('0'),
                motivo='devolucao',
                documento_numero=documento_numero,
                cfop=cfop,
                observacao=observacao,
                usuario=request.user,
                status=CreditoCliente.Status.DISPONIVEL,
            )

        if venda_original:
            devolucao = DevolucaoPDV.objects.create(
                venda_pdv=venda_original,
                filial=filial,
                motivo=observacao,
                tipo_estorno='credito' if gerar_credito else 'sem_credito',
                valor_estorno=credito_total,
                usuario=request.user,
            )
            for item_data in itens:
                original = itens_originais[item_data['item_venda_id']]
                quantidade = Decimal(str(item_data['quantidade']))
                valor_unitario = Decimal(str(item_data.get('valor_unitario') or original.valor_unitario))
                ItemDevolucaoPDV.objects.create(
                    devolucao=devolucao,
                    item_venda=original,
                    produto=original.produto,
                    quantidade_devolvida=quantidade,
                    valor_unitario=valor_unitario,
                    valor_total=quantidade * valor_unitario,
                    motivo_item=observacao,
                )

        msg = f'{len(movs)} item(s) registrado(s) com sucesso.'
        if gerar_credito and credito_total > 0:
            msg += f' Crédito de R$ {credito_total:.2f} gerado para {cliente}.'

        return JsonResponse({'ok': True, 'message': msg})


# ────────────────────────────────────────────────────────────
# Transferência entre lojas (multi-produto)
# ────────────────────────────────────────────────────────────

class TransferenciaLojaView(PermissaoRequiredMixin, View):
    """Tela de transferência entre lojas com suporte a múltiplos produtos."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def get(self, request):
        filial = request.filial_ativa
        empresa = request.user.empresa

        filiais_qs = Filial.objects.filter(
            empresa=empresa, ativo=True,
        ).exclude(pk=filial.pk).order_by('-is_matriz', 'nome_fantasia', 'razao_social')

        filiais_json = _json.dumps([
            {
                'id': f.pk,
                'label': (f.nome_fantasia or f.razao_social) + (' (Matriz)' if f.is_matriz else ' (Filial)'),
                'is_matriz': f.is_matriz,
            }
            for f in filiais_qs
        ])

        return render(request, 'estoque/outras_movimentacoes/transferencia_lojas.html', {
            'title': 'Transferência entre Lojas',
            'filiais_json': filiais_json,
            'filial_nome': filial.nome_fantasia or filial.razao_social,
            'filial_is_matriz': filial.is_matriz,
            'cancel_url': reverse('estoque:outras-mov-hub'),
        })


class TransferenciaLojaApiView(PermissaoRequiredMixin, View):
    """API JSON: transferência entre lojas com múltiplos produtos."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    @transaction.atomic
    def post(self, request):
        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        filial_destino_id = body.get('filial_destino_id')
        observacao = (body.get('observacao') or '').strip()
        itens = body.get('itens', [])

        if not filial_destino_id:
            return JsonResponse({'erro': 'Selecione a loja de destino.'}, status=400)
        if not itens:
            return JsonResponse({'erro': 'Adicione ao menos um produto.'}, status=400)
        if not observacao:
            return JsonResponse({'erro': 'Informe a justificativa.'}, status=400)

        filial = request.filial_ativa
        empresa = request.user.empresa

        if filial_destino_id == filial.pk:
            return JsonResponse({'erro': 'Loja de destino deve ser diferente da origem.'}, status=400)

        try:
            filial_destino = Filial.objects.get(pk=filial_destino_id, empresa=empresa, ativo=True)
        except Filial.DoesNotExist:
            return JsonResponse({'erro': 'Loja de destino inválida.'}, status=400)

        resultados = []
        for i, item_data in enumerate(itens, 1):
            produto_id = item_data.get('produto_id')
            lote_id = item_data.get('lote_id') or None

            try:
                quantidade = Decimal(str(item_data.get('quantidade', 0)))
            except Exception:
                return JsonResponse({'erro': f'Quantidade inválida no item {i}.'}, status=400)

            if not produto_id:
                return JsonResponse({'erro': f'Produto inválido no item {i}.'}, status=400)
            if quantidade <= 0:
                return JsonResponse({'erro': f'Quantidade deve ser maior que zero (item {i}).'}, status=400)

            try:
                mov_saida, mov_entrada = MovimentacaoService.transferir_entre_filiais(
                    produto_id=produto_id,
                    filial_origem_id=filial.pk,
                    filial_destino_id=filial_destino.pk,
                    quantidade=quantidade,
                    usuario_id=request.user.pk,
                    lote_id=lote_id,
                    observacao=observacao,
                )
                resultados.append({'saida': mov_saida.pk, 'entrada': mov_entrada.pk})
            except DomainError as exc:
                return JsonResponse({'erro': str(exc)}, status=400)

        return JsonResponse({
            'ok': True,
            'message': f'{len(resultados)} produto(s) transferido(s) de '
                       f'"{filial.nome_fantasia or filial.razao_social}" para '
                       f'"{filial_destino.nome_fantasia or filial_destino.razao_social}".',
        })
