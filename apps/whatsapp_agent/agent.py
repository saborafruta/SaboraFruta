import re
import unicodedata
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Q
from django.urls import reverse

from apps.agenda.models import AgendaLinkPublico
from apps.cadastros.models import Cliente
from apps.core.models import EmpresaBanco, Filial
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.tenant_registry import register_tenant_database

from .flow import garantir_fluxo_padrao
from .models import ConversaWhatsApp, OpcaoMenuWhatsApp
from .tracking import gerar_token_agendamento


SAUDACOES = {
    'oi', 'ola', 'olá', 'bom dia', 'boa tarde', 'boa noite',
    'e ai', 'e aí', 'opa',
}
COMANDOS_MENU = {'menu', 'inicio', 'início', 'recomecar', 'reiniciar', 'cancelar'}
MOTIVOS_NAO_AGENDAMENTO = {
    '1': 'Não encontrou um horário',
    '2': 'Valor não serviu para o cliente',
    '3': 'Não encontrou o serviço',
    '4': 'Teve dificuldade para agendar',
    '5': 'Vai agendar depois',
}


def normalizar(texto):
    texto = unicodedata.normalize('NFKD', (texto or '').lower())
    return ''.join(item for item in texto if not unicodedata.combining(item)).strip()


def somente_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def localizar_cliente(filial, telefone):
    digitos = somente_digitos(telefone)
    sufixo = digitos[-8:]
    if len(sufixo) < 8:
        return None
    candidatos = Cliente.objects.for_filial(filial).filter(ativo=True).filter(
        Q(celular__icontains=sufixo) | Q(telefone__icontains=sufixo),
    )[:20]
    return next(
        (
            item for item in candidatos
            if somente_digitos(item.celular or item.telefone).endswith(sufixo)
        ),
        None,
    )


def _menu_principal(configuracao):
    return garantir_fluxo_padrao(configuracao)


def _menu_atual(conversa):
    codigo = (conversa.contexto or {}).get('menu_codigo')
    if codigo:
        menu = conversa.configuracao.menus.filter(codigo=codigo, ativo=True).first()
        if menu:
            return menu
    return _menu_principal(conversa.configuracao)


def _renderizar_menu(menu, saudacao=''):
    opcoes = list(menu.opcoes.filter(ativo=True).order_by('ordem', 'id'))
    partes = []
    if saudacao.strip():
        partes.append(saudacao.strip())
    conteudo = []
    if menu.mensagem.strip():
        conteudo.append(menu.mensagem.strip())
    conteudo.extend(f'*{opcao.chave}.* {opcao.titulo}' for opcao in opcoes)
    if conteudo:
        partes.append('\n'.join(conteudo))
    if opcoes:
        chaves = [f'*{opcao.chave}*' for opcao in opcoes]
        instrucao = chaves[0] if len(chaves) == 1 else ', '.join(chaves[:-1]) + f' ou {chaves[-1]}'
        partes.append(f'Responda com {instrucao}.')
    return '\n\n'.join(partes)


def _salvar_menu_atual(conversa, menu, etapa='aguardando_opcao'):
    contexto = dict(conversa.contexto or {})
    contexto['menu_codigo'] = menu.codigo
    conversa.etapa = etapa
    conversa.contexto = contexto
    conversa.save(update_fields=['etapa', 'contexto', 'updated_at'])


def _destino_agenda(conversa):
    """Traduz a filial central do WhatsApp para a filial do banco operacional."""
    if not settings.TENANT_DATABASE_ROUTING_ENABLED:
        return conversa.filial, 'default'

    banco = EmpresaBanco.objects.using('default').filter(
        empresa_id=conversa.filial.empresa_id,
        ativo=True,
        status=EmpresaBanco.Status.ATIVO,
    ).first()
    if not banco or not register_tenant_database(banco):
        return None, None

    filial = Filial.objects.using(banco.db_alias).filter(
        cnpj=conversa.filial.cnpj,
        ativo=True,
    ).first()
    return (filial, banco.db_alias) if filial else (None, None)


def _link_agendamento(conversa):
    filial, db_alias = _destino_agenda(conversa)
    if not filial:
        return ''

    link, _ = AgendaLinkPublico.objects.using(db_alias).get_or_create(filial=filial)
    if not link.ativo:
        link.ativo = True
        link.save(using=db_alias, update_fields=['ativo', 'updated_at'])
    TenantPublicLinkService.register(kind='agenda', token=link.token, db_alias=db_alias)
    caminho = reverse('agenda_publica:agendar', args=[link.token])
    rastreamento = gerar_token_agendamento(conversa)
    return f'{settings.PUBLIC_BASE_URL.rstrip("/")}{caminho}?{urlencode({"wa": rastreamento})}'


def reiniciar(conversa, incluir_saudacao=True):
    menu = _menu_principal(conversa.configuracao)
    conversa.atendimento_humano = False
    conversa.ativa = True
    conversa.etapa = 'aguardando_opcao'
    conversa.contexto = {'menu_codigo': menu.codigo}
    if conversa.etapa_crm == ConversaWhatsApp.EtapaCRM.NAO_CONVERTIDO:
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.NOVA
        conversa.agendamento_iniciado_em = None
        conversa.acompanhamento_enviado_em = None
    conversa.save(update_fields=[
        'etapa', 'contexto', 'atendimento_humano', 'ativa', 'etapa_crm',
        'agendamento_iniciado_em', 'acompanhamento_enviado_em', 'updated_at',
    ])
    saudacao = conversa.configuracao.mensagem_saudacao if incluir_saudacao else ''
    return _renderizar_menu(menu, saudacao)


def _encerrar(conversa, mensagem=''):
    conversa.etapa = 'encerrada'
    conversa.contexto = {}
    conversa.atendimento_humano = False
    conversa.ativa = False
    if conversa.etapa_crm not in {
        ConversaWhatsApp.EtapaCRM.AGENDADO,
        ConversaWhatsApp.EtapaCRM.CONCLUIDO,
    }:
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.NAO_CONVERTIDO
    conversa.save(update_fields=[
        'etapa', 'contexto', 'atendimento_humano', 'ativa', 'etapa_crm', 'updated_at',
    ])
    return mensagem.strip() or conversa.configuracao.mensagem_encerramento


def _palavras_opcao(opcao):
    palavras = re.split(r'[,;\n]+', opcao.palavras_chave or '')
    return {
        normalizar(valor) for valor in [opcao.chave, opcao.titulo, *palavras] if valor.strip()
    }


def _encontrar_opcao(menu, valor):
    for opcao in menu.opcoes.filter(ativo=True).order_by('ordem', 'id'):
        if valor in _palavras_opcao(opcao):
            return opcao
    return None


def _executar_opcao(conversa, opcao):
    config = conversa.configuracao
    acao = opcao.acao

    if acao == OpcaoMenuWhatsApp.Acao.AGENDA:
        link = _link_agendamento(conversa)
        if not link:
            atendimento = opcao.menu.opcoes.filter(
                acao=OpcaoMenuWhatsApp.Acao.ATENDIMENTO_HUMANO, ativo=True,
            ).first()
            instrucao = (
                f'responda *{atendimento.chave}*' if atendimento
                else 'escolha a opção de falar com um atendente'
            )
            return f'Não consegui abrir a agenda agora. Por favor, {instrucao}.'
        contexto = dict(conversa.contexto or {})
        contexto['link_agendamento'] = link
        conversa.contexto = contexto
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.INTERESSADO
        conversa.save(update_fields=['contexto', 'etapa_crm', 'updated_at'])
        introducao = opcao.mensagem.strip() or (
            'Perfeito! Para escolher o profissional, o serviço, o dia e o horário, '
            'acesse o link abaixo:'
        )
        return f'{introducao}\n\n{link}\n\nQuando quiser ver as opções novamente, digite *menu*.'

    if acao == OpcaoMenuWhatsApp.Acao.ATENDIMENTO_HUMANO:
        conversa.etapa = 'atendimento_humano'
        conversa.atendimento_humano = True
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO
        conversa.save(update_fields=[
            'etapa', 'atendimento_humano', 'etapa_crm', 'updated_at',
        ])
        return opcao.mensagem.strip() or config.mensagem_transferencia

    if acao == OpcaoMenuWhatsApp.Acao.ENCERRAR:
        return _encerrar(conversa, opcao.mensagem)

    if acao == OpcaoMenuWhatsApp.Acao.ABRIR_MENU:
        destino = opcao.menu_destino
        if not destino or not destino.ativo:
            return f'{config.mensagem_opcao_invalida}\n\n{_renderizar_menu(_menu_atual(conversa))}'
        _salvar_menu_atual(conversa, destino)
        return _renderizar_menu(destino, opcao.mensagem.strip())

    if acao == OpcaoMenuWhatsApp.Acao.MENU_PRINCIPAL:
        return reiniciar(conversa, incluir_saudacao=False)

    mensagem = opcao.mensagem.strip()
    if opcao.voltar_ao_menu:
        return _renderizar_menu(_menu_atual(conversa), mensagem)
    return mensagem or _renderizar_menu(_menu_atual(conversa))


def processar_mensagem(conversa, texto):
    valor = normalizar(texto)
    estava_inativa = not conversa.ativa
    primeira_interacao = conversa.etapa == 'inicio'

    if estava_inativa:
        conversa.ativa = True
        conversa.etapa = 'inicio'
        conversa.save(update_fields=['ativa', 'etapa', 'updated_at'])

    if conversa.atendimento_humano:
        return None

    if valor in {normalizar(item) for item in COMANDOS_MENU}:
        return reiniciar(conversa, incluir_saudacao=primeira_interacao or estava_inativa)

    if conversa.etapa == 'aguardando_motivo_nao_agendamento':
        motivo = '' if valor == 'pular' else MOTIVOS_NAO_AGENDAMENTO.get(valor, texto.strip()[:180])
        conversa.motivo_nao_agendamento = motivo
        conversa.save(update_fields=['motivo_nao_agendamento', 'updated_at'])
        return _encerrar(conversa, conversa.configuracao.mensagem_feedback_agendamento)

    if (
        conversa.etapa_crm == ConversaWhatsApp.EtapaCRM.AGUARDANDO_CLIENTE
        and valor in {'3', 'nao quero agendar agora'}
    ):
        conversa.etapa = 'aguardando_motivo_nao_agendamento'
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.NAO_CONVERTIDO
        conversa.save(update_fields=['etapa', 'etapa_crm', 'updated_at'])
        return conversa.configuracao.mensagem_motivo_nao_agendamento

    if valor in {normalizar(item) for item in SAUDACOES}:
        if primeira_interacao or estava_inativa:
            return reiniciar(conversa, incluir_saudacao=True)
        menu = _menu_atual(conversa)
        _salvar_menu_atual(conversa, menu)
        return _renderizar_menu(menu)

    menu = _menu_atual(conversa)
    opcao = _encontrar_opcao(menu, valor)
    if opcao:
        return _executar_opcao(conversa, opcao)

    _salvar_menu_atual(conversa, menu)
    return f'{conversa.configuracao.mensagem_opcao_invalida}\n\n{_renderizar_menu(menu)}'
