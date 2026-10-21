import datetime
import hashlib
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import EmpresaBanco, Filial
from apps.core.services.tenant_task_service import TenantTaskService
from apps.core.tenant_context import get_current_database_alias, tenant_db
from apps.core.tenant_registry import register_tenant_database
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import Boleto, ContaReceber

from .gateway import GatewayWhatsAppError, cliente_whatsapp, identificador_envio
from .models import (
    ConfiguracaoWhatsApp, ConsentimentoCobrancaWhatsApp, ConversaWhatsApp,
    EnvioCobrancaWhatsApp, MensagemWhatsApp,
)
from .notifications import configuracao_whatsapp_conectada, numero_whatsapp


logger = logging.getLogger(__name__)
STATUS_ABERTOS = {
    StatusContaReceber.ABERTO,
    StatusContaReceber.PAGO_PARCIAL,
    StatusContaReceber.VENCIDO,
    StatusContaReceber.NEGOCIADO,
}


def _moeda(valor):
    valor = Decimal(str(valor or 0)).quantize(Decimal('0.01'))
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _evento(dias, manual=False):
    if manual:
        return f'manual_{timezone.localdate():%Y%m%d}'
    if dias == 0:
        return 'vencimento'
    return f"{'antes' if dias < 0 else 'depois'}_{abs(dias)}"


class _Valores(dict):
    def __missing__(self, chave):
        return '{' + chave + '}'


def _dados_titulo(titulo, configuracao):
    boleto = (
        Boleto.objects.filter(conta_receber=titulo)
        .order_by('-created_at')
        .first()
    )
    telefone = numero_whatsapp(titulo.cliente.celular or titulo.cliente.telefone)
    link = titulo.boleto_url or (boleto.url_boleto if boleto else '')
    empresa = configuracao.filial.empresa.nome_fantasia or configuracao.filial.empresa.razao_social
    valores = {
        'nome': titulo.cliente.razao_social or titulo.cliente.nome_fantasia or 'cliente',
        'empresa': empresa,
        'documento': titulo.documento_numero or str(titulo.pk),
        'valor': _moeda(titulo.valor_saldo),
        'vencimento': titulo.data_vencimento.strftime('%d/%m/%Y'),
        'link': link or 'Entre em contato conosco para receber a segunda via.',
    }
    try:
        mensagem = configuracao.cobranca_mensagem.format_map(_Valores(valores)).strip()
    except (KeyError, ValueError):
        padrao = ConfiguracaoWhatsApp._meta.get_field('cobranca_mensagem').default
        mensagem = padrao.format_map(_Valores(valores)).strip()
    return boleto, telefone, valores, mensagem


def _consentimento_valido(configuracao, alias, titulo, telefone):
    if (
        configuracao.provedor != ConfiguracaoWhatsApp.Provedor.META_CLOUD
        and not configuracao.cobranca_exigir_consentimento
    ):
        return True
    return ConsentimentoCobrancaWhatsApp.objects.using('default').filter(
        tenant_alias=alias,
        filial_cnpj=titulo.filial.cnpj,
        cliente_id=titulo.cliente_id,
        telefone=telefone,
        autorizado=True,
    ).exists()


def registrar_consentimento(titulo, *, alias, usuario=None, autorizado=True):
    telefone = numero_whatsapp(titulo.cliente.celular or titulo.cliente.telefone)
    if not telefone:
        return None
    agora = timezone.now()
    consentimento, _ = ConsentimentoCobrancaWhatsApp.objects.using('default').update_or_create(
        tenant_alias=alias,
        filial_cnpj=titulo.filial.cnpj,
        cliente_id=titulo.cliente_id,
        defaults={
            'telefone': telefone,
            'autorizado': autorizado,
            'autorizado_em': agora if autorizado else None,
            'revogado_em': None if autorizado else agora,
            'origem': 'financeiro_manual',
            'registrado_por_id': (
                getattr(usuario, 'pk', None)
                if getattr(getattr(usuario, '_state', None), 'db', None) == 'default'
                else None
            ),
        },
    )
    return consentimento


def _ajustar_para_janela(configuracao, instante):
    local = timezone.localtime(instante)
    inicio = timezone.make_aware(
        datetime.datetime.combine(local.date(), configuracao.cobranca_horario_inicio),
        timezone.get_current_timezone(),
    )
    fim = timezone.make_aware(
        datetime.datetime.combine(local.date(), configuracao.cobranca_horario_fim),
        timezone.get_current_timezone(),
    )
    if instante < inicio:
        return inicio
    if instante <= fim:
        return instante
    return timezone.make_aware(
        datetime.datetime.combine(
            local.date() + datetime.timedelta(days=1),
            configuracao.cobranca_horario_inicio,
        ),
        timezone.get_current_timezone(),
    )


def _proximo_horario(configuracao):
    intervalo = datetime.timedelta(minutes=configuracao.cobranca_intervalo_minutos)
    ultimo = (
        EnvioCobrancaWhatsApp.objects.using('default')
        .filter(
            configuracao=configuracao,
            status__in=[
                EnvioCobrancaWhatsApp.Status.PENDENTE,
                EnvioCobrancaWhatsApp.Status.ENVIANDO,
                EnvioCobrancaWhatsApp.Status.ENVIADO,
            ],
        )
        .order_by('-agendado_para')
        .values_list('agendado_para', flat=True)
        .first()
    )
    base = max(timezone.now(), (ultimo + intervalo) if ultimo else timezone.now())
    return _ajustar_para_janela(configuracao, base)


def _agendar_titulo(titulo, configuracao, *, alias, dias, manual=False):
    boleto, telefone, valores, mensagem = _dados_titulo(titulo, configuracao)
    if not telefone or not _consentimento_valido(configuracao, alias, titulo, telefone):
        return None, False
    evento = _evento(dias, manual=manual)
    agendado_para = _proximo_horario(configuracao)
    if not manual:
        semente = f'{alias}:{titulo.pk}:{evento}:{titulo.data_vencimento}'
        jitter = int(hashlib.sha256(semente.encode()).hexdigest()[:4], 16) % 46
        agendado_para += datetime.timedelta(seconds=jitter)
    envio, criado = EnvioCobrancaWhatsApp.objects.using('default').get_or_create(
        tenant_alias=alias,
        conta_receber_id=titulo.pk,
        evento=evento,
        data_vencimento=titulo.data_vencimento,
        defaults={
            'configuracao_id': configuracao.pk,
            'filial_cnpj': titulo.filial.cnpj,
            'boleto_id': boleto.pk if boleto else None,
            'cliente_id': titulo.cliente_id,
            'cliente_nome': valores['nome'],
            'telefone': telefone,
            'documento': valores['documento'],
            'valor': titulo.valor_saldo,
            'mensagem': mensagem,
            'parametros_template': [
                valores['nome'], valores['empresa'], valores['documento'],
                valores['valor'], valores['vencimento'], valores['link'],
            ],
            'agendado_para': agendado_para,
            'disparo_manual': manual,
        },
    )
    if not criado and envio.status in {
        EnvioCobrancaWhatsApp.Status.ERRO,
        EnvioCobrancaWhatsApp.Status.CANCELADO,
    }:
        envio.status = EnvioCobrancaWhatsApp.Status.PENDENTE
        envio.tentativas = 0
        envio.ultimo_erro = ''
        envio.agendado_para = agendado_para
        envio.disparo_manual = manual
        envio.save(using='default', update_fields=[
            'status', 'tentativas', 'ultimo_erro', 'agendado_para',
            'disparo_manual', 'updated_at',
        ])
        criado = True
    return envio, criado


def agendar_cobrancas_manuais(titulos, *, usuario=None, registrar_autorizacao=False):
    alias = get_current_database_alias()
    criados = 0
    ignorados = 0
    for titulo in titulos.select_related('cliente', 'filial', 'filial__empresa'):
        configuracao = configuracao_whatsapp_conectada(titulo.filial, alias)
        if not configuracao:
            ignorados += 1
            continue
        if registrar_autorizacao:
            registrar_consentimento(titulo, alias=alias, usuario=usuario)
        _, criado = _agendar_titulo(
            titulo, configuracao, alias=alias, dias=0, manual=True,
        )
        criados += int(criado)
        ignorados += int(not criado)
    return {'criados': criados, 'ignorados': ignorados}


def agendar_cobrancas_automaticas():
    hoje = timezone.localdate()

    def _agendar_banco():
        alias = get_current_database_alias()
        criados = 0
        filiais = Filial.objects.filter(ativo=True, empresa__ativo=True).select_related('empresa')
        for filial in filiais:
            configuracao = configuracao_whatsapp_conectada(filial, alias)
            if not configuracao or not configuracao.cobranca_ativa:
                continue
            for dias in configuracao.cobranca_dias_relativos or []:
                dias = int(dias)
                vencimento = hoje - datetime.timedelta(days=dias)
                titulos = ContaReceber.objects.filter(
                    filial=filial,
                    forma_pagamento__tipo=TipoFormaPagamento.BOLETO,
                    status__in=STATUS_ABERTOS,
                    valor_saldo__gt=0,
                    data_vencimento=vencimento,
                ).select_related('cliente', 'filial', 'filial__empresa')
                for titulo in titulos.iterator():
                    _, criado = _agendar_titulo(
                        titulo, configuracao, alias=alias, dias=dias,
                    )
                    criados += int(criado)
        return criados

    return TenantTaskService.executar_em_todos(_agendar_banco)


def _titulo_ainda_cobravel(envio):
    alias = envio.tenant_alias
    if alias != 'default':
        banco = EmpresaBanco.objects.using('default').filter(
            db_alias=alias, ativo=True, status=EmpresaBanco.Status.ATIVO,
        ).first()
        if not banco or not register_tenant_database(banco):
            return None
    with tenant_db(alias):
        titulo = ContaReceber.objects.filter(
            pk=envio.conta_receber_id,
            filial__cnpj=envio.filial_cnpj,
        ).select_related('cliente', 'filial').first()
        if not titulo or titulo.status not in STATUS_ABERTOS or titulo.valor_saldo <= 0:
            return None
        telefone = numero_whatsapp(titulo.cliente.celular or titulo.cliente.telefone)
        if telefone != envio.telefone:
            return None
        if not _consentimento_valido(envio.configuracao, alias, titulo, telefone):
            return None
        return titulo


def processar_proxima_cobranca():
    agora = timezone.now()
    hora_local = timezone.localtime(agora).time().replace(tzinfo=None)
    with transaction.atomic(using='default'):
        EnvioCobrancaWhatsApp.objects.using('default').filter(
            status=EnvioCobrancaWhatsApp.Status.ENVIANDO,
            updated_at__lt=agora - datetime.timedelta(minutes=15),
        ).update(
            status=EnvioCobrancaWhatsApp.Status.PENDENTE,
            ultimo_erro='Envio interrompido; reagendado.',
        )
        candidatos = (
            EnvioCobrancaWhatsApp.objects.using('default')
            .select_for_update()
            .select_related('configuracao', 'configuracao__filial')
            .filter(
                status=EnvioCobrancaWhatsApp.Status.PENDENTE,
                agendado_para__lte=agora,
                configuracao__ativo=True,
                configuracao__status=ConfiguracaoWhatsApp.Status.CONECTADO,
            )
            .filter(Q(configuracao__cobranca_ativa=True) | Q(disparo_manual=True))
            .order_by('agendado_para', 'id')
        )
        envio = None
        for candidato in candidatos[:50]:
            config = candidato.configuracao
            if not (config.cobranca_horario_inicio <= hora_local <= config.cobranca_horario_fim):
                continue
            intervalo = datetime.timedelta(minutes=config.cobranca_intervalo_minutos)
            if config.ultimo_envio_cobranca_em and agora - config.ultimo_envio_cobranca_em < intervalo:
                continue
            enviados_hoje = EnvioCobrancaWhatsApp.objects.using('default').filter(
                configuracao=config,
                status=EnvioCobrancaWhatsApp.Status.ENVIADO,
                enviado_em__date=timezone.localdate(),
            ).count()
            if enviados_hoje >= config.cobranca_limite_diario:
                continue
            if candidato.tentativas >= config.cobranca_max_tentativas:
                candidato.status = EnvioCobrancaWhatsApp.Status.ERRO
                candidato.ultimo_erro = 'Número máximo de tentativas atingido.'
                candidato.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
                continue
            envio = candidato
            envio.status = EnvioCobrancaWhatsApp.Status.ENVIANDO
            envio.tentativas += 1
            envio.ultimo_erro = ''
            envio.save(update_fields=['status', 'tentativas', 'ultimo_erro', 'updated_at'])
            config.ultimo_envio_cobranca_em = agora
            config.save(update_fields=['ultimo_envio_cobranca_em', 'updated_at'])
            break
    if not envio:
        return 0
    if not _titulo_ainda_cobravel(envio):
        envio.status = EnvioCobrancaWhatsApp.Status.CANCELADO
        envio.ultimo_erro = 'Título pago, cancelado, alterado ou sem autorização válida.'
        envio.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        return 0

    conversa = (
        ConversaWhatsApp.objects.using('default')
        .filter(
            configuracao=envio.configuracao,
            remote_jid=f'{envio.telefone}@s.whatsapp.net',
        )
        .order_by('-ativa', '-ultima_mensagem_em', '-created_at')
        .first()
    )
    if not conversa:
        conversa = ConversaWhatsApp.objects.using('default').create(
            configuracao=envio.configuracao,
            filial=envio.configuracao.filial,
            remote_jid=f'{envio.telefone}@s.whatsapp.net',
            telefone=envio.telefone,
            nome_contato=envio.cliente_nome,
            ativa=False,
            etapa='cobranca',
        )
    mensagem = MensagemWhatsApp.objects.using('default').create(
        conversa=conversa,
        direcao=MensagemWhatsApp.Direcao.SAIDA,
        tipo='aviso_boleto',
        texto=envio.mensagem,
        status='pendente',
    )
    try:
        cliente = cliente_whatsapp(envio.configuracao)
        if envio.configuracao.provedor == ConfiguracaoWhatsApp.Provedor.META_CLOUD:
            retorno = cliente.enviar_template(
                envio.telefone,
                envio.configuracao.cobranca_template_meta,
                envio.configuracao.cobranca_template_idioma,
                envio.parametros_template,
            )
        else:
            retorno = cliente.enviar_texto(envio.telefone, envio.mensagem)
    except GatewayWhatsAppError as exc:
        logger.warning('Falha ao enviar cobrança por WhatsApp: %s', exc)
        intervalo = datetime.timedelta(minutes=envio.configuracao.cobranca_intervalo_minutos)
        envio.status = (
            EnvioCobrancaWhatsApp.Status.ERRO
            if envio.tentativas >= envio.configuracao.cobranca_max_tentativas
            else EnvioCobrancaWhatsApp.Status.PENDENTE
        )
        envio.agendado_para = _ajustar_para_janela(envio.configuracao, agora + intervalo)
        envio.ultimo_erro = str(exc)
        mensagem.status = 'erro'
        mensagem.dados_evento = {'erro': str(exc)}
        envio.save(update_fields=['status', 'agendado_para', 'ultimo_erro', 'updated_at'])
        mensagem.save(update_fields=['status', 'dados_evento', 'updated_at'])
        return 0

    identificador = identificador_envio(retorno)
    envio.status = EnvioCobrancaWhatsApp.Status.ENVIADO
    envio.enviado_em = timezone.now()
    envio.identificador_externo = identificador
    envio.save(update_fields=[
        'status', 'enviado_em', 'identificador_externo', 'ultimo_erro', 'updated_at',
    ])
    mensagem.status = 'enviada'
    mensagem.identificador_externo = identificador
    mensagem.save(update_fields=['status', 'identificador_externo', 'updated_at'])
    return 1
