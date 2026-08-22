"""Views de Categorias Financeiras."""
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import RegistroAuditoria
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.financeiro.forms.plano_contas import PlanoContasForm
from apps.financeiro.models.conta_bancaria import PlanoContas
from apps.financeiro.models.plano_contabil import PlanoContabil

# Configuracao dos 6 tipos de conta
TIPO_CONFIGS = {
    'grupo_receita':    {'tipo': 'R', 'nivel': 1, 'label': 'Grupos de Receitas',    'label_singular': 'Grupo de Receitas',    'pai_nivel': None, 'aceita_lancamento': False},
    'subgrupo_receita': {'tipo': 'R', 'nivel': 2, 'label': 'Subgrupos de Receitas', 'label_singular': 'Subgrupo de Receitas', 'pai_nivel': 1,    'aceita_lancamento': False},
    'outras_receitas':  {'tipo': 'R', 'nivel': 3, 'label': 'Categorias de Receitas','label_singular': 'Categoria de Receita', 'pai_nivel': 2,    'aceita_lancamento': True},
    'grupo_despesa':    {'tipo': 'D', 'nivel': 1, 'label': 'Grupos de Despesas',    'label_singular': 'Grupo de Despesas',    'pai_nivel': None, 'aceita_lancamento': False},
    'subgrupo_despesa': {'tipo': 'D', 'nivel': 2, 'label': 'Subgrupos de Despesas', 'label_singular': 'Subgrupo de Despesas', 'pai_nivel': 1,    'aceita_lancamento': False},
    'outras_despesas':  {'tipo': 'D', 'nivel': 3, 'label': 'Categorias de Despesas','label_singular': 'Categoria de Despesa', 'pai_nivel': 2,    'aceita_lancamento': True},
}

DEFAULT_TIPO = 'grupo_despesa'


def _get_empresa(request):
    filial = request.filial_ativa
    return filial.empresa if filial else None


def _proximo_codigo_categoria(empresa, nivel, conta_pai=None):
    prefixo = conta_pai.codigo if conta_pai else ''
    largura = 2 if nivel == 2 else 5
    codigos = PlanoContas.objects.filter(
        empresa=empresa, tipo='D', nivel=nivel, conta_pai=conta_pai,
    ).values_list('codigo', flat=True)
    numeros = []
    for codigo in codigos:
        sufixo = codigo[len(prefixo):] if prefixo and codigo.startswith(prefixo) else codigo
        if sufixo.isdigit():
            numeros.append(int(sufixo))
    if nivel == 1:
        candidato = str(max(numeros, default=299) + 1)
    else:
        candidato = f'{prefixo}{max(numeros, default=0) + 1:0{largura}d}'
    while PlanoContas.objects.filter(empresa=empresa, codigo=candidato).exists():
        numeros.append(int(candidato[len(prefixo):] or 0))
        candidato = (
            str(max(numeros) + 1) if nivel == 1
            else f'{prefixo}{max(numeros) + 1:0{largura}d}'
        )
    return candidato[:20]


class PlanoContasQuickCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    @transaction.atomic
    def post(self, request):
        empresa = _get_empresa(request)
        try:
            nivel = int(request.POST.get('nivel', '0'))
        except ValueError:
            nivel = 0
        descricao = request.POST.get('descricao', '').strip()
        if nivel not in (1, 2, 3) or not descricao:
            return JsonResponse({'erro': 'Informe o nome da nova categoria.'}, status=400)

        conta_pai = None
        if nivel > 1:
            conta_pai = PlanoContas.objects.filter(
                pk=request.POST.get('conta_pai'), empresa=empresa,
                tipo='D', nivel=nivel - 1, ativo=True,
            ).first()
            if not conta_pai:
                return JsonResponse({'erro': 'Selecione primeiro o nivel anterior.'}, status=400)

        if PlanoContas.objects.filter(
            empresa=empresa, tipo='D', nivel=nivel, conta_pai=conta_pai,
            descricao__iexact=descricao,
        ).exists():
            return JsonResponse({'erro': 'Ja existe uma categoria com esse nome neste nivel.'}, status=400)

        conta_contabil = None
        if nivel == 3:
            conta_contabil = PlanoContabil.objects.filter(
                pk=request.POST.get('conta_contabil'), empresa=empresa,
                tipo_conta=PlanoContabil.TipoConta.ANALITICA, ativo=True,
            ).first()
            if not conta_contabil:
                return JsonResponse(
                    {'erro': 'Selecione a conta contabil analitica desta categoria.'},
                    status=400,
                )

        conta = PlanoContas.objects.create(
            empresa=empresa,
            conta_pai=conta_pai,
            conta_contabil=conta_contabil,
            codigo=_proximo_codigo_categoria(empresa, nivel, conta_pai),
            descricao=descricao,
            tipo='D',
            nivel=nivel,
            aceita_lancamento=nivel == 3,
            ativo=True,
        )
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.CRIAR,
            objeto=conta,
            descricao=f'Categoria financeira criada: {conta.caminho_descricao}',
            depois=snapshot_modelo(conta),
            metadados={'origem': 'cadastro_rapido'},
        )
        return JsonResponse({
            'ok': True,
            'categoria': {
                'id': conta.pk,
                'descricao': conta.descricao,
                'codigo': conta.codigo,
                'pai_id': conta.conta_pai_id,
                'nivel': conta.nivel,
            },
        })


class PlanoContasListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        empresa = _get_empresa(request)
        tipo_key = request.GET.get('tipo', DEFAULT_TIPO)
        if tipo_key not in TIPO_CONFIGS:
            tipo_key = DEFAULT_TIPO

        cfg = TIPO_CONFIGS[tipo_key]
        q = request.GET.get('q', '').strip()

        contas_qs = PlanoContas.objects.none()
        if empresa:
            contas_qs = (
                PlanoContas.objects
                .filter(empresa=empresa, tipo=cfg['tipo'], nivel=cfg['nivel'])
                .select_related('conta_pai', 'conta_contabil')
                .order_by('codigo')
            )
            if q:
                contas_qs = contas_qs.filter(
                    descricao__icontains=q
                ) | contas_qs.filter(codigo__icontains=q)
                contas_qs = contas_qs.distinct().order_by('codigo')

        return render(request, 'financeiro/plano_contas/list.html', {
            'title': 'Categorias Financeiras',
            'tipo_key': tipo_key,
            'cfg': cfg,
            'contas': contas_qs,
            'total': contas_qs.count(),
            'tipo_configs': TIPO_CONFIGS,
            'q': q,
            'pode_editar': True,
        })


class PlanoContasCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def _get_tipo_key(self, request):
        tipo_key = request.GET.get('tipo', DEFAULT_TIPO)
        return tipo_key if tipo_key in TIPO_CONFIGS else DEFAULT_TIPO

    def get(self, request):
        empresa = _get_empresa(request)
        tipo_key = self._get_tipo_key(request)
        cfg = TIPO_CONFIGS[tipo_key]
        form = PlanoContasForm(empresa=empresa, tipo_key=tipo_key)
        return render(request, 'financeiro/plano_contas/form.html', {
            'title': f'Nova conta - {cfg["label_singular"]}',
            'form': form,
            'tipo_key': tipo_key,
            'cfg': cfg,
            'cancel_url': reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}',
        })

    def post(self, request):
        empresa = _get_empresa(request)
        tipo_key = request.POST.get('tipo_key', DEFAULT_TIPO)
        if tipo_key not in TIPO_CONFIGS:
            tipo_key = DEFAULT_TIPO
        cfg = TIPO_CONFIGS[tipo_key]
        form = PlanoContasForm(request.POST, empresa=empresa, tipo_key=tipo_key)
        if form.is_valid():
            conta = form.save(commit=False)
            conta.empresa = empresa
            conta.tipo = cfg['tipo']
            conta.nivel = cfg['nivel']
            conta.aceita_lancamento = cfg['aceita_lancamento']
            conta.save()
            messages.success(
                request,
                f"'{conta.codigo} - {conta.descricao}' cadastrado com sucesso.",
            )
            return redirect(reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}')
        return render(request, 'financeiro/plano_contas/form.html', {
            'title': f'Nova conta - {cfg["label_singular"]}',
            'form': form,
            'tipo_key': tipo_key,
            'cfg': cfg,
            'cancel_url': reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}',
        })


class PlanoContasEditView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def _get_conta_e_tipo(self, request, pk):
        empresa = _get_empresa(request)
        conta = get_object_or_404(PlanoContas, pk=pk, empresa=empresa)
        # Detecta o tipo_key pela conta
        for key, cfg in TIPO_CONFIGS.items():
            if cfg['tipo'] == conta.tipo and cfg['nivel'] == conta.nivel:
                return conta, key, cfg
        return conta, DEFAULT_TIPO, TIPO_CONFIGS[DEFAULT_TIPO]

    def get(self, request, pk):
        conta, tipo_key, cfg = self._get_conta_e_tipo(request, pk)
        form = PlanoContasForm(instance=conta, empresa=conta.empresa, tipo_key=tipo_key)
        return render(request, 'financeiro/plano_contas/form.html', {
            'title': f'Editar - {conta.codigo}',
            'form': form,
            'conta': conta,
            'tipo_key': tipo_key,
            'cfg': cfg,
            'cancel_url': reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}',
        })

    def post(self, request, pk):
        conta, tipo_key, cfg = self._get_conta_e_tipo(request, pk)
        form = PlanoContasForm(request.POST, instance=conta, empresa=conta.empresa, tipo_key=tipo_key)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"'{conta.codigo} - {conta.descricao}' atualizado.",
            )
            return redirect(reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}')
        return render(request, 'financeiro/plano_contas/form.html', {
            'title': f'Editar - {conta.codigo}',
            'form': form,
            'conta': conta,
            'tipo_key': tipo_key,
            'cfg': cfg,
            'cancel_url': reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}',
        })


class PlanoContasToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        empresa = _get_empresa(request)
        conta = get_object_or_404(PlanoContas, pk=pk, empresa=empresa)
        conta.ativo = not conta.ativo
        conta.save(update_fields=['ativo'])
        estado = 'ativada' if conta.ativo else 'desativada'
        messages.success(request, f"Conta '{conta.codigo}' {estado}.")
        # Detecta tipo_key para redirecionar de volta
        tipo_key = DEFAULT_TIPO
        for key, cfg in TIPO_CONFIGS.items():
            if cfg['tipo'] == conta.tipo and cfg['nivel'] == conta.nivel:
                tipo_key = key
                break
        return redirect(reverse('financeiro:plano_contas_list') + f'?tipo={tipo_key}')
