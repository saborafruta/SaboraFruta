import json
import re

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.services.permissions import PermissaoRequiredMixin

from .forms import ConfiguracaoWhatsAppForm
from .gateway import EvolutionClient, GatewayWhatsAppError, qr_data_url
from .models import ConfiguracaoWhatsApp, ConversaWhatsApp
from .webhook import receber_evento


def _configuracao(request):
    filial = request.filial_ativa
    identificador = re.sub(r'\D', '', filial.cnpj or '') or str(filial.pk)
    base = slugify(filial.nome_fantasia or filial.razao_social)[:40] or 'empresa'
    configuracao, _ = ConfiguracaoWhatsApp.objects.for_filial(filial).get_or_create(
        filial=filial,
        defaults={
            'instancia': f'ited-{identificador}-{base}',
            'agente_ativo': True,
        },
    )
    return configuracao


def _gateway_configurado():
    return bool(
        getattr(settings, 'WHATSAPP_EVOLUTION_URL', '')
        and getattr(settings, 'WHATSAPP_EVOLUTION_API_KEY', '')
    )


def _webhook_url(request, configuracao):
    return request.build_absolute_uri(
        reverse('whatsapp_agent:webhook', kwargs={'secret': configuracao.webhook_secret}),
    )


class ConfiguracaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def get(self, request):
        configuracao = _configuracao(request)
        form = ConfiguracaoWhatsAppForm(instance=configuracao)
        return render(request, 'whatsapp_agent/configuracao.html', {
            'form': form, 'configuracao': configuracao,
            'gateway_configurado': _gateway_configurado(),
        })

    def post(self, request):
        configuracao = _configuracao(request)
        form = ConfiguracaoWhatsAppForm(request.POST, instance=configuracao)
        if form.is_valid():
            configuracao = form.save(commit=False)
            configuracao.filial = request.filial_ativa
            configuracao.save()
            messages.success(request, 'Configuração do WhatsApp salva.')
            return redirect('whatsapp_agent:configuracao')
        return render(request, 'whatsapp_agent/configuracao.html', {
            'form': form, 'configuracao': configuracao,
            'gateway_configurado': _gateway_configurado(),
        })


@method_decorator(require_POST, name='dispatch')
class ConectarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request):
        configuracao = get_object_or_404(
            ConfiguracaoWhatsApp.objects.for_filial(request.filial_ativa), ativo=True,
        )
        try:
            cliente = EvolutionClient(configuracao)
            webhook_url = _webhook_url(request, configuracao)
            try:
                cliente.estado()
            except GatewayWhatsAppError:
                cliente.criar_instancia(webhook_url)
            cliente.configurar_webhook(webhook_url)
            configuracao.status = ConfiguracaoWhatsApp.Status.AGUARDANDO_QR
            configuracao.ultimo_erro = ''
            configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        except GatewayWhatsAppError as exc:
            configuracao.status = ConfiguracaoWhatsApp.Status.ERRO
            configuracao.ultimo_erro = str(exc)
            configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
            messages.error(request, str(exc))
            return redirect('whatsapp_agent:configuracao')
        return redirect('whatsapp_agent:conexao')


class ConexaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def get(self, request):
        configuracao = get_object_or_404(
            ConfiguracaoWhatsApp.objects.for_filial(request.filial_ativa), ativo=True,
        )
        return render(request, 'whatsapp_agent/conexao.html', {'configuracao': configuracao})


class StatusConexaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def get(self, request):
        configuracao = get_object_or_404(
            ConfiguracaoWhatsApp.objects.for_filial(request.filial_ativa), ativo=True,
        )
        try:
            cliente = EvolutionClient(configuracao)
            estado = cliente.estado()
            if estado == 'open':
                configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
                configuracao.ultima_conexao_em = timezone.now()
                configuracao.ultimo_erro = ''
                configuracao.save(update_fields=['status', 'ultima_conexao_em', 'ultimo_erro', 'updated_at'])
                return JsonResponse({'conectado': True, 'status': configuracao.get_status_display()})
            dados_qr = cliente.conectar()
            configuracao.status = ConfiguracaoWhatsApp.Status.AGUARDANDO_QR
            configuracao.save(update_fields=['status', 'updated_at'])
            return JsonResponse({
                'conectado': False, 'status': configuracao.get_status_display(),
                'qr_code': qr_data_url(dados_qr), 'pairing_code': dados_qr.get('pairingCode', ''),
            })
        except GatewayWhatsAppError as exc:
            configuracao.status = ConfiguracaoWhatsApp.Status.ERRO
            configuracao.ultimo_erro = str(exc)
            configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
            return JsonResponse({'conectado': False, 'erro': str(exc)}, status=502)


class ConversaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        conversas = (
            ConversaWhatsApp.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'configuracao')[:100]
        )
        return render(request, 'whatsapp_agent/conversa_list.html', {'conversas': conversas})


class ConversaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conversa = get_object_or_404(
            ConversaWhatsApp.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'configuracao').prefetch_related('mensagens'),
            pk=pk,
        )
        return render(request, 'whatsapp_agent/conversa_detail.html', {'conversa': conversa})


@method_decorator(require_POST, name='dispatch')
class RetomarAgenteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        conversa = get_object_or_404(
            ConversaWhatsApp.objects.for_filial(request.filial_ativa), pk=pk,
        )
        conversa.atendimento_humano = False
        conversa.etapa = 'inicio'
        conversa.contexto = {}
        conversa.save(update_fields=['atendimento_humano', 'etapa', 'contexto', 'updated_at'])
        messages.success(request, 'Atendimento automático retomado.')
        return redirect('whatsapp_agent:conversa-detail', pk=pk)


@csrf_exempt
@require_POST
def webhook(request, secret):
    try:
        tamanho = int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        tamanho = 0
    if tamanho > 1_000_000:
        return JsonResponse({'erro': 'Payload muito grande.'}, status=413)
    configuracao = ConfiguracaoWhatsApp.objects.filter(
        webhook_secret=secret, ativo=True,
    ).select_related('filial').first()
    if not configuracao:
        return JsonResponse({'erro': 'Webhook inválido.'}, status=404)
    try:
        payload = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)
    resultado = receber_evento(configuracao, payload)
    return JsonResponse({'ok': True, 'resultado': resultado})
