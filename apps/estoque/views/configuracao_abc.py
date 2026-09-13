"""Ajuste de meta de estoque por classe da curva ABC (giro) -- 3 linhas fixas (A/B/C) por empresa."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.forms import ConfiguracaoAbcEstoqueForm
from apps.estoque.models import ConfiguracaoAbcEstoque
from apps.estoque.views.permissoes import permissoes_estoque


class ConfiguracaoAbcEstoqueView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'
    template_name = 'estoque/configuracao_abc/form.html'

    def _linhas(self, empresa):
        return {
            classe: ConfiguracaoAbcEstoque.objects.get_or_create(empresa=empresa, classe=classe)[0]
            for classe, _ in ConfiguracaoAbcEstoque.Classe.choices
        }

    def get(self, request):
        linhas = self._linhas(request.user.empresa)
        forms = {
            classe: ConfiguracaoAbcEstoqueForm(instance=linha, prefix=classe)
            for classe, linha in linhas.items()
        }
        return render(request, self.template_name, {
            'title': 'Meta de estoque por curva ABC',
            'forms': forms,
            'permissoes_estoque': permissoes_estoque(request),
        })

    def post(self, request):
        if not request.user.tem_permissao('estoque', 'editar'):
            messages.error(request, 'Você não tem permissão para esta ação.')
            return redirect('estoque:configuracao-abc')

        linhas = self._linhas(request.user.empresa)
        forms = {
            classe: ConfiguracaoAbcEstoqueForm(request.POST, instance=linha, prefix=classe)
            for classe, linha in linhas.items()
        }
        if all(form.is_valid() for form in forms.values()):
            for classe, form in forms.items():
                antes = snapshot_modelo(linhas[classe])
                salvo = form.save()
                registrar_auditoria(
                    request=request, modulo='estoque', acao='editar', objeto=salvo,
                    descricao=f'Configuração ABC de estoque (classe {classe}) atualizada',
                    antes=antes, depois=snapshot_modelo(salvo),
                )
            messages.success(request, 'Configuração salva.')
            return redirect('estoque:configuracao-abc')
        return render(request, self.template_name, {
            'title': 'Meta de estoque por curva ABC',
            'forms': forms,
            'permissoes_estoque': permissoes_estoque(request),
        })
