import json
import logging

from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core.models import EmpresaBanco, Filial
from apps.core.views._admin import superuser_required

from .forms import ConfiguracaoWhatsAppCentralForm
from .gateway import EvolutionClient, GatewayWhatsAppError, identidade_instancia, qr_data_url
from .models import ConfiguracaoWhatsApp, ConfiguracaoWhatsAppCentral, EnvioResumoWhatsApp
from .resumo_service import preparar_resumos_diarios
from .tasks import processar_proximo_resumo_task


logger = logging.getLogger(__name__)


def _webhook_url(request, configuracao):
    return request.build_absolute_uri(
        reverse(
            'whatsapp_agent:webhook-central',
            kwargs={'secret': configuracao.webhook_secret},
        ),
    )


@superuser_required
def central_instancias(request):
    configuracao = ConfiguracaoWhatsAppCentral.carregar()
    if request.method == 'POST':
        form = ConfiguracaoWhatsAppCentralForm(request.POST, instance=configuracao)
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuração do WhatsApp central salva.')
            return redirect('core:admin_whatsapp_central')
    else:
        form = ConfiguracaoWhatsAppCentralForm(instance=configuracao)

    busca = request.GET.get('q', '').strip()
    instancias = list(
        ConfiguracaoWhatsApp.objects.using('default')
        .select_related('filial__empresa')
        .filter(filial__ativo=True, filial__empresa__ativo=True)
        .order_by('filial__empresa__razao_social', 'filial__razao_social')
    )
    configuracoes_por_filial = {item.filial_id: item for item in instancias}
    filiais = (
        Filial.objects.using('default')
        .select_related('empresa')
        .filter(ativo=True, empresa__ativo=True)
        .order_by('empresa__razao_social', 'razao_social')
    )
    total_filiais = filiais.count()
    if busca:
        filiais_configuracao = [
            item.filial_id for item in instancias
            if busca.lower() in item.instancia.lower()
            or busca in (item.numero_conectado or '')
        ]
        filiais = filiais.filter(
            Q(pk__in=filiais_configuracao)
            | Q(razao_social__icontains=busca)
            | Q(nome_fantasia__icontains=busca)
            | Q(empresa__razao_social__icontains=busca)
            | Q(empresa__nome_fantasia__icontains=busca)
        )
    bancos = {
        banco.empresa_id: banco
        for banco in EmpresaBanco.objects.using('default').select_related('empresa')
    }
    linhas = [{
        'filial': filial,
        'configuracao': configuracoes_por_filial.get(filial.pk),
        'banco': bancos.get(filial.empresa_id),
    } for filial in filiais]
    hoje = timezone.localdate()
    contagens = {
        item['status']: item['total']
        for item in (
            EnvioResumoWhatsApp.objects.using('default')
            .filter(created_at__date=hoje)
            .values('status')
            .annotate(total=Count('id'))
        )
    }
    minutos_janela = (
        configuracao.horario_fim.hour * 60 + configuracao.horario_fim.minute
        - configuracao.horario_inicio.hour * 60 - configuracao.horario_inicio.minute
    )
    capacidade_janela = max(
        0,
        minutos_janela // configuracao.intervalo_entre_envios_minutos + 1,
    )
    envios_recentes = (
        EnvioResumoWhatsApp.objects.using('default')
        .select_related('configuracao')
        .order_by('-created_at')[:40]
    )
    return render(request, 'whatsapp_agent/central_instancias.html', {
        'form': form,
        'configuracao_central': configuracao,
        'linhas': linhas,
        'total_configuradas': len(configuracoes_por_filial),
        'total_filiais': total_filiais,
        'busca': busca,
        'contagens': contagens,
        'capacidade_janela': capacidade_janela,
        'envios_recentes': envios_recentes,
    })


@require_POST
@superuser_required
def central_conectar(request):
    configuracao = ConfiguracaoWhatsAppCentral.carregar()
    try:
        cliente = EvolutionClient(configuracao)
        webhook_url = _webhook_url(request, configuracao)
        try:
            cliente.estado()
        except GatewayWhatsAppError:
            cliente.criar_instancia(webhook_url)
        cliente.configurar_webhook(webhook_url)
        configuracao.status = configuracao.Status.AGUARDANDO_QR
        configuracao.ultimo_erro = ''
        configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
    except GatewayWhatsAppError as exc:
        configuracao.status = configuracao.Status.ERRO
        configuracao.ultimo_erro = str(exc)
        configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        messages.error(request, str(exc))
        return redirect('core:admin_whatsapp_central')
    return redirect('core:admin_whatsapp_central_conexao')


@require_GET
@superuser_required
def central_conexao(request):
    configuracao = ConfiguracaoWhatsAppCentral.carregar()
    return render(request, 'whatsapp_agent/conexao.html', {
        'configuracao': configuracao,
        'titulo_conexao': 'Conectar WhatsApp central do iTED',
        'descricao_conexao': (
            'Este número será usado somente para os resumos gerenciais das filiais.'
        ),
        'status_url': reverse('core:admin_whatsapp_central_status'),
        'voltar_url': reverse('core:admin_whatsapp_central'),
    })


@require_GET
@superuser_required
def central_status(request):
    configuracao = ConfiguracaoWhatsAppCentral.carregar()
    try:
        cliente = EvolutionClient(configuracao)
        estado = cliente.estado()
        if estado == 'open':
            configuracao.status = configuracao.Status.CONECTADO
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
            return JsonResponse({
                'conectado': True,
                'status': configuracao.get_status_display(),
            })
        dados_qr = cliente.conectar()
        configuracao.status = configuracao.Status.AGUARDANDO_QR
        configuracao.save(update_fields=['status', 'updated_at'])
        return JsonResponse({
            'conectado': False,
            'status': configuracao.get_status_display(),
            'qr_code': qr_data_url(dados_qr),
            'pairing_code': dados_qr.get('pairingCode', ''),
        })
    except GatewayWhatsAppError as exc:
        configuracao.status = configuracao.Status.ERRO
        configuracao.ultimo_erro = str(exc)
        configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        return JsonResponse({'conectado': False, 'erro': str(exc)}, status=502)


@require_POST
@superuser_required
def atualizar_status_instancia(request, pk):
    configuracao = get_object_or_404(
        ConfiguracaoWhatsApp.objects.using('default').select_related('filial'),
        pk=pk,
    )
    try:
        cliente = EvolutionClient(configuracao)
        estado = cliente.estado()
        configuracao.status = (
            configuracao.Status.CONECTADO
            if estado == 'open'
            else configuracao.Status.DESCONECTADO
        )
        if estado == 'open':
            configuracao.ultima_conexao_em = timezone.now()
            try:
                numero, nome = identidade_instancia(cliente.detalhes())
                configuracao.numero_conectado = numero or configuracao.numero_conectado
                configuracao.nome_conectado = nome or configuracao.nome_conectado
            except GatewayWhatsAppError:
                pass
        configuracao.ultimo_erro = ''
        configuracao.save(using='default', update_fields=[
            'status', 'ultima_conexao_em', 'ultimo_erro',
            'numero_conectado', 'nome_conectado', 'updated_at',
        ])
        messages.success(request, f'Status de {configuracao.filial} atualizado.')
    except GatewayWhatsAppError as exc:
        configuracao.status = configuracao.Status.ERRO
        configuracao.ultimo_erro = str(exc)
        configuracao.save(using='default', update_fields=['status', 'ultimo_erro', 'updated_at'])
        messages.error(request, str(exc))
    return redirect('core:admin_whatsapp_central')


@require_POST
@superuser_required
def preparar_fila(request):
    criados = preparar_resumos_diarios(disparo_manual=True)
    if criados:
        try:
            processar_proximo_resumo_task.delay()
        except Exception:
            # O beat fará uma nova tentativa em até um minuto; o clique não pode
            # perder a fila caso o broker esteja se recuperando.
            logger.exception('Não foi possível antecipar o processamento do resumo manual.')
        messages.success(
            request,
            f'{criados} resumo(s) liberado(s) para envio manual e sequencial.',
        )
    else:
        messages.info(
            request,
            'Nenhum novo resumo foi liberado. Verifique os destinatários ou os envios de hoje.',
        )
    return redirect('core:admin_whatsapp_central')


@require_POST
@superuser_required
def reenviar_resumo(request, pk):
    envio = get_object_or_404(EnvioResumoWhatsApp.objects.using('default'), pk=pk)
    envio.status = envio.Status.PENDENTE
    envio.tentativas = 0
    envio.agendado_para = timezone.now()
    envio.disparo_manual = True
    envio.ultimo_erro = ''
    envio.save(using='default', update_fields=[
        'status', 'tentativas', 'agendado_para', 'disparo_manual', 'ultimo_erro', 'updated_at',
    ])
    messages.success(request, 'Mensagem devolvida ao fim da fila de envio.')
    return redirect('core:admin_whatsapp_central')


@csrf_exempt
@require_POST
def webhook_central(request, secret):
    try:
        tamanho = int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        tamanho = 0
    if tamanho > 1_000_000:
        return JsonResponse({'erro': 'Payload muito grande.'}, status=413)
    configuracao = ConfiguracaoWhatsAppCentral.objects.using('default').filter(
        webhook_secret=secret,
    ).first()
    if not configuracao:
        return JsonResponse({'erro': 'Webhook inválido.'}, status=404)
    try:
        payload = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)
    instancia = payload.get('instance') or ''
    if isinstance(instancia, dict):
        instancia = instancia.get('instanceName') or instancia.get('name') or ''
    if instancia and str(instancia) != configuracao.instancia:
        return JsonResponse({'ok': True, 'resultado': 'ignorado'})
    configuracao.ultimo_evento_em = timezone.now()
    evento = str(payload.get('event') or '').upper().replace('.', '_').replace('-', '_')
    if evento == 'CONNECTION_UPDATE':
        estado = str((payload.get('data') or {}).get('state') or '').lower()
        configuracao.status = (
            configuracao.Status.CONECTADO
            if estado == 'open'
            else configuracao.Status.DESCONECTADO
        )
        if estado == 'open':
            configuracao.ultima_conexao_em = timezone.now()
        configuracao.ultimo_erro = ''
        configuracao.save(using='default', update_fields=[
            'status', 'ultima_conexao_em', 'ultimo_evento_em', 'ultimo_erro', 'updated_at',
        ])
        return JsonResponse({'ok': True, 'resultado': 'conexao_atualizada'})
    configuracao.save(using='default', update_fields=['ultimo_evento_em', 'updated_at'])
    return JsonResponse({'ok': True, 'resultado': 'evento_ignorado'})
