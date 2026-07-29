from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.core.models import Notificacao, NotificacaoLeitura


class NotificacaoAbrirView(LoginRequiredMixin, View):
    def get(self, request, pk):
        notificacao = get_object_or_404(
            Notificacao,
            pk=pk,
            filial=getattr(request, 'filial_ativa', None),
            ativa=True,
        )
        NotificacaoLeitura.objects.get_or_create(
            notificacao=notificacao,
            usuario=request.user,
        )
        destino = notificacao.url or reverse('core:dashboard')
        if not url_has_allowed_host_and_scheme(
            destino,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            destino = reverse('core:dashboard')
        return redirect(destino)


class NotificacaoMarcarTodasView(LoginRequiredMixin, View):
    def post(self, request):
        notificacoes = Notificacao.objects.filter(
            filial=getattr(request, 'filial_ativa', None),
            ativa=True,
        ).exclude(leituras__usuario=request.user)
        NotificacaoLeitura.objects.bulk_create(
            [
                NotificacaoLeitura(notificacao=item, usuario=request.user)
                for item in notificacoes
            ],
            ignore_conflicts=True,
        )
        return JsonResponse({'ok': True})


class NotificacaoStatusView(LoginRequiredMixin, View):
    def get(self, request):
        filial = getattr(request, 'filial_ativa', None)
        if not filial:
            return JsonResponse({'nao_lidas': 0, 'notificacoes': []})

        from apps.estoque.services.conferencia_transferencia import (
            garantir_conferencias_recebidas,
        )
        garantir_conferencias_recebidas(filial)

        base = Notificacao.objects.filter(filial=filial, ativa=True)
        ids_lidas = set(
            base.filter(leituras__usuario=request.user)
            .values_list('pk', flat=True)
        )
        recentes = list(base[:15])
        return JsonResponse({
            'nao_lidas': base.exclude(leituras__usuario=request.user).count(),
            'notificacoes': [
                {
                    'id': item.pk,
                    'titulo': item.titulo,
                    'mensagem': item.mensagem,
                    'url': reverse(
                        'core:notificacao-abrir',
                        kwargs={'pk': item.pk},
                    ),
                    'criada_em': item.created_at.isoformat(),
                    'lida': item.pk in ids_lidas,
                }
                for item in recentes
            ],
        })
