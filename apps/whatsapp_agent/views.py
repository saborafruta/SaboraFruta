import hashlib
import hmac
import json
import re

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.tenant_context import get_current_database_alias
from apps.crm.services import CarteiraService

from .conversation_service import encerrar_conversa, encerrar_conversas_inativas
from .flow import garantir_fluxo_padrao
from .forms import (
    ConexaoWhatsAppVendedorForm,
    ConfiguracaoWhatsAppForm,
    FluxoWhatsAppForm,
)
from .gateway import (
    EvolutionClient, GatewayWhatsAppError, MetaCloudClient,
    identidade_instancia, identificador_envio, qr_data_url,
)
from .models import (
    ConexaoWhatsAppVendedor, ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp,
    MenuWhatsApp, OpcaoMenuWhatsApp, OrcamentoWhatsApp,
    UsoIAWhatsApp,
)
from .orcamento_service import (
    buscar_produtos, dados_editor, pedido_do_orcamento, resumo_para_whatsapp,
    salvar_orcamento, serializar_orcamento,
)
from .webhook import receber_evento, receber_evento_meta


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


def _gateway_configurado(configuracao):
    if configuracao.provedor == ConfiguracaoWhatsApp.Provedor.META_CLOUD:
        return configuracao.meta_credenciais_configuradas
    return bool(
        getattr(settings, 'WHATSAPP_EVOLUTION_URL', '')
        and getattr(settings, 'WHATSAPP_EVOLUTION_API_KEY', '')
    )


def _webhook_url(request, configuracao):
    return request.build_absolute_uri(
        reverse('whatsapp_agent:webhook', kwargs={'secret': configuracao.webhook_secret}),
    )


def _conversas_crm_permitidas(request):
    """Isola conversas por empresa e, para vendedor, pelo próprio número/carteira."""
    queryset = ConversaWhatsApp.objects.filter(
        conexao_vendedor__empresa__cnpj=request.filial_ativa.empresa.cnpj,
    )
    if CarteiraService.pode_supervisionar(request.user):
        return queryset
    return queryset.filter(
        conexao_vendedor__vendedor__email__iexact=request.user.email,
        fora_carteira=False,
    )


def _empresa_gerencial(request):
    return get_object_or_404(
        Empresa.objects.using('default'), cnpj=request.filial_ativa.empresa.cnpj,
    )


def _conexoes_crm_permitidas(request):
    queryset = ConexaoWhatsAppVendedor.objects.using('default').filter(
        empresa__cnpj=request.filial_ativa.empresa.cnpj,
    ).select_related('empresa', 'filial_padrao', 'vendedor')
    if CarteiraService.pode_supervisionar(request.user):
        return queryset
    return queryset.filter(vendedor__email__iexact=request.user.email)


def _vendedor_operacional_da_conexao(conexao):
    return Usuario.objects.using(get_current_database_alias()).filter(
        email__iexact=conexao.vendedor.email,
        empresa__cnpj=conexao.empresa.cnpj,
        ativo=True,
    ).first()


def _telefone_whatsapp_cliente(cliente):
    digitos = re.sub(r'\D', '', cliente.celular or cliente.telefone or '')
    if len(digitos) in (10, 11):
        digitos = f'55{digitos}'
    return digitos if 12 <= len(digitos) <= 15 else ''


def _clientes_da_carteira_da_conexao(request, conexao, busca=''):
    vendedor = _vendedor_operacional_da_conexao(conexao)
    if not vendedor:
        return [], None
    queryset = Cliente.objects.for_empresa(request.filial_ativa.empresa).filter(ativo=True)
    queryset = CarteiraService.filtrar_clientes_do_vendedor(
        queryset, vendedor=vendedor, empresa=request.filial_ativa.empresa,
    )
    busca = (busca or '').strip()
    if busca:
        queryset = queryset.filter(
            Q(razao_social__icontains=busca)
            | Q(nome_fantasia__icontains=busca)
            | Q(cpf_cnpj__icontains=busca)
            | Q(celular__icontains=busca)
            | Q(telefone__icontains=busca)
        )
    clientes, grupos = [], set()
    for cliente in queryset.select_related('filial').order_by('razao_social', 'pk')[:100]:
        if cliente.grupo_replicacao in grupos:
            continue
        grupos.add(cliente.grupo_replicacao)
        telefone = _telefone_whatsapp_cliente(cliente)
        clientes.append({
            'id': cliente.pk,
            'nome': cliente.nome_display,
            'documento': cliente.cpf_cnpj,
            'telefone': telefone,
            'telefone_exibicao': cliente.celular or cliente.telefone,
            'pode_iniciar': bool(telefone),
        })
        if len(clientes) >= 30:
            break
    return clientes, vendedor


def _serializar_mensagem_crm(mensagem):
    criada_em = timezone.localtime(mensagem.created_at)
    partes_rodape = []
    if mensagem.autor_nome:
        partes_rodape.append(mensagem.autor_nome)
    partes_rodape.extend([
        mensagem.get_origem_display(),
        criada_em.strftime('%d/%m %H:%M'),
    ])
    if mensagem.status:
        partes_rodape.append(mensagem.status)
    return {
        'id': mensagem.pk,
        'direcao': mensagem.direcao,
        'texto': mensagem.texto,
        'apagada': bool(mensagem.apagada_em),
        'rodape': ' · '.join(partes_rodape),
        'status': mensagem.status,
        'updated_at': mensagem.updated_at.isoformat(),
    }


def _serializar_conversa_crm(conversa, *, pode_supervisionar):
    ultima_em = conversa.ultima_mensagem_em or conversa.created_at
    return {
        'id': conversa.pk,
        'nome': conversa.nome_contato or conversa.telefone,
        'telefone': conversa.telefone,
        'inicial': (conversa.nome_contato or conversa.telefone or '?')[:1].upper(),
        'ultima_mensagem': conversa.ultima_mensagem_texto or 'Sem mensagens',
        'ultima_direcao': conversa.ultima_mensagem_direcao or '',
        'ultima_hora': timezone.localtime(ultima_em).strftime('%H:%M'),
        'ordem': ultima_em.timestamp(),
        'vendedor': (
            conversa.conexao_vendedor.vendedor.nome if pode_supervisionar else ''
        ),
        'url': reverse('whatsapp_agent:crm-inbox-detail', args=[conversa.pk]),
    }


def _request_quer_json(request):
    return (
        'application/json' in request.headers.get('Accept', '')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


class ConfiguracaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def get(self, request):
        configuracao = _configuracao(request)
        form = ConfiguracaoWhatsAppForm(instance=configuracao)
        return render(request, 'whatsapp_agent/configuracao.html', {
            'form': form, 'configuracao': configuracao,
            'gateway_configurado': _gateway_configurado(configuracao),
            'webhook_url': _webhook_url(request, configuracao),
        })

    def post(self, request):
        configuracao = _configuracao(request)
        provedor_anterior = configuracao.provedor
        phone_number_id_anterior = configuracao.meta_phone_number_id
        form = ConfiguracaoWhatsAppForm(request.POST, instance=configuracao)
        if form.is_valid():
            configuracao = form.save(commit=False)
            configuracao.filial = request.filial_ativa
            credencial_meta_alterada = bool(
                request.POST.get('meta_access_token')
                or configuracao.meta_phone_number_id != phone_number_id_anterior
            )
            if configuracao.provedor != provedor_anterior or credencial_meta_alterada:
                configuracao.status = ConfiguracaoWhatsApp.Status.NAO_CONFIGURADO
                configuracao.numero_conectado = ''
                configuracao.nome_conectado = ''
                configuracao.ultimo_erro = ''
            configuracao.save()
            messages.success(request, 'Configuração do WhatsApp salva.')
            return redirect('whatsapp_agent:configuracao')
        return render(request, 'whatsapp_agent/configuracao.html', {
            'form': form, 'configuracao': configuracao,
            'gateway_configurado': _gateway_configurado(configuracao),
            'webhook_url': _webhook_url(request, configuracao),
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


def _validar_fluxo(valor, modo_atendimento=ConfiguracaoWhatsApp.ModoAtendimento.AGENDA):
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
    acoes_ativas = {
        opcao['acao']
        for menu in menus if menu.get('ativo', True)
        for opcao in (menu.get('opcoes') or []) if opcao.get('ativo', True)
    }
    if modo_atendimento in {
        ConfiguracaoWhatsApp.ModoAtendimento.AGENDA,
        ConfiguracaoWhatsApp.ModoAtendimento.AMBOS,
    } and OpcaoMenuWhatsApp.Acao.AGENDA not in acoes_ativas:
        raise ValueError('Inclua ao menos uma opção ativa para enviar o link da agenda.')
    if modo_atendimento in {
        ConfiguracaoWhatsApp.ModoAtendimento.CATALOGO,
        ConfiguracaoWhatsApp.ModoAtendimento.AMBOS,
    } and OpcaoMenuWhatsApp.Acao.CATALOGO not in acoes_ativas:
        raise ValueError('Inclua ao menos uma opção ativa para enviar o link do catálogo.')
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

    def _contexto(self, configuracao, form, fluxo=None):
        inicio_mes = timezone.localdate().replace(day=1)
        uso = UsoIAWhatsApp.objects.filter(
            configuracao=configuracao,
            created_at__date__gte=inicio_mes,
        ).aggregate(
            chamadas=Count('id'),
            tokens_entrada=Sum('tokens_entrada'),
            tokens_saida=Sum('tokens_saida'),
            tokens_total=Sum('tokens_total'),
            segundos_audio=Sum('segundos_audio'),
            custo_brl=Sum('custo_brl'),
        )
        return {
            'form': form,
            'fluxo': fluxo if fluxo is not None else _fluxo_serializado(configuracao),
            'acoes': list(OpcaoMenuWhatsApp.Acao.choices),
            'ia_api_key_configurada': configuracao.ia_api_key_configurada,
            'uso_ia': uso,
            'inicio_mes': inicio_mes,
        }

    def get(self, request):
        configuracao = _configuracao(request)
        return render(
            request,
            'whatsapp_agent/fluxo_configuracao.html',
            self._contexto(configuracao, FluxoWhatsAppForm(instance=configuracao)),
        )

    def post(self, request):
        configuracao = _configuracao(request)
        form = FluxoWhatsAppForm(request.POST, instance=configuracao)
        formulario_valido = form.is_valid()
        try:
            fluxo = _validar_fluxo(
                request.POST.get('fluxo_json'),
                form.cleaned_data.get('modo_atendimento', configuracao.modo_atendimento)
                if formulario_valido else configuracao.modo_atendimento,
            )
        except ValueError as exc:
            fluxo = _fluxo_serializado(configuracao)
            form.add_error(None, str(exc))
            formulario_valido = False
        if formulario_valido:
            with transaction.atomic():
                form.save()
                _salvar_fluxo(configuracao, fluxo)
            messages.success(request, 'Fluxo de atendimento salvo e já disponível no WhatsApp.')
            return redirect('whatsapp_agent:fluxo-configuracao')
        return render(
            request,
            'whatsapp_agent/fluxo_configuracao.html',
            self._contexto(configuracao, form, fluxo),
        )


@method_decorator(require_POST, name='dispatch')
class ConectarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request):
        configuracao = get_object_or_404(
            ConfiguracaoWhatsApp.objects.for_filial(request.filial_ativa), ativo=True,
        )
        try:
            if configuracao.provedor == ConfiguracaoWhatsApp.Provedor.META_CLOUD:
                dados = MetaCloudClient(configuracao).detalhes()
                configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
                configuracao.numero_conectado = re.sub(
                    r'\D', '', str(dados.get('display_phone_number') or ''),
                )[:24]
                configuracao.nome_conectado = str(dados.get('verified_name') or '')[:120]
                configuracao.ultima_conexao_em = timezone.now()
                configuracao.ultimo_erro = ''
                configuracao.save(update_fields=[
                    'status', 'numero_conectado', 'nome_conectado',
                    'ultima_conexao_em', 'ultimo_erro', 'updated_at',
                ])
                messages.success(
                    request,
                    'API oficial validada. Cadastre a URL e o token de webhook exibidos nesta tela na Meta.',
                )
                return redirect('whatsapp_agent:configuracao')
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
            if configuracao.provedor == ConfiguracaoWhatsApp.Provedor.META_CLOUD:
                dados = MetaCloudClient(configuracao).detalhes()
                configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
                configuracao.ultima_conexao_em = timezone.now()
                configuracao.ultimo_erro = ''
                configuracao.numero_conectado = re.sub(
                    r'\D', '', str(dados.get('display_phone_number') or ''),
                )[:24]
                configuracao.nome_conectado = str(dados.get('verified_name') or '')[:120]
                configuracao.save(update_fields=[
                    'status', 'ultima_conexao_em', 'ultimo_erro',
                    'numero_conectado', 'nome_conectado', 'updated_at',
                ])
                return JsonResponse({
                    'conectado': True,
                    'status': configuracao.get_status_display(),
                    'oficial': True,
                })
            cliente = EvolutionClient(configuracao)
            estado = cliente.estado()
            if estado == 'open':
                configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
                configuracao.ultima_conexao_em = timezone.now()
                configuracao.ultimo_erro = ''
                try:
                    numero, nome = identidade_instancia(cliente.detalhes())
                    configuracao.numero_conectado = numero or configuracao.numero_conectado
                    configuracao.nome_conectado = nome or configuracao.nome_conectado
                except GatewayWhatsAppError:
                    pass
                configuracao.save(update_fields=[
                    'status', 'ultima_conexao_em', 'ultimo_erro',
                    'numero_conectado', 'nome_conectado', 'updated_at',
                ])
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


class CRMWhatsAppConexoesView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request):
        return render(request, 'whatsapp_agent/crm_conexoes.html', {
            'conexoes': _conexoes_crm_permitidas(request),
            'pode_supervisionar': CarteiraService.pode_supervisionar(request.user),
        })


class CRMWhatsAppConexaoFormView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'aprovar'

    def _instance(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(_conexoes_crm_permitidas(request), pk=pk)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not CarteiraService.pode_supervisionar(request.user):
            messages.error(request, 'Somente gestores podem cadastrar números comerciais.')
            return redirect('whatsapp_agent:crm-conexoes')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk=None):
        empresa = _empresa_gerencial(request)
        instancia = self._instance(request, pk)
        return render(request, 'whatsapp_agent/crm_conexao_form.html', {
            'form': ConexaoWhatsAppVendedorForm(
                empresa=empresa, instance=instancia,
            ),
            'conexao': instancia,
        })

    def post(self, request, pk=None):
        empresa = _empresa_gerencial(request)
        instancia = self._instance(request, pk)
        form = ConexaoWhatsAppVendedorForm(
            request.POST, empresa=empresa, instance=instancia,
        )
        if form.is_valid():
            conexao = form.save()
            messages.success(request, 'Número comercial salvo. Continue pela conexão oficial.')
            return redirect('whatsapp_agent:crm-conexao-onboarding', pk=conexao.pk)
        return render(request, 'whatsapp_agent/crm_conexao_form.html', {
            'form': form, 'conexao': instancia,
        })


class CRMWhatsAppConexaoOnboardingView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conexao = get_object_or_404(_conexoes_crm_permitidas(request), pk=pk)
        webhook_url = request.build_absolute_uri(
            reverse('whatsapp_agent:webhook', kwargs={'secret': conexao.webhook_secret}),
        )
        erro_conexao = ''
        try:
            cliente = EvolutionClient(conexao)
            try:
                cliente.estado()
            except GatewayWhatsAppError:
                cliente.criar_instancia(webhook_url)
            cliente.configurar_webhook(webhook_url)
            if conexao.status == ConexaoWhatsAppVendedor.Status.NAO_CONFIGURADO:
                conexao.status = ConexaoWhatsAppVendedor.Status.PENDENTE
                conexao.ultimo_erro = ''
                conexao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        except GatewayWhatsAppError as exc:
            erro_conexao = str(exc)
            conexao.status = ConexaoWhatsAppVendedor.Status.ERRO
            conexao.ultimo_erro = erro_conexao
            conexao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        return render(request, 'whatsapp_agent/crm_conexao_onboarding.html', {
            'conexao': conexao,
            'pode_supervisionar': CarteiraService.pode_supervisionar(request.user),
            'status_url': reverse(
                'whatsapp_agent:crm-conexao-status', kwargs={'pk': conexao.pk},
            ),
            'erro_conexao': erro_conexao,
        })


class CRMWhatsAppConexaoStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conexao = get_object_or_404(_conexoes_crm_permitidas(request), pk=pk)
        try:
            cliente = EvolutionClient(conexao)
            estado = cliente.estado()
            if estado == 'open':
                conexao.status = ConexaoWhatsAppVendedor.Status.CONECTADO
                conexao.ultimo_erro = ''
                try:
                    numero, nome = identidade_instancia(cliente.detalhes())
                    conexao.numero_conectado = numero or conexao.numero_conectado
                    conexao.nome_conectado = nome or conexao.nome_conectado
                except GatewayWhatsAppError:
                    pass
                conexao.save(update_fields=[
                    'status', 'numero_conectado', 'nome_conectado',
                    'ultimo_erro', 'updated_at',
                ])
                return JsonResponse({
                    'conectado': True,
                    'status': conexao.get_status_display(),
                })
            dados_qr = cliente.conectar()
            conexao.status = ConexaoWhatsAppVendedor.Status.PENDENTE
            conexao.ultimo_erro = ''
            conexao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
            return JsonResponse({
                'conectado': False,
                'status': 'Aguardando leitura do QR Code',
                'qr_code': qr_data_url(dados_qr),
                'pairing_code': dados_qr.get('pairingCode', ''),
            })
        except GatewayWhatsAppError as exc:
            conexao.status = ConexaoWhatsAppVendedor.Status.ERRO
            conexao.ultimo_erro = str(exc)
            conexao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
            return JsonResponse({'conectado': False, 'erro': str(exc)}, status=502)


class CRMWhatsAppInboxView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request, pk=None):
        ultima_mensagem = MensagemWhatsApp.objects.filter(
            conversa_id=OuterRef('pk'),
        ).order_by('-created_at')
        conversas = list(
            _conversas_crm_permitidas(request)
            .select_related('conexao_vendedor', 'conexao_vendedor__vendedor')
            .annotate(
                ultima_mensagem_texto=Subquery(ultima_mensagem.values('texto')[:1]),
                ultima_mensagem_direcao=Subquery(ultima_mensagem.values('direcao')[:1]),
            )
            .order_by('-ultima_mensagem_em', '-created_at')[:200]
        )
        selecionada = None
        if pk is not None:
            selecionada = get_object_or_404(
                _conversas_crm_permitidas(request)
                .select_related('conexao_vendedor', 'conexao_vendedor__vendedor')
                .prefetch_related('mensagens'),
                pk=pk,
            )
        elif conversas:
            selecionada = conversas[0]
            selecionada = (
                _conversas_crm_permitidas(request)
                .select_related('conexao_vendedor', 'conexao_vendedor__vendedor')
                .prefetch_related('mensagens')
                .get(pk=selecionada.pk)
            )
        return render(request, 'whatsapp_agent/crm_inbox.html', {
            'conversas': conversas,
            'conversa_selecionada': selecionada,
            'pode_supervisionar': CarteiraService.pode_supervisionar(request.user),
        })


class CRMWhatsAppAtualizacoesView(PermissaoRequiredMixin, View):
    """Sincronização incremental da caixa, sem recarregar a página."""

    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conversa = get_object_or_404(_conversas_crm_permitidas(request), pk=pk)
        limite = timezone.now()
        desde = parse_datetime((request.GET.get('desde') or '').strip())
        if desde and timezone.is_naive(desde):
            desde = timezone.make_aware(desde)
        pode_supervisionar = CarteiraService.pode_supervisionar(request.user)

        mensagens = conversa.mensagens.filter(updated_at__lte=limite)
        conversas = _conversas_crm_permitidas(request).filter(updated_at__lte=limite)
        if desde:
            mensagens = mensagens.filter(updated_at__gt=desde)
            conversas = conversas.filter(updated_at__gt=desde)

        mensagens = list(mensagens.order_by('-updated_at')[:500])
        ultima_mensagem = MensagemWhatsApp.objects.filter(
            conversa_id=OuterRef('pk'),
        ).order_by('-created_at')
        conversas = list(
            conversas.select_related(
                'conexao_vendedor', 'conexao_vendedor__vendedor',
            ).annotate(
                ultima_mensagem_texto=Subquery(ultima_mensagem.values('texto')[:1]),
                ultima_mensagem_direcao=Subquery(ultima_mensagem.values('direcao')[:1]),
            ).order_by('-ultima_mensagem_em', '-created_at')[:200]
        )
        return JsonResponse({
            'sincronizado_em': limite.isoformat(),
            'conversa_id': conversa.pk,
            'mensagens': [
                _serializar_mensagem_crm(item)
                for item in sorted(mensagens, key=lambda item: (item.created_at, item.pk))
            ],
            'conversas': [
                _serializar_conversa_crm(
                    item, pode_supervisionar=pode_supervisionar,
                )
                for item in conversas
            ],
            'total_conversas': _conversas_crm_permitidas(request).count(),
        })


class CRMNovaConversaDadosView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request):
        conexoes = list(_conexoes_crm_permitidas(request).filter(ativo=True))
        conexao_id = request.GET.get('conexao')
        conexao = next(
            (item for item in conexoes if str(item.pk) == str(conexao_id)),
            next(
                (
                    item for item in conexoes
                    if item.status == ConexaoWhatsAppVendedor.Status.CONECTADO
                ),
                conexoes[0] if conexoes else None,
            ),
        )
        clientes = []
        if conexao and (request.GET.get('q') or '').strip():
            clientes, _vendedor = _clientes_da_carteira_da_conexao(
                request, conexao, request.GET.get('q'),
            )
        return JsonResponse({
            'conexoes': [
                {
                    'id': item.pk,
                    'vendedor': item.vendedor.nome,
                    'numero': item.numero_conectado,
                    'status': item.status,
                    'conectada': item.status == ConexaoWhatsAppVendedor.Status.CONECTADO,
                }
                for item in conexoes
            ],
            'conexao_selecionada': conexao.pk if conexao else None,
            'clientes': clientes,
            'pode_supervisionar': CarteiraService.pode_supervisionar(request.user),
        })


@method_decorator(require_POST, name='dispatch')
class CRMNovaConversaIniciarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'criar'

    def post(self, request):
        try:
            payload = json.loads(request.body or b'{}')
        except json.JSONDecodeError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        conexao = get_object_or_404(
            _conexoes_crm_permitidas(request).filter(ativo=True),
            pk=payload.get('conexao_id'),
        )
        if conexao.status != ConexaoWhatsAppVendedor.Status.CONECTADO:
            return JsonResponse({'erro': 'Conecte o número do vendedor antes de iniciar.'}, status=400)
        vendedor = _vendedor_operacional_da_conexao(conexao)
        if not vendedor:
            return JsonResponse({'erro': 'O vendedor não foi localizado no banco da empresa.'}, status=400)
        cliente = Cliente.objects.for_empresa(request.filial_ativa.empresa).filter(
            pk=payload.get('cliente_id'), ativo=True,
        ).first()
        if not cliente or not CarteiraService.usuario_pode_acessar_cliente(
            usuario=vendedor, cliente=cliente,
        ):
            return JsonResponse({'erro': 'Este cliente não pertence à carteira do vendedor.'}, status=403)
        telefone = _telefone_whatsapp_cliente(cliente)
        if not telefone:
            return JsonResponse({
                'erro': 'Cadastre um celular ou telefone válido no cliente antes de iniciar.',
            }, status=400)

        filial_central = conexao.filial_padrao or Filial.objects.using('default').filter(
            empresa_id=conexao.empresa_id, ativo=True,
        ).order_by('-is_matriz', 'pk').first()
        if not filial_central:
            return JsonResponse({'erro': 'A filial central da conexão não foi localizada.'}, status=400)
        remote_jid = f'{telefone}@s.whatsapp.net'
        conversa = ConversaWhatsApp.objects.using('default').filter(
            conexao_vendedor_id=conexao.pk, remote_jid=remote_jid, ativa=True,
        ).first()
        if conversa and (
            conversa.cliente_grupo_replicacao
            and conversa.cliente_grupo_replicacao != cliente.grupo_replicacao
        ):
            return JsonResponse({
                'erro': 'Este telefone já está ligado a outro cliente nesta conexão.',
            }, status=409)
        if not conversa:
            conversa = ConversaWhatsApp.objects.using('default').create(
                filial_id=filial_central.pk,
                conexao_vendedor_id=conexao.pk,
                remote_jid=remote_jid,
                telefone=telefone,
                nome_contato=cliente.nome_display,
                tenant_alias=(
                    getattr(request, 'tenant_db_alias', None)
                    or get_current_database_alias()
                ),
                cliente_operacional_id=cliente.pk,
                cliente_grupo_replicacao=cliente.grupo_replicacao,
                fora_carteira=False,
                atendimento_humano=True,
                etapa_crm=ConversaWhatsApp.EtapaCRM.NOVA,
            )
        else:
            conversa.nome_contato = cliente.nome_display
            conversa.cliente_operacional_id = cliente.pk
            conversa.cliente_grupo_replicacao = cliente.grupo_replicacao
            conversa.fora_carteira = False
            conversa.atendimento_humano = True
            conversa.save(using='default', update_fields=[
                'nome_contato', 'cliente_operacional_id',
                'cliente_grupo_replicacao', 'fora_carteira',
                'atendimento_humano', 'updated_at',
            ])
        return JsonResponse({
            'ok': True,
            'url': reverse('whatsapp_agent:crm-inbox-detail', args=[conversa.pk]),
        })


def _enviar_texto_humano_crm(conversa, texto, usuario):
    conexao = conversa.conexao_vendedor
    mensagem = MensagemWhatsApp.objects.create(
        conversa=conversa,
        direcao=MensagemWhatsApp.Direcao.SAIDA,
        origem=MensagemWhatsApp.Origem.CRM,
        autor_nome=usuario.nome,
        autor_email=usuario.email,
        texto=texto,
        texto_original=texto,
        tipo='texto',
        status='pendente',
    )
    try:
        retorno = EvolutionClient(conexao).enviar_texto(conversa.telefone, texto)
        mensagem.identificador_externo = identificador_envio(retorno)
        mensagem.status = 'enviada'
        conversa.ultima_mensagem_em = timezone.now()
        conversa.save(update_fields=['ultima_mensagem_em', 'updated_at'])
    except GatewayWhatsAppError as exc:
        mensagem.status = 'erro'
        mensagem.dados_evento = {'erro': str(exc)}
        mensagem.save(update_fields=['status', 'dados_evento', 'updated_at'])
        raise
    mensagem.save(update_fields=[
        'identificador_externo', 'status', 'dados_evento', 'updated_at',
    ])
    return mensagem


def _partes_whatsapp(texto, limite=3900):
    partes, atual = [], []
    tamanho = 0
    for linha in texto.splitlines():
        acrescimo = len(linha) + (1 if atual else 0)
        if atual and tamanho + acrescimo > limite:
            partes.append('\n'.join(atual))
            atual, tamanho = [], 0
        atual.append(linha)
        tamanho += len(linha) + (1 if tamanho else 0)
    if atual:
        partes.append('\n'.join(atual))
    return partes


class CRMOrcamentoEditorView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conversa = get_object_or_404(_conversas_crm_permitidas(request), pk=pk)
        try:
            return JsonResponse(dados_editor(conversa, request.filial_ativa))
        except DadosInvalidosError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)


class CRMOrcamentoProdutosView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request, pk):
        conversa = get_object_or_404(_conversas_crm_permitidas(request), pk=pk)
        try:
            produtos = buscar_produtos(
                conversa, request.filial_ativa, request.GET.get('q', ''),
            )
            return JsonResponse({'produtos': produtos})
        except DadosInvalidosError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)


@method_decorator(require_POST, name='dispatch')
class CRMOrcamentoSalvarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'criar'

    def post(self, request, pk):
        conversa = get_object_or_404(_conversas_crm_permitidas(request), pk=pk)
        try:
            payload = json.loads(request.body or b'{}')
            orcamento = salvar_orcamento(
                conversa=conversa, filial=request.filial_ativa,
                usuario=request.user, payload=payload,
            )
            return JsonResponse({'ok': True, 'orcamento': serializar_orcamento(orcamento)})
        except (json.JSONDecodeError, DadosInvalidosError) as exc:
            return JsonResponse({'erro': str(exc)}, status=400)


@method_decorator(require_POST, name='dispatch')
class CRMOrcamentoEnviarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'criar'

    def post(self, request, pk, orcamento_pk):
        conversa = get_object_or_404(
            _conversas_crm_permitidas(request).select_related('conexao_vendedor'),
            pk=pk,
        )
        orcamento = get_object_or_404(
            conversa.orcamentos.using('default'),
            pk=orcamento_pk, status=OrcamentoWhatsApp.Status.RASCUNHO,
        )
        if orcamento.tenant_alias != get_current_database_alias():
            return JsonResponse({'erro': 'O orçamento pertence a outro banco operacional.'}, status=400)
        if not conversa.conexao_vendedor:
            return JsonResponse({'erro': 'A conexão do vendedor não está configurada.'}, status=400)

        link = request.build_absolute_uri(
            reverse('orcamento_publico:detalhe', args=[orcamento.token_publico]),
        )
        TenantPublicLinkService.register(
            kind='orcamento', token=orcamento.token_publico,
            db_alias=get_current_database_alias(),
        )
        texto = resumo_para_whatsapp(orcamento, link)
        try:
            for parte in _partes_whatsapp(texto):
                _enviar_texto_humano_crm(conversa, parte, request.user)
        except GatewayWhatsAppError as exc:
            return JsonResponse({'erro': f'Não foi possível enviar o orçamento: {exc}'}, status=502)

        orcamento.status = OrcamentoWhatsApp.Status.ENVIADO
        orcamento.enviado_em = timezone.now()
        orcamento.save(update_fields=['status', 'enviado_em', 'updated_at'])
        pedido = pedido_do_orcamento(orcamento)
        pedido.status = pedido.Status.AGUARDANDO_APROVACAO
        pedido.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'ok': True, 'mensagem': 'Orçamento enviado para o cliente.'})


@method_decorator(require_POST, name='dispatch')
class CRMWhatsAppEnviarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'criar'

    def post(self, request, pk):
        conversa = get_object_or_404(
            _conversas_crm_permitidas(request).select_related(
                'conexao_vendedor', 'conexao_vendedor__vendedor',
            ),
            pk=pk,
        )
        texto = (request.POST.get('mensagem') or '').strip()
        if not texto:
            if _request_quer_json(request):
                return JsonResponse({'erro': 'Digite uma mensagem antes de enviar.'}, status=400)
            messages.error(request, 'Digite uma mensagem antes de enviar.')
            return redirect('whatsapp_agent:crm-inbox-detail', pk=pk)
        if len(texto) > 4096:
            if _request_quer_json(request):
                return JsonResponse({
                    'erro': 'A mensagem deve ter no máximo 4.096 caracteres.',
                }, status=400)
            messages.error(request, 'A mensagem deve ter no máximo 4.096 caracteres.')
            return redirect('whatsapp_agent:crm-inbox-detail', pk=pk)
        conexao = conversa.conexao_vendedor
        if not conexao:
            if _request_quer_json(request):
                return JsonResponse({
                    'erro': 'A conexão deste vendedor não está configurada.',
                }, status=400)
            messages.error(request, 'A conexão deste vendedor não está configurada.')
            return redirect('whatsapp_agent:crm-inbox-detail', pk=pk)
        try:
            mensagem = _enviar_texto_humano_crm(conversa, texto, request.user)
            if _request_quer_json(request):
                return JsonResponse({
                    'ok': True,
                    'mensagem': _serializar_mensagem_crm(mensagem),
                })
            messages.success(request, 'Mensagem enviada pelo CRM.')
        except GatewayWhatsAppError as exc:
            if _request_quer_json(request):
                mensagem = conversa.mensagens.filter(
                    origem=MensagemWhatsApp.Origem.CRM,
                    autor_email=request.user.email,
                    texto=texto,
                    status='erro',
                ).order_by('-created_at').first()
                resposta = {'erro': f'Não foi possível enviar a mensagem: {exc}'}
                if mensagem:
                    resposta['mensagem'] = _serializar_mensagem_crm(mensagem)
                return JsonResponse(resposta, status=502)
            messages.error(request, f'Não foi possível enviar a mensagem: {exc}')
        return redirect('whatsapp_agent:crm-inbox-detail', pk=pk)


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
        uso = conversa.usos_ia.aggregate(
            chamadas=Count('id'),
            tokens_entrada=Sum('tokens_entrada'),
            tokens_saida=Sum('tokens_saida'),
            tokens_total=Sum('tokens_total'),
            segundos_audio=Sum('segundos_audio'),
            custo_brl=Sum('custo_brl'),
        )
        return render(request, 'whatsapp_agent/conversa_detail.html', {
            'conversa': conversa,
            'uso_ia': uso,
        })


@method_decorator(require_POST, name='dispatch')
class RetomarAgenteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        conversa = get_object_or_404(
            ConversaWhatsApp.objects.for_filial(request.filial_ativa), pk=pk,
        )
        if not conversa.ativa:
            messages.info(
                request,
                'Esta sessão já foi encerrada. A próxima mensagem do cliente iniciará uma nova conversa.',
            )
            return redirect('whatsapp_agent:conversa-detail', pk=pk)
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
def webhook(request, secret):
    configuracao = ConfiguracaoWhatsApp.objects.filter(
        webhook_secret=secret, ativo=True,
    ).select_related('filial').first()
    if not configuracao:
        configuracao = ConexaoWhatsAppVendedor.objects.filter(
            webhook_secret=secret, ativo=True,
        ).select_related('empresa', 'filial_padrao', 'vendedor').first()
    if not configuracao:
        return JsonResponse({'erro': 'Webhook inválido.'}, status=404)
    usa_meta = (
        isinstance(configuracao, ConfiguracaoWhatsApp)
        and configuracao.provedor == ConfiguracaoWhatsApp.Provedor.META_CLOUD
    )
    if request.method == 'GET':
        if not usa_meta:
            return JsonResponse({'erro': 'Método não permitido.'}, status=405)
        token = request.GET.get('hub.verify_token', '')
        desafio = request.GET.get('hub.challenge', '')
        token_configurado = configuracao.obter_meta_verify_token()
        if (
            token_configurado
            and request.GET.get('hub.mode') == 'subscribe'
            and hmac.compare_digest(token, token_configurado)
        ):
            return HttpResponse(desafio, content_type='text/plain')
        return JsonResponse({'erro': 'Verificação recusada.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)
    try:
        tamanho = int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        tamanho = 0
    if tamanho > 1_000_000:
        return JsonResponse({'erro': 'Payload muito grande.'}, status=413)
    if usa_meta:
        segredo = configuracao.obter_meta_app_secret()
        assinatura = request.headers.get('X-Hub-Signature-256', '')
        if not segredo:
            return JsonResponse({'erro': 'Webhook oficial sem assinatura configurada.'}, status=403)
        esperada = 'sha256=' + hmac.new(
            segredo.encode('utf-8'), request.body, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(assinatura, esperada):
            return JsonResponse({'erro': 'Assinatura inválida.'}, status=403)
    try:
        payload = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)
    resultado = (
        receber_evento_meta(configuracao, payload)
        if usa_meta
        else receber_evento(configuracao, payload)
    )
    return JsonResponse({'ok': True, 'resultado': resultado})
