import json
import re

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import OuterRef, Subquery
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

from .conversation_service import encerrar_conversa, encerrar_conversas_inativas
from .flow import garantir_fluxo_padrao
from .forms import ConfiguracaoWhatsAppForm, FluxoWhatsAppForm
from .gateway import EvolutionClient, GatewayWhatsAppError, qr_data_url
from .models import (
    ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp,
    MenuWhatsApp, OpcaoMenuWhatsApp,
)
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


def _fluxo_serializado(configuracao):
    garantir_fluxo_padrao(configuracao)
    return [
        {
            'codigo': menu.codigo,
            'nome': menu.nome,
            'mensagem': menu.mensagem,
            'principal': menu.principal,
            'ativo': menu.ativo,
            'opcoes': [
                {
                    'chave': opcao.chave,
                    'titulo': opcao.titulo,
                    'acao': opcao.acao,
                    'mensagem': opcao.mensagem,
                    'palavras_chave': opcao.palavras_chave,
                    'menu_destino': opcao.menu_destino.codigo if opcao.menu_destino else '',
                    'voltar_ao_menu': opcao.voltar_ao_menu,
                    'ativo': opcao.ativo,
                }
                for opcao in menu.opcoes.all()
            ],
        }
        for menu in configuracao.menus.prefetch_related('opcoes', 'opcoes__menu_destino')
    ]


def _validar_fluxo(valor):
    try:
        menus = json.loads(valor or '[]')
    except json.JSONDecodeError as exc:
        raise ValueError('Não foi possível ler a configuração do menu.') from exc
    if not isinstance(menus, list) or not 1 <= len(menus) <= 10:
        raise ValueError('Crie entre 1 e 10 menus.')

    codigos = set()
    acoes = {item[0] for item in OpcaoMenuWhatsApp.Acao.choices}
    for indice, menu in enumerate(menus):
        if not isinstance(menu, dict):
            raise ValueError('Há um menu inválido na configuração.')
        codigo = slugify(menu.get('codigo') or menu.get('nome') or f'menu-{indice + 1}')[:60]
        if not codigo or codigo in codigos:
            raise ValueError('Cada menu precisa ter um nome e um código diferentes.')
        codigos.add(codigo)
        menu['codigo'] = codigo
        menu['nome'] = str(menu.get('nome') or '').strip()[:80]
        menu['mensagem'] = str(menu.get('mensagem') or '').strip()
        if not menu['nome'] or not menu['mensagem']:
            raise ValueError('Informe o nome e a mensagem de todos os menus.')
        opcoes = menu.get('opcoes') or []
        if not isinstance(opcoes, list) or len(opcoes) > 20:
            raise ValueError('Cada menu pode ter no máximo 20 opções.')
        chaves = set()
        for opcao in opcoes:
            chave = str(opcao.get('chave') or '').strip()[:20]
            titulo = str(opcao.get('titulo') or '').strip()[:120]
            acao = opcao.get('acao')
            if not chave or not titulo or acao not in acoes or chave in chaves:
                raise ValueError('Revise as chaves, os títulos e as ações das opções.')
            chaves.add(chave)
            opcao['chave'] = chave
            opcao['titulo'] = titulo
    principais = [menu for menu in menus if menu.get('principal')]
    if len(principais) > 1:
        raise ValueError('Somente um menu pode ser definido como principal.')
    if not principais:
        menus[0]['principal'] = True
    if not next(menu for menu in menus if menu.get('principal')).get('ativo', True):
        raise ValueError('O menu principal precisa estar ativo.')
    for menu in menus:
        for opcao in menu.get('opcoes') or []:
            destino = opcao.get('menu_destino')
            if opcao['acao'] == OpcaoMenuWhatsApp.Acao.ABRIR_MENU and destino not in codigos:
                raise ValueError('Selecione um menu de destino válido nas ações de submenu.')
    return menus


@transaction.atomic
def _salvar_fluxo(configuracao, menus):
    existentes = {menu.codigo: menu for menu in configuracao.menus.all()}
    salvos = {}
    for ordem, dados in enumerate(menus):
        menu = existentes.get(dados['codigo']) or MenuWhatsApp(
            filial=configuracao.filial, configuracao=configuracao, codigo=dados['codigo'],
        )
        menu.nome = dados['nome']
        menu.mensagem = dados['mensagem']
        menu.principal = bool(dados.get('principal'))
        menu.ativo = bool(dados.get('ativo', True))
        menu.ordem = ordem
        menu.save()
        salvos[menu.codigo] = menu

    configuracao.menus.exclude(codigo__in=salvos).delete()
    for dados in menus:
        menu = salvos[dados['codigo']]
        menu.opcoes.all().delete()
        for ordem, dados_opcao in enumerate(dados.get('opcoes') or []):
            OpcaoMenuWhatsApp.objects.create(
                menu=menu,
                chave=dados_opcao['chave'],
                titulo=dados_opcao['titulo'],
                acao=dados_opcao['acao'],
                mensagem=str(dados_opcao.get('mensagem') or '').strip(),
                palavras_chave=str(dados_opcao.get('palavras_chave') or '').strip(),
                menu_destino=salvos.get(dados_opcao.get('menu_destino')),
                voltar_ao_menu=bool(dados_opcao.get('voltar_ao_menu')),
                ativo=bool(dados_opcao.get('ativo', True)),
                ordem=ordem,
            )


class FluxoConfiguracaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def get(self, request):
        configuracao = _configuracao(request)
        return render(request, 'whatsapp_agent/fluxo_configuracao.html', {
            'form': FluxoWhatsAppForm(instance=configuracao),
            'fluxo': _fluxo_serializado(configuracao),
            'acoes': list(OpcaoMenuWhatsApp.Acao.choices),
        })

    def post(self, request):
        configuracao = _configuracao(request)
        form = FluxoWhatsAppForm(request.POST, instance=configuracao)
        try:
            fluxo = _validar_fluxo(request.POST.get('fluxo_json'))
        except ValueError as exc:
            fluxo = _fluxo_serializado(configuracao)
            form.add_error(None, str(exc))
        if form.is_valid():
            with transaction.atomic():
                form.save()
                _salvar_fluxo(configuracao, fluxo)
            messages.success(request, 'Fluxo de atendimento salvo e já disponível no WhatsApp.')
            return redirect('whatsapp_agent:fluxo-configuracao')
        return render(request, 'whatsapp_agent/fluxo_configuracao.html', {
            'form': form, 'fluxo': fluxo,
            'acoes': list(OpcaoMenuWhatsApp.Acao.choices),
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
        encerrar_conversas_inativas(filial=request.filial_ativa)
        ultima_mensagem = MensagemWhatsApp.objects.filter(
            conversa_id=OuterRef('pk'),
        ).order_by('-created_at')
        conversas = list(
            ConversaWhatsApp.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'configuracao')
            .annotate(
                ultima_mensagem_texto=Subquery(ultima_mensagem.values('texto')[:1]),
            )[:200]
        )
        cores = {
            ConversaWhatsApp.EtapaCRM.NOVA: '#3b82f6',
            ConversaWhatsApp.EtapaCRM.INTERESSADO: '#8b5cf6',
            ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO: '#f59e0b',
            ConversaWhatsApp.EtapaCRM.AGUARDANDO_CLIENTE: '#eab308',
            ConversaWhatsApp.EtapaCRM.AGENDADO: '#22c55e',
            ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO: '#06b6d4',
            ConversaWhatsApp.EtapaCRM.CONCLUIDO: '#10b981',
            ConversaWhatsApp.EtapaCRM.NAO_CONVERTIDO: '#64748b',
        }
        colunas = [
            {
                'codigo': codigo,
                'titulo': titulo,
                'cor': cores[codigo],
                'conversas': [item for item in conversas if item.etapa_crm == codigo],
            }
            for codigo, titulo in ConversaWhatsApp.EtapaCRM.choices
        ]
        return render(request, 'whatsapp_agent/conversa_list.html', {
            'conversas': conversas,
            'colunas': colunas,
            'etapas_crm': ConversaWhatsApp.EtapaCRM.choices,
            'total_agendados': sum(
                item.etapa_crm == ConversaWhatsApp.EtapaCRM.AGENDADO
                for item in conversas
            ),
            'total_pendentes': sum(
                item.etapa_crm in {
                    ConversaWhatsApp.EtapaCRM.INTERESSADO,
                    ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO,
                    ConversaWhatsApp.EtapaCRM.AGUARDANDO_CLIENTE,
                }
                for item in conversas
            ),
        })


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
        conversa.ativa = True
        conversa.ultima_mensagem_em = timezone.now()
        conversa.save(update_fields=[
            'atendimento_humano', 'etapa', 'contexto', 'ativa',
            'ultima_mensagem_em', 'updated_at',
        ])
        messages.success(request, 'Atendimento automático retomado.')
        return redirect('whatsapp_agent:conversa-detail', pk=pk)


@method_decorator(require_POST, name='dispatch')
class EncerrarConversaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        conversa = get_object_or_404(
            ConversaWhatsApp.objects.for_filial(request.filial_ativa), pk=pk,
        )
        encerrar_conversa(conversa)
        messages.success(request, 'Conversa encerrada. Uma nova mensagem do cliente iniciará outro atendimento.')
        destino = request.POST.get('next')
        if destino == 'lista':
            return redirect('whatsapp_agent:conversa-list')
        return redirect('whatsapp_agent:conversa-detail', pk=pk)


@method_decorator(require_POST, name='dispatch')
class AlterarEtapaCRMView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        conversa = get_object_or_404(
            ConversaWhatsApp.objects.for_filial(request.filial_ativa), pk=pk,
        )
        etapa = request.POST.get('etapa_crm')
        etapas_validas = {item[0] for item in ConversaWhatsApp.EtapaCRM.choices}
        if etapa not in etapas_validas:
            messages.error(request, 'Etapa comercial inválida.')
        else:
            conversa.etapa_crm = etapa
            if etapa == ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO:
                conversa.atendimento_humano = True
                conversa.etapa = 'atendimento_humano'
            if etapa == ConversaWhatsApp.EtapaCRM.NAO_CONVERTIDO:
                conversa.motivo_nao_agendamento = (
                    request.POST.get('motivo_nao_agendamento') or ''
                ).strip()[:180]
            conversa.save(update_fields=[
                'etapa_crm', 'atendimento_humano', 'etapa',
                'motivo_nao_agendamento', 'updated_at',
            ])
            messages.success(request, 'Etapa comercial atualizada.')
        if request.POST.get('next') == 'detalhe':
            return redirect('whatsapp_agent:conversa-detail', pk=pk)
        return redirect('whatsapp_agent:conversa-list')


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
