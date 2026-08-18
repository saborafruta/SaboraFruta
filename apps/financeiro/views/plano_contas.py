"""Views de Categorias Financeiras."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.forms.plano_contas import PlanoContasForm
from apps.financeiro.models.conta_bancaria import PlanoContas

# Configuracao dos 6 tipos de conta
TIPO_CONFIGS = {
    'grupo_receita':    {'tipo': 'R', 'nivel': 1, 'label': 'Grupos de Receitas',    'label_singular': 'Grupo de Receitas',    'pai_nivel': None, 'aceita_lancamento': False},
    'subgrupo_receita': {'tipo': 'R', 'nivel': 2, 'label': 'Subgrupos de Receitas', 'label_singular': 'Subgrupo de Receitas', 'pai_nivel': 1,    'aceita_lancamento': False},
    'outras_receitas':  {'tipo': 'R', 'nivel': 3, 'label': 'Categorias de Receitas','label_singular': 'Categoria de Receita', 'pai_nivel': 2,    'aceita_lancamento': True},
    'grupo_despesa':    {'tipo': 'D', 'nivel': 1, 'label': 'Grupos de Despesas',    'label_singular': 'Grupo de Despesas',    'pai_nivel': None, 'aceita_lancamento': False},
    'subgrupo_despesa': {'tipo': 'D', 'nivel': 2, 'label': 'Subgrupos de Despesas', 'label_singular': 'Subgrupo de Despesas', 'pai_nivel': 1,    'aceita_lancamento': False},
    'outras_despesas':  {'tipo': 'D', 'nivel': 3, 'label': 'Categorias de Despesas','label_singular': 'Categoria de Despesa', 'pai_nivel': 2,    'aceita_lancamento': True},
}

DEFAULT_TIPO = 'grupo_receita'


def _get_empresa(request):
    filial = request.filial_ativa
    return filial.empresa if filial else None


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
