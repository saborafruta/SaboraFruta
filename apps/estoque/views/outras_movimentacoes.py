"""Views do módulo Outras Movimentações de Estoque."""
from __future__ import annotations

import json as _json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import models as db_models
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.cadastros.models import Cliente, Fornecedor, Motorista, Veiculo
from apps.core.models import Filial
from apps.core.services.auditoria import auditoria_para_objeto
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin
from apps.core.services.search import normalize_search_text, ranked_search_ids
from apps.estoque.forms.outras_movimentacoes import DevolucaoClienteForm, DevolucaoFornecedorForm, SaidaEspecialForm
from apps.estoque.models import (
    ConferenciaTransferencia,
    Estoque,
    ItemConferenciaTransferencia,
    LoteProduto,
    MovimentacaoEstoque,
)
from apps.estoque.services.peso_carga import calcular_peso_bruto, peso_unitario_kg
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.permissoes import permissoes_estoque
from apps.financeiro.constants.enums import StatusDocumentoFiscal
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



def _peso_unitario_para_json(produto):
    """
    Peso unitario em kg para o front, ou None quando nao ha fonte.

    None (e nao 0) porque a tela usa a ausencia para listar o produto como
    pendente; 0 seria indistinguivel de um peso legitimamente zerado.
    """
    peso, origem = peso_unitario_kg(produto)
    return float(peso) if origem != 'ausente' else None


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
        browse = request.GET.get('browse') == '1'
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
        ranked_ids = None
        if len(q) >= 2 or q.isdigit() or (browse and q):
            for term in normalize_search_text(q).split():
                term_filter = (
                    db_models.Q(descricao__icontains=term)
                    | db_models.Q(codigo__icontains=term)
                    | db_models.Q(codigo_barras__icontains=term)
                )
                if term.isdigit():
                    term_filter |= db_models.Q(pk=int(term))
                qs = qs.filter(term_filter)

            ranked_ids = ranked_search_ids(
                qs.values('pk', 'descricao', 'codigo', 'codigo_barras'),
                q,
                name_fields=('descricao',),
                code_fields=('codigo', 'codigo_barras'),
                limit=40,
            )
            qs = qs.filter(pk__in=ranked_ids)
        elif not browse:
            qs = qs.none()

        if ranked_ids is not None:
            products_by_id = {
                produto.pk: produto
                for produto in qs.select_related('unidade_medida').distinct()
            }
            produtos = [
                products_by_id[pk] for pk in ranked_ids if pk in products_by_id
            ]
        else:
            produtos = list(
                qs.select_related('unidade_medida').order_by('descricao').distinct()[:40]
            )
        saldos = {
            produto_id: float(quantidade or 0)
            for produto_id, quantidade in Estoque.objects.filter(
                filial=filial,
                produto_id__in=[produto.pk for produto in produtos],
            ).values_list('produto_id', 'quantidade_atual')
        }
        resultados = [
            {
                'id': p.pk,
                'label': p.descricao,
                'detalhe': p.codigo or '',
                'unidade': str(p.unidade_medida) if p.unidade_medida_id else '',
                'controla_lote': p.controla_lote,
                'codigo_barras': p.codigo_barras or '',
                'foto_url': (
                    reverse('produtos:produto-image-file', kwargs={'pk': p.pk})
                    if p.foto_url else ''
                ),
                'estoque': saldos.get(p.pk, 0),
                # Peso ja resolvido pela hierarquia (peso_bruto ->
                # peso_liquido -> unidade em peso): a tela precisa somar
                # exatamente o mesmo que o backend valida, senao o usuario ve
                # um total e recebe outro erro.
                'peso_bruto': _peso_unitario_para_json(p),
            }
            for p in produtos
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

def _transferencias_para_listagem(filial, usuario, limite=500):
    perfil = getattr(usuario, '_perfil_ativo', None) or usuario.perfil
    is_admin = bool(usuario.is_superuser or perfil.is_admin)
    movs = list(
        MovimentacaoEstoque.objects
        .filter(
            filial=filial,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
        )
        .exclude(documento_numero__startswith='EST-')
        .exclude(documento_numero__startswith='RAT-')
        .select_related(
            'produto',
            'produto__unidade_medida',
            'filial_destino',
            'lote',
            'documento_fiscal',
        )
        .order_by('-data_movimentacao')[:limite]
    )

    from apps.logistica.models import MDFe

    documento_ids = {
        mov.documento_fiscal_id for mov in movs if mov.documento_fiscal_id
    }
    mdfes_por_documento = {
        mdfe.documento_fiscal_id: mdfe
        for mdfe in MDFe.objects.for_filial(filial).filter(
            documento_fiscal_id__in=documento_ids,
        )
    }
    documentos_transferencia = {
        mov.documento_numero for mov in movs if mov.documento_numero
    }
    conferencias_por_documento = {
        conferencia.documento_numero: conferencia
        for conferencia in ConferenciaTransferencia.objects.filter(
            filial_origem=filial,
            documento_numero__in=documentos_transferencia,
        )
    }

    grupos = {}
    ordem = []
    for mov in movs:
        chave = mov.documento_numero or f'MOV-{mov.pk}'
        if chave not in grupos:
            doc = mov.documento_fiscal
            nota = None
            mdfe_info = None
            if doc:
                nota = {
                    'id': doc.pk,
                    'numero': doc.numero,
                    'serie': doc.serie,
                    'status': doc.status,
                    'status_label': doc.get_status_display(),
                    'codigo_status_sefaz': doc.codigo_status_sefaz,
                    'mensagem_sefaz': doc.mensagem_sefaz,
                    'chave': doc.chave or '',
                    'protocolo': doc.protocolo,
                    'pdf_danfe_url': doc.pdf_danfe_url,
                }
                mdfe = mdfes_por_documento.get(doc.pk)
                if mdfe:
                    mdfe_info = {
                        'id': mdfe.pk,
                        'numero': mdfe.numero,
                        'status': mdfe.status,
                        'status_label': mdfe.get_status_display(),
                        'mensagem_sefaz': mdfe.mensagem_sefaz,
                        'url': reverse(
                            'logistica:mdfe-detail', kwargs={'pk': mdfe.pk},
                        ),
                    }
            nota_ativa = bool(
                nota and nota['status'] == StatusDocumentoFiscal.AUTORIZADA
            )
            conferencia = conferencias_por_documento.get(chave)
            grupos[chave] = {
                'chave': chave,
                'filial_destino_nome': (
                    mov.filial_destino.nome_fantasia
                    or mov.filial_destino.razao_social
                ) if mov.filial_destino else '—',
                'data': mov.data_movimentacao,
                'observacao': mov.observacao,
                'cancelada': mov.transferencia_cancelada,
                'nota': nota,
                'mdfe': mdfe_info,
                'conferencia': {
                    'id': conferencia.pk,
                    'status': conferencia.status,
                    'status_label': conferencia.get_status_display(),
                    'observacao': conferencia.observacao_conferencia,
                    'conferida_em': conferencia.conferida_em,
                    'log_url': reverse(
                        'estoque:transferencia-conferencia-log',
                        kwargs={'pk': conferencia.pk},
                    ),
                } if conferencia else None,
                'mdfe_create_url': (
                    f"{reverse('logistica:mdfe-create')}?nfe_documento_id={doc.pk}"
                    if doc and nota_ativa and not mdfe_info
                    else ''
                ),
                'pode_emitir_nota': (
                    not nota_ativa
                    and not (
                        nota
                        and nota['status'] == StatusDocumentoFiscal.PROCESSANDO
                    )
                ),
                'pode_cancelar_nota': nota_ativa,
                'pode_cancelar_transferencia': (
                    not mov.transferencia_cancelada and not nota_ativa
                ),
                'pode_reativar_transferencia': (
                    mov.transferencia_cancelada and nota_ativa
                ),
                'pode_excluir': is_admin and not nota_ativa,
                'itens': [],
            }
            ordem.append(chave)
        grupos[chave]['itens'].append({
            'mov_saida_id': mov.pk,
            'produto_id': mov.produto_id,
            'produto_nome': mov.produto.descricao,
            'produto_unidade': (
                str(mov.produto.unidade_medida)
                if mov.produto.unidade_medida_id else ''
            ),
            'lote_id': mov.lote_id,
            'lote_nome': mov.lote.numero_lote if mov.lote_id else '',
            'quantidade': str(mov.quantidade),
        })

    return [grupos[chave] for chave in ordem], is_admin


class TransferenciaLojaListView(PermissaoRequiredMixin, View):
    """Lista transferências e centraliza seu acompanhamento fiscal."""

    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def get(self, request):
        transferencias, is_admin = _transferencias_para_listagem(
            request.filial_ativa, request.user,
        )
        q = (request.GET.get('q') or '').strip().casefold()
        status = (request.GET.get('status') or '').strip()

        if q:
            transferencias = [
                item for item in transferencias
                if q in item['chave'].casefold()
                or q in item['filial_destino_nome'].casefold()
                or any(
                    q in produto['produto_nome'].casefold()
                    for produto in item['itens']
                )
                or (
                    item['nota']
                    and q in str(item['nota']['numero']).casefold()
                )
            ]
        if status:
            def corresponde(item):
                if status == 'cancelada':
                    return item['cancelada']
                if status == 'sem_nota':
                    return not item['nota'] and not item['cancelada']
                if status == 'com_mdfe':
                    return bool(item['mdfe'])
                return bool(
                    item['nota']
                    and item['nota']['status'] == status
                    and not item['cancelada']
                )

            transferencias = [item for item in transferencias if corresponde(item)]

        totais = {
            'total': len(transferencias),
            'com_nfe': sum(bool(item['nota']) for item in transferencias),
            'com_mdfe': sum(bool(item['mdfe']) for item in transferencias),
            'com_erro': sum(
                bool(
                    item['nota']
                    and item['nota']['status'] in {
                        StatusDocumentoFiscal.REJEITADA,
                        StatusDocumentoFiscal.DENEGADA,
                    }
                )
                for item in transferencias
            ),
        }
        return render(
            request,
            'estoque/outras_movimentacoes/transferencia_lojas_list.html',
            {
                'title': 'Transferências entre Lojas',
                'transferencias': transferencias,
                'is_admin': is_admin,
                'q': request.GET.get('q', ''),
                'status_filtro': status,
                'totais': totais,
            },
        )


class TransferenciaLojaView(PermissaoRequiredMixin, View):
    """Tela de transferência entre lojas com suporte a múltiplos produtos."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def get(self, request):
        filial = request.filial_ativa
        empresa = request.user.empresa
        copiar_documento = (request.GET.get('copiar') or '').strip()
        copia = {}

        if copiar_documento:
            movs_copia = list(
                MovimentacaoEstoque.objects.filter(
                    filial=filial,
                    tipo_operacao=(
                        MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA
                    ),
                    documento_numero=copiar_documento,
                    transferencia_cancelada=True,
                ).select_related(
                    'produto',
                    'produto__unidade_medida',
                    'filial_destino',
                    'lote',
                ).order_by('pk')
            )
            if movs_copia and movs_copia[0].filial_destino_id:
                copia = {
                    'filial_destino_id': movs_copia[0].filial_destino_id,
                    'observacao': movs_copia[0].observacao or '',
                    'itens': [
                        {
                            'produto_id': mov.produto_id,
                            'produto_nome': mov.produto.descricao,
                            'unidade': (
                                str(mov.produto.unidade_medida)
                                if mov.produto.unidade_medida_id else ''
                            ),
                            'lote_id': mov.lote_id,
                            'lote_nome': (
                                mov.lote.numero_lote if mov.lote_id else ''
                            ),
                            'quantidade': float(mov.quantidade),
                            'peso_bruto': _peso_unitario_para_json(mov.produto),
                        }
                        for mov in movs_copia
                    ],
                }

        filiais = list(Filial.objects.filter(
            empresa=empresa, ativo=True,
        ).exclude(pk=filial.pk).order_by('-is_matriz', 'nome_fantasia', 'razao_social'))

        filial_destino_padrao = next(
            (
                item for item in filiais
                if item.pk == copia.get('filial_destino_id')
            ),
            next(
                (
                    item for item in filiais
                    if 'SABORAFRUTA' in (
                        item.nome_fantasia or item.razao_social or ''
                    ).upper()
                ),
                filiais[0] if len(filiais) == 1 else None,
            ),
        )

        filiais_json = _json.dumps([
            {
                'id': f.pk,
                'label': (f.nome_fantasia or f.razao_social) + (' (Matriz)' if f.is_matriz else ' (Filial)'),
                'is_matriz': f.is_matriz,
            }
            for f in filiais
        ])
        motoristas_json = _json.dumps(list(
            Motorista.objects.for_filial(filial).filter(ativo=True)
            .values("id", "nome", "cpf", "cnh").order_by("nome")
        ))
        veiculos_json = _json.dumps(list(
            Veiculo.objects.for_filial(filial).filter(ativo=True)
            .values(
                "id", "placa", "descricao", "marca", "modelo", "tara",
                "capacidade_kg", "uf_placa", "tipo_rodado", "tipo_carroceria",
            ).order_by("placa")
        ), default=str)

        return render(request, 'estoque/outras_movimentacoes/transferencia_lojas.html', {
            'title': 'Transferência entre Lojas',
            'filiais_json': filiais_json,
            'filiais': filiais,
            'filial_destino_padrao_id': (
                filial_destino_padrao.pk if filial_destino_padrao else None
            ),
            'filial_nome': filial.nome_fantasia or filial.razao_social,
            'filial_is_matriz': filial.is_matriz,
            'focusnfe_ambiente': filial.focusnfe_ambiente,
            'focusnfe_ambiente_label': (
                'Producao' if filial.focusnfe_ambiente == 1 else 'Homologacao'
            ),
            'motoristas_json': motoristas_json,
            'veiculos_json': veiculos_json,
            'copia_transferencia_json': _json.dumps(copia),
            'cancel_url': reverse('estoque:transferencia-lojas'),
        })


def _gerar_documento_numero_transferencia(filial_id):
    import secrets
    agora = timezone.now()
    return f'TRF-{agora:%y%m%d%H%M%S}{secrets.token_hex(1)}'


def _emitir_nfe_transferencia_e_vincular(
    *, filial_origem, filial_destino, itens_nota, usuario, observacao, mov_ids,
    origem_mercadoria="producao_propria",
):
    """
    Emite a NF-e de transferência e, se bem sucedida, vincula todas as
    movimentações informadas (saída + entrada) ao DocumentoFiscal criado —
    para que deixem de aparecer como "pendentes de nota".

    Retorna (doc_info_dict_ou_None, erro_ou_None, documento_ou_None).
    """
    from apps.estoque.services.transferencia_nfe import emitir_nfe_transferencia

    try:
        doc = emitir_nfe_transferencia(
            filial_origem=filial_origem,
            filial_destino=filial_destino,
            itens=itens_nota,
            usuario=usuario,
            origem_id=mov_ids[0] if mov_ids else None,
            observacao=observacao,
            origem_mercadoria=origem_mercadoria,
        )
    except DomainError as exc:
        return None, str(exc), None
    except Exception as exc:  # noqa: BLE001 — falha de integração não reverte o estoque
        return None, f'Falha ao emitir a NF-e: {exc}', None

    if mov_ids:
        MovimentacaoEstoque.objects.filter(pk__in=mov_ids).update(documento_fiscal=doc)

    return {
        'numero': doc.numero,
        'serie': doc.serie,
        'status': doc.get_status_display(),
    }, None, doc


def _custo_unitario_produto(produto, filial):
    custo = (
        Estoque.objects.filter(produto_id=produto.pk, filial=filial)
        .values_list('custo_medio', flat=True).first()
    )
    if not custo:
        custo = produto.preco_custo_medio or produto.preco_custo or Decimal('0')
    return custo


class TransferenciaLojaApiView(PermissaoRequiredMixin, View):
    """API JSON: transferência entre lojas com múltiplos produtos."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def post(self, request):
        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        filial_destino_id = body.get('filial_destino_id')
        observacao = (
            (body.get('observacao') or '').strip()
            or 'Transferência entre filiais'
        )
        itens = body.get('itens', [])
        gerar_nota = bool(body.get('gerar_nota'))
        gerar_mdfe = gerar_nota and bool(body.get('gerar_mdfe', True))
        origem_mercadoria = body.get('origem_mercadoria') or 'producao_propria'

        if not filial_destino_id:
            return JsonResponse({'erro': 'Selecione a loja de destino.'}, status=400)
        if not itens:
            return JsonResponse({'erro': 'Adicione ao menos um produto.'}, status=400)
        filial = request.filial_ativa
        empresa = request.user.empresa

        if filial_destino_id == filial.pk:
            return JsonResponse({'erro': 'Loja de destino deve ser diferente da origem.'}, status=400)

        try:
            filial_destino = Filial.objects.get(pk=filial_destino_id, empresa=empresa, ativo=True)
        except Filial.DoesNotExist:
            return JsonResponse({'erro': 'Loja de destino inválida.'}, status=400)

        motorista = None
        veiculo = None
        peso_bruto = Decimal("0")
        if gerar_nota:
            cnpj_origem = "".join(filter(str.isdigit, filial.cnpj or ""))
            cnpj_destino = "".join(filter(str.isdigit, filial_destino.cnpj or ""))
            if len(cnpj_origem) != 14 or len(cnpj_destino) != 14:
                return JsonResponse(
                    {'erro': 'Origem e destino precisam ter CNPJ válido.'}, status=400,
                )
            if cnpj_origem[:8] != cnpj_destino[:8]:
                return JsonResponse({
                    'erro': (
                        'A nota de transferência só pode ser emitida entre matriz '
                        'e filial do mesmo titular.'
                    ),
                }, status=400)
            if origem_mercadoria not in {'producao_propria', 'terceiros'}:
                return JsonResponse(
                    {'erro': 'Informe a origem fiscal dos produtos.'}, status=400,
                )

        # ── Validação prévia dos itens ──────────────────────────────
        itens_norm = []
        for i, item_data in enumerate(itens, 1):
            try:
                produto_id = int(item_data.get('produto_id'))
            except (TypeError, ValueError):
                produto_id = None
            lote_id = item_data.get('lote_id') or None
            try:
                quantidade = Decimal(str(item_data.get('quantidade', 0)))
            except Exception:
                return JsonResponse({'erro': f'Quantidade inválida no item {i}.'}, status=400)
            if not produto_id:
                return JsonResponse({'erro': f'Produto inválido no item {i}.'}, status=400)
            if quantidade <= 0:
                return JsonResponse({'erro': f'Quantidade deve ser maior que zero (item {i}).'}, status=400)
            itens_norm.append({'produto_id': produto_id, 'lote_id': lote_id, 'quantidade': quantidade})

        produtos = {
            produto.pk: produto
            for produto in Produto.objects.filter(
                pk__in=[item['produto_id'] for item in itens_norm],
            )
        }
        if any(item['produto_id'] not in produtos for item in itens_norm):
            return JsonResponse(
                {'erro': 'Um ou mais produtos da transferência não existem.'},
                status=400,
            )

        if gerar_mdfe:
            # O peso sai de uma hierarquia de fontes ja cadastradas
            # (peso_bruto -> peso_liquido -> unidade de medida em peso), em vez
            # de exigir peso_bruto em todo produto. Ver services.peso_carga.
            resultado_peso = calcular_peso_bruto([
                (produtos[item['produto_id']], item['quantidade'])
                for item in itens_norm
            ])
            peso_bruto = resultado_peso['peso_kg']

            # Peso informado a mao pelo operador tem precedencia: e a saida
            # para produto sem peso no cadastro (ou quando a balanca diverge).
            # Recusa zero/negativo aqui e o `_validar_transporte` recusa de
            # novo antes de montar o payload. Nenhum dos dois compara com a
            # capacidade do veiculo -- essa checagem nao existe hoje.
            if body.get('peso_bruto_manual'):
                try:
                    peso_bruto = Decimal(str(body.get('peso_bruto') or '0'))
                except (InvalidOperation, TypeError, ValueError):
                    peso_bruto = Decimal('0')
                if peso_bruto <= 0:
                    return JsonResponse({
                        'erro': 'Informe um peso bruto maior que zero para o MDF-e.',
                    }, status=400)

            elif resultado_peso['pendentes']:
                nomes = resultado_peso['pendentes']
                return JsonResponse({
                    'erro': (
                        'Para gerar o MDF-e, informe o peso destes produtos '
                        '(peso bruto, peso liquido ou unidade de medida em kg) '
                        f'ou digite o peso total manualmente: {", ".join(nomes)}.'
                    ),
                    'produtos_sem_peso': nomes,
                }, status=400)

            motorista = Motorista.objects.for_filial(filial).filter(
                pk=body.get('motorista_id'), ativo=True,
            ).first()
            veiculo = Veiculo.objects.for_filial(filial).filter(
                pk=body.get('veiculo_id'), ativo=True,
            ).first()
            if not motorista or not veiculo:
                return JsonResponse({
                    'erro': (
                        'Para emitir o MDF-e, selecione o motorista e o veículo.'
                    ),
                }, status=400)

            from apps.logistica.services.mdfe_focusnfe import (
                _validar_filial_mdfe,
                _validar_transporte,
            )
            try:
                _validar_filial_mdfe(filial)
                _validar_filial_mdfe(filial_destino, destino=True)
                _validar_transporte(motorista, veiculo, peso_bruto)
            except DomainError as exc:
                return JsonResponse({'erro': str(exc)}, status=400)

        # ── Transferência de estoque (atômica entre os itens) ───────
        documento_numero = _gerar_documento_numero_transferencia(filial.pk)
        try:
            with transaction.atomic():
                resultados = []
                for item in itens_norm:
                    mov_saida, mov_entrada = MovimentacaoService.transferir_entre_filiais(
                        produto_id=item['produto_id'],
                        filial_origem_id=filial.pk,
                        filial_destino_id=filial_destino.pk,
                        quantidade=item['quantidade'],
                        usuario_id=request.user.pk,
                        lote_id=item['lote_id'],
                        observacao=observacao,
                        permitir_sem_lote=True,
                        vincular_destino=True,
                        documento_numero=documento_numero,
                    )
                    resultados.append({'saida': mov_saida.pk, 'entrada': mov_entrada.pk})
        except DomainError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)

        from apps.estoque.services.conferencia_transferencia import (
            criar_conferencia_transferencia,
        )
        conferencia = criar_conferencia_transferencia(
            documento_numero=documento_numero,
            filial_origem=filial,
            filial_destino=filial_destino,
            usuario=request.user,
            observacao=observacao,
        )

        message = (
            f'{len(resultados)} produto(s) transferido(s) de '
            f'"{filial.nome_fantasia or filial.razao_social}" para '
            f'"{filial_destino.nome_fantasia or filial_destino.razao_social}".'
        )
        resposta = {
            'ok': True,
            'message': message,
            'conferencia_url': reverse(
                'estoque:transferencia-conferencia-detail',
                kwargs={'pk': conferencia.pk},
            ),
        }

        # ── Emissão da NF-e de transferência (fora da transação de
        #    estoque: se a nota falhar, o estoque já movido é mantido) ─
        if gerar_nota:
            itens_nota = []
            for item in itens_norm:
                produto = produtos[item['produto_id']]
                itens_nota.append({
                    'produto': produto,
                    'quantidade': item['quantidade'],
                    'custo_unitario': _custo_unitario_produto(produto, filial),
                })

            mov_ids = [r['saida'] for r in resultados] + [r['entrada'] for r in resultados]
            nota_info, nota_erro, documento_nfe = _emitir_nfe_transferencia_e_vincular(
                filial_origem=filial,
                filial_destino=filial_destino,
                itens_nota=itens_nota,
                usuario=request.user,
                observacao=observacao,
                mov_ids=mov_ids,
                origem_mercadoria=origem_mercadoria,
            )
            if nota_info:
                resposta['nota'] = nota_info
                resposta['message'] += f' NF-e nº {nota_info["numero"]} enviada para autorização.'
                if gerar_mdfe:
                    try:
                        from apps.logistica.services.mdfe_focusnfe import criar_mdfe_transferencia

                        mdfe = criar_mdfe_transferencia(
                            nfe=documento_nfe,
                            filial_destino=filial_destino,
                            motorista=motorista,
                            veiculo=veiculo,
                            peso_bruto=peso_bruto,
                            usuario=request.user,
                            observacao=observacao,
                        )
                        resposta['mdfe'] = {
                            'id': mdfe.pk,
                            'numero': mdfe.numero,
                            'status': mdfe.get_status_display(),
                            'url': reverse('logistica:mdfe-detail', kwargs={'pk': mdfe.pk}),
                        }
                        resposta['message'] += (
                            f' MDF-e nº {mdfe.numero} preparado e vinculado à NF-e.'
                        )
                    except Exception as exc:  # noqa: BLE001
                        resposta['mdfe_erro'] = (
                            f'A NF-e foi enviada, mas o MDF-e não foi preparado: {exc}'
                        )
            else:
                resposta['nota_erro'] = nota_erro

        return JsonResponse(resposta)


def _garantir_conferencias_recebidas(filial_destino):
    from apps.estoque.services.conferencia_transferencia import (
        garantir_conferencias_recebidas,
    )
    return garantir_conferencias_recebidas(filial_destino)


class TransferenciaConferenciaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def get(self, request):
        filial = request.filial_ativa
        _garantir_conferencias_recebidas(filial)
        status = (request.GET.get('status') or '').strip()
        conferencias = (
            ConferenciaTransferencia.objects
            .filter(filial_destino=filial)
            .select_related('filial_origem', 'conferida_por')
            .prefetch_related('itens__produto_enviado')
        )
        if status:
            conferencias = conferencias.filter(status=status)
        totais = {
            'aguardando': ConferenciaTransferencia.objects.filter(
                filial_destino=filial,
                status=ConferenciaTransferencia.Status.AGUARDANDO,
            ).count(),
            'divergencias': ConferenciaTransferencia.objects.filter(
                filial_destino=filial,
                status=ConferenciaTransferencia.Status.COM_DIVERGENCIA,
            ).count(),
            'conferidas': ConferenciaTransferencia.objects.filter(
                filial_destino=filial,
                status=ConferenciaTransferencia.Status.CONFERIDA,
            ).count(),
        }
        return render(
            request,
            'estoque/outras_movimentacoes/transferencia_conferencia_list.html',
            {
                'title': 'Recebimento de transferencias',
                'conferencias': conferencias,
                'status_filtro': status,
                'totais': totais,
                'status_choices': ConferenciaTransferencia.Status.choices,
            },
        )


class TransferenciaConferenciaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def _conferencia(self, request, pk):
        return get_object_or_404(
            ConferenciaTransferencia.objects
            .select_related(
                'filial_origem', 'filial_destino', 'criada_por',
                'conferida_por',
            )
            .prefetch_related(
                'itens__produto_enviado',
                'itens__produto_recebido',
                'itens__lote_enviado',
            ),
            pk=pk,
            filial_destino=request.filial_ativa,
        )

    def get(self, request, pk):
        conferencia = self._conferencia(request, pk)
        return render(
            request,
            'estoque/outras_movimentacoes/transferencia_conferencia_detail.html',
            {
                'title': f'Conferencia {conferencia.documento_numero}',
                'conferencia': conferencia,
                'ocorrencias': ItemConferenciaTransferencia.Ocorrencia.choices,
                'pode_editar': conferencia.status == ConferenciaTransferencia.Status.AGUARDANDO,
            },
        )

    def post(self, request, pk):
        conferencia = self._conferencia(request, pk)
        acao = request.POST.get('acao')
        try:
            if acao == 'cancelar':
                from apps.estoque.services.conferencia_transferencia import (
                    cancelar_na_conferencia,
                )
                cancelar_na_conferencia(
                    conferencia_id=conferencia.pk,
                    filial_destino=request.filial_ativa,
                    usuario=request.user,
                )
                messages.success(request, 'Transferencia cancelada e estoque estornado.')
                return redirect('estoque:transferencia-conferencia-list')

            itens = {}
            for item in conferencia.itens.all():
                prefixo = f'item_{item.pk}_'
                itens[str(item.pk)] = {
                    'ocorrencia': request.POST.get(prefixo + 'ocorrencia'),
                    'quantidade_recebida': request.POST.get(prefixo + 'quantidade_recebida'),
                    'produto_recebido_id': request.POST.get(prefixo + 'produto_recebido_id'),
                    'quantidade_produto_recebido': request.POST.get(
                        prefixo + 'quantidade_produto_recebido'
                    ),
                    'quantidade_devolvida': request.POST.get(
                        prefixo + 'quantidade_devolvida'
                    ),
                    'observacao': request.POST.get(prefixo + 'observacao'),
                }
            from apps.estoque.services.conferencia_transferencia import (
                concluir_conferencia,
            )
            resultado = concluir_conferencia(
                conferencia_id=conferencia.pk,
                filial_destino=request.filial_ativa,
                usuario=request.user,
                itens=itens,
                observacao=request.POST.get('observacao_conferencia'),
            )
            messages.success(
                request,
                f'Transferencia finalizada como: {resultado.get_status_display()}.',
            )
            return redirect(
                'estoque:transferencia-conferencia-detail',
                pk=conferencia.pk,
            )
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect(
                'estoque:transferencia-conferencia-detail',
                pk=conferencia.pk,
            )


def _quantidade_log(valor):
    try:
        return f'{Decimal(str(valor or 0)):.2f}'.replace('.', ',')
    except Exception:
        return str(valor or '-')


def _transferencia_log_entries(conferencia):
    entries = []
    registros = list(auditoria_para_objeto(conferencia, limit=100))
    for registro in registros:
        metadados = registro.metadados or {}
        evento = metadados.get('evento')
        itens = metadados.get('itens') or []
        changes = []
        for item in itens:
            produto = item.get('produto_enviado') or 'Produto'
            enviada = _quantidade_log(item.get('quantidade_enviada'))
            recebida = _quantidade_log(item.get('quantidade_recebida'))
            ocorrencia = item.get('ocorrencia') or 'ok'
            substituto = item.get('produto_recebido') or ''
            trocada = _quantidade_log(item.get('quantidade_trocada'))
            devolvida = _quantidade_log(item.get('quantidade_devolvida'))
            depois = f'Recebido: {recebida}; resultado: {ocorrencia}'
            if substituto:
                depois += f'; substituto: {substituto} ({trocada})'
            if devolvida != '0,00':
                depois += f'; devolvido: {devolvida}'
            changes.append({
                'campo': produto,
                'antes': f'Enviado: {enviada}',
                'depois': depois if evento != 'transferencia_enviada' else 'Aguardando conferencia',
            })

        if evento == 'transferencia_enviada':
            acao = 'Transferencia enviada'
            kind = 'created'
            detalhe = (
                f'{metadados.get("filial_origem", "")} para '
                f'{metadados.get("filial_destino", "")}.'
            )
        elif evento == 'conferencia_concluida':
            acao = 'Conferencia concluida'
            kind = 'stock'
            detalhe = f'Resultado: {conferencia.get_status_display()}.'
        elif registro.acao == 'cancelar':
            acao = 'Transferencia cancelada'
            kind = 'cancelled'
            detalhe = registro.justificativa or registro.objeto_descricao
        else:
            acao = registro.get_acao_display()
            kind = 'edit'
            detalhe = registro.justificativa or registro.objeto_descricao

        entries.append({
            'data': registro.criado_em,
            'usuario': registro.usuario.nome if registro.usuario else 'Sistema',
            'acao': acao,
            'quantidade': f'{len(itens)} item(ns)' if itens else '',
            'detalhes': detalhe,
            'changes': changes,
            'kind': kind,
        })

    if not entries:
        entries.append({
            'data': conferencia.created_at,
            'usuario': (
                conferencia.criada_por.nome
                if conferencia.criada_por_id else 'Sistema'
            ),
            'acao': 'Transferencia enviada',
            'quantidade': f'{conferencia.itens.count()} item(ns)',
            'detalhes': (
                f'{conferencia.filial_origem} para {conferencia.filial_destino}.'
            ),
            'changes': [],
            'kind': 'created',
        })
    return sorted(entries, key=lambda item: item['data'], reverse=True)


class TransferenciaConferenciaLogView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conferencia = get_object_or_404(
            ConferenciaTransferencia.objects
            .select_related(
                'filial_origem', 'filial_destino', 'criada_por', 'conferida_por',
            )
            .prefetch_related(
                'itens__produto_enviado',
                'itens__produto_recebido',
                'itens__lote_enviado',
            )
            .filter(
                db_models.Q(filial_origem=request.filial_ativa)
                | db_models.Q(filial_destino=request.filial_ativa)
            ),
            pk=pk,
        )
        logs = _transferencia_log_entries(conferencia)
        return render(
            request,
            'estoque/outras_movimentacoes/transferencia_conferencia_log.html',
            {
                'title': f'Historico {conferencia.documento_numero}',
                'conferencia': conferencia,
                'transferencia_logs': logs,
                'transferencia_log_usuarios': sorted({
                    item['usuario'] for item in logs if item.get('usuario')
                }),
                'transferencia_log_campos': sorted({
                    change['campo']
                    for item in logs
                    for change in item.get('changes', [])
                    if change.get('campo')
                }),
                'e_destino': conferencia.filial_destino_id == request.filial_ativa.pk,
            },
        )


class TransferenciasPendentesNFeView(PermissaoRequiredMixin, View):
    """Lista as transferências recentes desta filial (origem) com seu status
    de NF-e e cancelamento, para permitir emitir/reemitir nota, cancelar a
    emissão, cancelar a transferência ou excluí-la."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def get(self, request):
        filial = request.filial_ativa
        usuario = request.user
        perfil = getattr(usuario, '_perfil_ativo', None) or usuario.perfil
        is_admin = bool(usuario.is_superuser or perfil.is_admin)

        movs = list(
            MovimentacaoEstoque.objects
            .filter(
                filial=filial,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            )
            .exclude(documento_numero__startswith='EST-')
            .select_related('produto', 'filial_destino', 'lote', 'documento_fiscal')
            .order_by('-data_movimentacao')[:200]
        )

        from apps.logistica.models import MDFe

        documento_ids = {
            mov.documento_fiscal_id for mov in movs if mov.documento_fiscal_id
        }
        mdfes_por_documento = {
            mdfe.documento_fiscal_id: mdfe
            for mdfe in MDFe.objects.for_filial(filial).filter(
                documento_fiscal_id__in=documento_ids,
            )
        }

        grupos = {}
        ordem = []
        for mov in movs:
            chave = mov.documento_numero or f'MOV-{mov.pk}'
            if chave not in grupos:
                doc = mov.documento_fiscal
                nota = None
                mdfe_info = None
                if doc:
                    nota = {
                        'id': doc.pk,
                        'numero': doc.numero,
                        'serie': doc.serie,
                        'status': doc.status,
                        'status_label': doc.get_status_display(),
                        'codigo_status_sefaz': doc.codigo_status_sefaz,
                        'mensagem_sefaz': doc.mensagem_sefaz,
                        'chave': doc.chave or '',
                        'protocolo': doc.protocolo,
                        'pdf_danfe_url': doc.pdf_danfe_url,
                    }
                    mdfe = mdfes_por_documento.get(doc.pk)
                    if mdfe:
                        mdfe_info = {
                            'id': mdfe.pk,
                            'numero': mdfe.numero,
                            'status': mdfe.status,
                            'status_label': mdfe.get_status_display(),
                            'mensagem_sefaz': mdfe.mensagem_sefaz,
                            'url': reverse('logistica:mdfe-detail', kwargs={'pk': mdfe.pk}),
                        }
                nota_ativa = bool(nota and nota['status'] == StatusDocumentoFiscal.AUTORIZADA)
                pode_emitir_nota = not nota_ativa and not (nota and nota['status'] == StatusDocumentoFiscal.PROCESSANDO)
                grupos[chave] = {
                    'chave': chave,
                    'filial_destino_id': mov.filial_destino_id,
                    'filial_destino_nome': (
                        mov.filial_destino.nome_fantasia or mov.filial_destino.razao_social
                    ) if mov.filial_destino else '—',
                    'data': mov.data_movimentacao,
                    'observacao': mov.observacao,
                    'cancelada': mov.transferencia_cancelada,
                    'nota': nota,
                    'mdfe': mdfe_info,
                    'mdfe_create_url': (
                        f"{reverse('logistica:mdfe-create')}?nfe_documento_id={doc.pk}"
                        if doc and nota_ativa and not mdfe_info
                        else ''
                    ),
                    'pode_emitir_nota': pode_emitir_nota,
                    'pode_cancelar_nota': nota_ativa,
                    'pode_cancelar_transferencia': not mov.transferencia_cancelada and not nota_ativa,
                    'pode_excluir': is_admin and not nota_ativa,
                    'itens': [],
                }
                ordem.append(chave)
            grupos[chave]['itens'].append({
                'mov_saida_id': mov.pk,
                'produto_id': mov.produto_id,
                'produto_nome': mov.produto.descricao,
                'lote_id': mov.lote_id,
                'lote_nome': mov.lote.numero_lote if mov.lote_id else '',
                'quantidade': str(mov.quantidade),
                'custo_unitario': str(mov.valor_unitario or 0),
            })

        pendentes = [grupos[c] for c in ordem]

        return JsonResponse({
            'ok': True,
            'is_admin': is_admin,
            'pendentes': [
                {
                    **g,
                    'data': timezone.localtime(g['data']).strftime('%d/%m/%Y %H:%M'),
                }
                for g in pendentes
            ],
        })


class TransferenciaReemitirNFeApiView(PermissaoRequiredMixin, View):
    """Emite a NF-e para uma transferência já concluída (sem nota ou rejeitada)."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def post(self, request):
        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        mov_ids_saida = body.get('mov_saida_ids') or []
        if not mov_ids_saida:
            return JsonResponse({'erro': 'Nenhuma movimentação informada.'}, status=400)

        filial = request.filial_ativa

        movs = list(
            MovimentacaoEstoque.objects
            .filter(
                pk__in=mov_ids_saida,
                filial=filial,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            )
            .select_related('produto', 'filial_destino')
        )
        if not movs:
            return JsonResponse({'erro': 'Movimentações não encontradas para esta filial.'}, status=404)

        filial_destino = movs[0].filial_destino
        if not filial_destino:
            return JsonResponse({'erro': 'Filial de destino não identificada nesta movimentação.'}, status=400)
        if any(m.filial_destino_id != filial_destino.pk for m in movs):
            return JsonResponse({'erro': 'As movimentações selecionadas têm filiais de destino diferentes.'}, status=400)

        # Vincula também as respectivas movimentações de entrada (mesmo documento_numero).
        documento_numero = movs[0].documento_numero
        mov_ids_entrada = list(
            MovimentacaoEstoque.objects.filter(
                filial=filial_destino,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_ENTRADA,
                documento_numero=documento_numero,
            ).values_list('pk', flat=True)
        ) if documento_numero else []

        itens_nota = [
            {
                'produto': m.produto,
                'quantidade': m.quantidade,
                'custo_unitario': m.valor_unitario or _custo_unitario_produto(m.produto, filial),
            }
            for m in movs
        ]
        observacao = movs[0].observacao or 'Transferência entre filiais.'

        mov_ids = [m.pk for m in movs] + mov_ids_entrada
        nota_info, nota_erro, _ = _emitir_nfe_transferencia_e_vincular(
            filial_origem=filial,
            filial_destino=filial_destino,
            itens_nota=itens_nota,
            usuario=request.user,
            observacao=observacao,
            mov_ids=mov_ids,
        )
        if not nota_info:
            return JsonResponse({'erro': nota_erro}, status=400)

        return JsonResponse({'ok': True, 'nota': nota_info})


class TransferenciaConsultarNFeApiView(PermissaoRequiredMixin, View):
    """Consulta na Focus o status atual da NF-e vinculada à transferência."""

    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def post(self, request):
        from apps.fiscal.integrations.focusnfe import FocusNFeClient
        from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
        from apps.fiscal.services.focusnfe_service import FocusNFeService
        from apps.logistica.services.mdfe_focusnfe import (
            processar_nfe_transferencia_autorizada,
        )

        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        documento_numero = (body.get('documento_numero') or '').strip()
        mov = (
            MovimentacaoEstoque.objects
            .filter(
                filial=request.filial_ativa,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
                documento_numero=documento_numero,
                documento_fiscal__isnull=False,
            )
            .select_related('documento_fiscal', 'documento_fiscal__filial')
            .first()
        )
        if not mov:
            return JsonResponse(
                {'erro': 'Nenhuma NF-e vinculada a esta transferência.'},
                status=404,
            )

        documento = mov.documento_fiscal
        filial = documento.filial
        token = (filial.focusnfe_token or '').strip()
        if not token:
            return JsonResponse(
                {'erro': 'Configure o token de emissão Focus da filial.'},
                status=400,
            )

        try:
            client = FocusNFeClient(
                config=FocusNFeConfig.from_env(
                    token=token,
                    ambiente=filial.focusnfe_ambiente,
                ),
            )
            FocusNFeService(client=client).consultar(documento)
            documento.refresh_from_db()
            if not mov.transferencia_cancelada:
                processar_nfe_transferencia_autorizada(documento)
        except Exception as exc:  # noqa: BLE001
            return JsonResponse(
                {'erro': f'Não foi possível consultar a NF-e na SEFAZ: {exc}'},
                status=400,
            )

        return JsonResponse({
            'ok': True,
            'status': documento.status,
            'status_label': documento.get_status_display(),
            'mensagem_sefaz': documento.mensagem_sefaz,
        })


class TransferenciaCancelarNFeApiView(PermissaoRequiredMixin, View):
    """Cancela (via Focus/SEFAZ) a NF-e autorizada de uma transferência."""

    permissao_modulo = 'estoque'
    permissao_acao = 'cancelar'

    def post(self, request):
        from apps.estoque.services.transferencia_nfe import cancelar_nfe_transferencia

        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        documento_numero = (body.get('documento_numero') or '').strip()
        justificativa = (body.get('justificativa') or '').strip()
        if not documento_numero:
            return JsonResponse({'erro': 'Transferência não informada.'}, status=400)

        filial = request.filial_ativa
        mov = (
            MovimentacaoEstoque.objects
            .filter(
                filial=filial,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
                documento_numero=documento_numero,
                documento_fiscal__isnull=False,
            )
            .select_related('documento_fiscal')
            .first()
        )
        if not mov or not mov.documento_fiscal_id:
            return JsonResponse({'erro': 'Nenhuma NF-e vinculada a esta transferência.'}, status=404)

        try:
            doc = cancelar_nfe_transferencia(
                mov.documento_fiscal,
                justificativa,
                usuario=request.user,
            )
        except DomainError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return JsonResponse({'erro': f'Falha ao cancelar a NF-e: {exc}'}, status=400)

        return JsonResponse({
            'ok': True,
            'nota': {
                'numero': doc.numero,
                'status': doc.status,
                'status_label': doc.get_status_display(),
            },
        })


class TransferenciaCancelarApiView(PermissaoRequiredMixin, View):
    """Cancela (estorna) uma transferência entre lojas já concluída."""

    permissao_modulo = 'estoque'
    permissao_acao = 'cancelar'

    def post(self, request):
        from apps.estoque.services.transferencia_cancelamento import cancelar_transferencia

        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        documento_numero = (body.get('documento_numero') or '').strip()
        if not documento_numero:
            return JsonResponse({'erro': 'Transferência não informada.'}, status=400)

        filial = request.filial_ativa

        try:
            cancelar_transferencia(documento_numero, filial, request.user)
        except DomainError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)

        return JsonResponse({'ok': True})


class TransferenciaReativarApiView(PermissaoRequiredMixin, View):
    """Reativa no estoque uma transferência estornada que possui NF-e ativa."""

    permissao_modulo = 'estoque'
    permissao_acao = 'criar'

    def post(self, request):
        from apps.estoque.services.transferencia_cancelamento import reativar_transferencia

        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        documento_numero = (body.get('documento_numero') or '').strip()
        if not documento_numero:
            return JsonResponse({'erro': 'Transferência não informada.'}, status=400)

        try:
            reativar_transferencia(
                documento_numero,
                request.filial_ativa,
                request.user,
            )
        except DomainError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)

        return JsonResponse({'ok': True})


class TransferenciaExcluirApiView(PermissaoRequiredMixin, View):
    """Exclui definitivamente uma transferência. Restrito a administradores."""

    permissao_modulo = 'estoque'
    permissao_acao = 'cancelar'

    def post(self, request):
        from apps.estoque.services.transferencia_cancelamento import excluir_transferencia

        usuario = request.user
        perfil = getattr(usuario, '_perfil_ativo', None) or usuario.perfil
        is_admin = bool(usuario.is_superuser or perfil.is_admin)
        if not is_admin:
            return JsonResponse(
                {'erro': 'Somente administradores podem excluir transferências.'}, status=403,
            )

        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({'erro': 'JSON inválido.'}, status=400)

        documento_numero = (body.get('documento_numero') or '').strip()
        if not documento_numero:
            return JsonResponse({'erro': 'Transferência não informada.'}, status=400)

        filial = request.filial_ativa

        try:
            excluir_transferencia(documento_numero, filial, usuario)
        except DomainError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)

        return JsonResponse({'ok': True})
