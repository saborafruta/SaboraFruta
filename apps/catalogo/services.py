import logging
import re
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import EmpresaBanco, Filial
from apps.whatsapp_agent.gateway import EvolutionClient, GatewayWhatsAppError
from apps.whatsapp_agent.models import ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp

from .models import PedidoCatalogo


logger = logging.getLogger(__name__)


def somente_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def numero_whatsapp(valor):
    numero = somente_digitos(valor)
    if len(numero) in {10, 11}:
        numero = f'55{numero}'
    if len(numero) == 12 and numero.startswith('55') and numero[4] in '6789':
        numero = f'{numero[:4]}9{numero[4:]}'
    return numero if 12 <= len(numero) <= 15 else ''


def localizar_cliente(filial, telefone):
    numero = numero_whatsapp(telefone)
    if not numero:
        return None
    sufixo = numero[-8:]
    candidatos = Cliente.objects.for_filial(filial).filter(ativo=True).filter(
        Q(celular__icontains=sufixo) | Q(telefone__icontains=sufixo),
    )[:50]
    return next((item for item in candidatos if numero_whatsapp(item.celular or item.telefone) == numero), None)


def obter_ou_criar_cliente(filial, nome, telefone, endereco=None):
    cliente = localizar_cliente(filial, telefone)
    endereco = endereco or {}
    if not cliente:
        cliente = Cliente.objects.create(
            filial=filial, tipo_pessoa='F', razao_social=nome,
            celular=numero_whatsapp(telefone), consumidor_final=True,
            endereco=endereco.get('logradouro', ''), numero=endereco.get('numero', ''),
            complemento=endereco.get('complemento', ''), bairro=endereco.get('bairro', ''),
            cidade=endereco.get('cidade', ''), uf=endereco.get('uf', ''),
            cep=somente_digitos(endereco.get('cep', ''))[:8],
        )
    else:
        alterados = []
        if nome and cliente.razao_social.casefold() != nome.casefold():
            cliente.razao_social = nome
            alterados.append('razao_social')
        if not cliente.celular:
            cliente.celular = numero_whatsapp(telefone)
            alterados.append('celular')
        for campo_cliente, campo_endereco in (
            ('endereco', 'logradouro'), ('numero', 'numero'), ('complemento', 'complemento'),
            ('bairro', 'bairro'), ('cidade', 'cidade'), ('uf', 'uf'),
        ):
            if endereco.get(campo_endereco) and not getattr(cliente, campo_cliente):
                setattr(cliente, campo_cliente, endereco[campo_endereco])
                alterados.append(campo_cliente)
        if endereco.get('cep') and not cliente.cep:
            cliente.cep = somente_digitos(endereco['cep'])[:8]
            alterados.append('cep')
        if alterados:
            cliente.save(update_fields=[*alterados, 'updated_at'])
    ClienteFilial.objects.update_or_create(cliente=cliente, filial=filial, defaults={'ativo': True})
    return cliente


def _configuracao_whatsapp(filial, db_alias):
    if not settings.TENANT_DATABASE_ROUTING_ENABLED or db_alias == 'default':
        filial_central = Filial.objects.using('default').filter(pk=filial.pk, ativo=True).first()
    else:
        banco = EmpresaBanco.objects.using('default').filter(
            db_alias=db_alias, ativo=True, status=EmpresaBanco.Status.ATIVO,
        ).first()
        filial_central = Filial.objects.using('default').filter(
            empresa_id=getattr(banco, 'empresa_id', None), cnpj=filial.cnpj, ativo=True,
        ).first()
    if not filial_central:
        return None
    return ConfiguracaoWhatsApp.objects.using('default').filter(
        filial_id=filial_central.pk, ativo=True, status=ConfiguracaoWhatsApp.Status.CONECTADO,
    ).first()


def formatar_resumo(pedido, configuracao):
    itens = '\n'.join(
        f'• {item.quantidade}x {item.descricao} — R$ {item.valor_total:.2f}'.replace('.', ',')
        for item in pedido.itens.all()
    )
    frete = 'A combinar' if pedido.frete_a_combinar else f'R$ {pedido.valor_frete:.2f}'.replace('.', ',')
    entrega = pedido.get_modalidade_display()
    if pedido.entrega_em:
        entrega += f' em {timezone.localtime(pedido.entrega_em):%d/%m/%Y às %H:%M}'
    valores = {
        'numero': pedido.numero, 'nome': pedido.nome_cliente, 'itens': itens,
        'subtotal': f'R$ {pedido.subtotal:.2f}'.replace('.', ','), 'frete': frete,
        'desconto': f'R$ {pedido.valor_desconto:.2f}'.replace('.', ','),
        'cupom': pedido.codigo_cupom or 'Nenhum',
        'total': f'R$ {pedido.total:.2f}'.replace('.', ','), 'entrega': entrega,
        'pagamento': pedido.get_forma_pagamento_display(),
        'observacao': pedido.observacao or 'Nenhuma',
    }
    texto = configuracao.mensagem_resumo_pedido
    if pedido.valor_desconto and '{desconto}' not in texto:
        desconto_formatado = f'{pedido.valor_desconto:.2f}'.replace('.', ',')
        linha_desconto = (
            f'*Cupom {pedido.codigo_cupom}:* '
            f'- R$ {desconto_formatado}\n'
        )
        if '*Frete:*' in texto:
            texto = texto.replace('*Frete:*', linha_desconto + '*Frete:*', 1)
        else:
            texto += '\n' + linha_desconto.rstrip()
    for chave, valor in valores.items():
        texto = texto.replace(f'{{{chave}}}', str(valor))
    return texto


def enviar_resumo_whatsapp(pedido, *, db_alias, conversa=None):
    config = conversa.configuracao if conversa else _configuracao_whatsapp(pedido.filial, db_alias)
    if not config:
        return False, 'O WhatsApp da filial não está conectado.'
    telefone = numero_whatsapp(pedido.telefone)
    if not telefone:
        return False, 'O pedido não possui um WhatsApp válido.'
    if not conversa:
        conversa, _ = ConversaWhatsApp.objects.using('default').get_or_create(
            configuracao_id=config.pk, remote_jid=f'{telefone}@s.whatsapp.net',
            defaults={'filial_id': config.filial_id, 'telefone': telefone, 'nome_contato': pedido.nome_cliente},
        )
    texto = formatar_resumo(pedido, config)
    contexto = dict(conversa.contexto or {})
    contexto.update({'catalogo_pedido_id': pedido.pk, 'catalogo_db_alias': db_alias})
    conversa.contexto = contexto
    conversa.etapa = 'aguardando_confirmacao_pedido'
    conversa.ativa = True
    conversa.ultima_mensagem_em = timezone.now()
    conversa.save(using='default', update_fields=['contexto', 'etapa', 'ativa', 'ultima_mensagem_em', 'updated_at'])
    PedidoCatalogo.objects.using(db_alias).filter(pk=pedido.pk).update(conversa_id=conversa.pk)
    saida = MensagemWhatsApp.objects.using('default').create(
        conversa_id=conversa.pk, direcao=MensagemWhatsApp.Direcao.SAIDA,
        texto=texto, tipo='resumo_pedido_catalogo', status='pendente',
    )
    try:
        retorno = EvolutionClient(config).enviar_texto(telefone, texto)
        saida.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
        saida.status = 'enviada'
        ok, mensagem = True, 'Resumo enviado pelo WhatsApp.'
    except GatewayWhatsAppError as exc:
        saida.status = 'erro'
        saida.dados_evento = {'erro': str(exc)}
        ok, mensagem = False, f'Pedido salvo, mas o WhatsApp não pôde ser enviado: {exc}'
        logger.warning('Falha ao enviar resumo do pedido %s: %s', pedido.numero, exc)
    saida.save(using='default', update_fields=['identificador_externo', 'status', 'dados_evento', 'updated_at'])
    return ok, mensagem


def enviar_atualizacao_whatsapp(pedido, *, db_alias):
    conversa = None
    if pedido.conversa_id:
        conversa = ConversaWhatsApp.objects.using('default').filter(pk=pedido.conversa_id).first()
    config = conversa.configuracao if conversa else _configuracao_whatsapp(pedido.filial, db_alias)
    if not config:
        return False
    texto = config.mensagem_atualizacao_pedido
    valores = {
        'numero': pedido.numero,
        'status': pedido.get_status_display(),
        'nome': pedido.nome_cliente,
        'total': f'R$ {pedido.total:.2f}'.replace('.', ','),
    }
    for chave, valor in valores.items():
        texto = texto.replace(f'{{{chave}}}', str(valor))
    telefone = numero_whatsapp(pedido.telefone)
    if not telefone:
        return False
    if not conversa:
        conversa, _ = ConversaWhatsApp.objects.using('default').get_or_create(
            configuracao_id=config.pk, remote_jid=f'{telefone}@s.whatsapp.net',
            defaults={'filial_id': config.filial_id, 'telefone': telefone, 'nome_contato': pedido.nome_cliente},
        )
    saida = MensagemWhatsApp.objects.using('default').create(
        conversa_id=conversa.pk, direcao=MensagemWhatsApp.Direcao.SAIDA,
        texto=texto, tipo='atualizacao_pedido_catalogo', status='pendente',
    )
    try:
        retorno = EvolutionClient(config).enviar_texto(telefone, texto)
        saida.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
        saida.status = 'enviada'
        enviado = True
    except GatewayWhatsAppError as exc:
        saida.status = 'erro'
        saida.dados_evento = {'erro': str(exc)}
        logger.warning('Falha ao atualizar pedido %s pelo WhatsApp: %s', pedido.numero, exc)
        enviado = False
    saida.save(using='default', update_fields=['identificador_externo', 'status', 'dados_evento', 'updated_at'])
    return enviado


def processar_resposta_whatsapp(conversa, valor):
    contexto = conversa.contexto or {}
    pedido_id = contexto.get('catalogo_pedido_id')
    alias = contexto.get('catalogo_db_alias')
    if not pedido_id or not alias:
        return None
    pedido = PedidoCatalogo.objects.using(alias).filter(pk=pedido_id).first()
    if not pedido or pedido.status != PedidoCatalogo.Status.AGUARDANDO_CLIENTE:
        return None
    config = conversa.configuracao
    resposta = (valor or '').strip().casefold()
    if resposta == config.pedido_resposta_confirmar.casefold():
        pedido.status = PedidoCatalogo.Status.AGUARDANDO_LOJA
        pedido.confirmado_cliente_em = timezone.now()
        pedido.save(using=alias, update_fields=['status', 'confirmado_cliente_em', 'updated_at'])
        conversa.etapa = 'pedido_confirmado_cliente'
        conversa.save(using='default', update_fields=['etapa', 'updated_at'])
        return config.mensagem_pedido_recebido.replace('{numero}', pedido.numero)
    if resposta == config.pedido_resposta_alterar.casefold():
        pedido.status = PedidoCatalogo.Status.CANCELADO
        pedido.save(using=alias, update_fields=['status', 'updated_at'])
        conversa.etapa = 'aguardando_opcao'
        conversa.save(using='default', update_fields=['etapa', 'updated_at'])
        return 'Tudo bem. O pedido anterior foi cancelado. Use o link do catálogo para montar outro carrinho.'
    if resposta == config.pedido_resposta_cancelar.casefold():
        pedido.status = PedidoCatalogo.Status.CANCELADO
        pedido.save(using=alias, update_fields=['status', 'updated_at'])
        conversa.etapa = 'encerrada'
        conversa.ativa = False
        conversa.save(using='default', update_fields=['etapa', 'ativa', 'updated_at'])
        return 'Pedido cancelado. Quando precisar, envie *oi* para começar novamente.'
    return 'Não entendi. Responda com *{}* para confirmar, *{}* para refazer ou *{}* para cancelar.'.format(
        config.pedido_resposta_confirmar, config.pedido_resposta_alterar, config.pedido_resposta_cancelar,
    )
