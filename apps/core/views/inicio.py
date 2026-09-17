from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.core.services.home import nome_rota_inicial, usuario_e_administrador
from apps.core.models import Notificacao
from apps.core.views.menu_favoritos import listar_links_menu, resolver_favoritos


class HomeRedirectView(LoginRequiredMixin, TemplateView):
    """Entrada unica: dashboard para administradores e Inicio para a equipe."""

    def get(self, request, *args, **kwargs):
        return redirect(nome_rota_inicial(request.user, getattr(request, 'filial_ativa', None)))


class InicioView(LoginRequiredMixin, TemplateView):
    template_name = 'core/inicio.html'

    def dispatch(self, request, *args, **kwargs):
        if usuario_e_administrador(request.user, getattr(request, 'filial_ativa', None)):
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agora = timezone.localtime()
        if agora.hour < 12:
            saudacao = 'Bom dia'
        elif agora.hour < 18:
            saudacao = 'Boa tarde'
        else:
            saudacao = 'Boa noite'

        html_menu = render_to_string('core/_sidebar.html', request=self.request)
        disponiveis = listar_links_menu(html_menu)
        por_caminho = {item['caminho']: item for item in disponiveis}
        caminhos_padrao = [
            reverse('cadastros:cliente-list'),
            reverse('produtos:produto-list'),
            reverse('estoque:estoque-list'),
            reverse('pdv:home'),
        ]
        favoritos = resolver_favoritos(self.request.user.menu_favoritos, html_menu)
        acessos_rapidos = []
        for caminho in caminhos_padrao:
            if caminho in por_caminho:
                acessos_rapidos.append(por_caminho[caminho])
        for item in favoritos:
            if item['caminho'] not in {acesso['caminho'] for acesso in acessos_rapidos}:
                acessos_rapidos.append(item)

        filial = getattr(self.request, 'filial_ativa', None)
        notificacoes_pendentes = []
        if filial is not None:
            notificacoes_pendentes = list(
                Notificacao.objects.filter(filial=filial, ativa=True)
                .exclude(leituras__usuario_id=self.request.user.pk)[:15]
            )

        context.update({
            'saudacao': saudacao,
            'primeiro_nome': (self.request.user.nome or self.request.user.email).split()[0],
            'acessos_rapidos': acessos_rapidos,
            'acessos_disponiveis': disponiveis,
            'caminhos_favoritos': self.request.user.menu_favoritos or [],
            'notificacoes_pendentes': notificacoes_pendentes,
        })
        return context
