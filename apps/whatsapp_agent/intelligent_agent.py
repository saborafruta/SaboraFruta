import json
import logging
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.agenda.models import Agendamento, ProfissionalAgenda, ProfissionalServico
from apps.agenda.services import criar_agendamento, listar_horarios
from apps.catalogo.services import obter_ou_criar_cliente
from apps.core.tenant_context import tenant_db
from apps.produtos.models import Produto

from .agent import (
    _destino_agenda,
    _link_agendamento,
    _link_catalogo,
)
from .ai_service import IAWhatsAppError, OpenAIWhatsAppClient
from .models import ConversaWhatsApp


logger = logging.getLogger(__name__)


ETAPAS_DETERMINISTICAS = {
    'aguardando_confirmacao_pedido',
    'aguardando_motivo_nao_agendamento',
}

SCHEMA_DECISAO = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'acao': {
            'type': 'string',
            'enum': [
                'responder', 'consultar_agenda', 'agendar', 'abrir_agenda',
                'abrir_catalogo', 'atendente', 'encerrar', 'mostrar_menu',
            ],
        },
        'mensagem': {'type': 'string'},
        'servico_id': {'type': ['integer', 'null']},
        'profissional_id': {'type': ['integer', 'null']},
        'data': {'type': 'string'},
        'periodo': {'type': 'string', 'enum': ['', 'manha', 'tarde', 'noite']},
        'horario': {'type': 'string'},
        'nome_cliente': {'type': 'string'},
    },
    'required': [
        'acao', 'mensagem', 'servico_id', 'profissional_id', 'data',
        'periodo', 'horario', 'nome_cliente',
    ],
}


def _catalogo_negocio(conversa):
    filial, alias = _destino_agenda(conversa)
    if not filial:
        return None, None, {'servicos': [], 'produtos': []}
    with tenant_db(alias):
        vinculos = list(
            ProfissionalServico.objects.filter(
                profissional__filial=filial,
                profissional__ativo=True,
                ativo=True,
                servico__ativo=True,
                servico__agendavel=True,
            ).select_related('profissional__funcionario', 'servico')
        )
        servicos = {}
        for vinculo in vinculos:
            item = servicos.setdefault(vinculo.servico_id, {
                'id': vinculo.servico_id,
                'nome': vinculo.servico.descricao,
                'duracao_minutos': vinculo.servico.duracao_servico_minutos,
                'preco': str(vinculo.servico.preco_venda),
                'profissionais': [],
            })
            item['profissionais'].append({
                'id': vinculo.profissional_id,
                'nome': vinculo.profissional.funcionario.nome,
            })
        produtos = list(
            Produto.objects.for_filial(filial).filter(
                ativo=True,
                exibir_catalogo=True,
            ).values('id', 'descricao', 'catalogo_descricao', 'preco_venda')[:60]
        )
    return filial, alias, {
        'servicos': list(servicos.values()),
        'produtos': [
            {
                'id': item['id'],
                'nome': item['descricao'],
                'descricao': item['catalogo_descricao'],
                'preco': str(item['preco_venda']),
            }
            for item in produtos
        ],
    }


def _historico(conversa):
    mensagens = list(conversa.mensagens.order_by('-created_at')[:12])
    mensagens.reverse()
    return [
        {
            'papel': 'cliente' if item.direcao == item.Direcao.ENTRADA else 'assistente',
            'texto': item.texto[:1200],
        }
        for item in mensagens
    ]


def _instrucoes(conversa):
    config = conversa.configuracao
    return f'''
Você é o atendente virtual da filial {conversa.filial.nome_fantasia or conversa.filial.razao_social}.
Responda sempre em português do Brasil, de forma curta, cordial e natural para WhatsApp.
Você é a porta de entrada do atendimento: responda toda mensagem recebida, inclusive saudações,
perguntas abertas e respostas curtas ou numéricas. Se a pessoa apenas cumprimentar, cumprimente-a
e pergunte naturalmente como pode ajudar, sem exibir um menu numerado.
Você atende agenda e/ou catálogo conforme o modo: {config.modo_atendimento}.
Nunca invente serviço, produto, preço, profissional, data ou horário. A disponibilidade real será
consultada pelo sistema depois da sua decisão. Se o cliente quiser agendar e já informou serviço e
dia, use consultar_agenda. Quando ele escolher um horário oferecido, use agendar. Se houver mais de
um profissional e ele não escolher, deixe profissional_id nulo. Se faltar o nome, ainda use agendar:
o sistema fará a pergunta. Para pedidos, dúvidas de produtos ou intenção de compra, use
abrir_catalogo; o pedido e o pagamento continuam no link seguro. Use atendente quando a pessoa pedir
um humano e encerrar quando pedir para finalizar. Se a pessoa pedir menu ou opções, use
mostrar_menu e explique naturalmente o que você consegue fazer, sem criar um menu numerado.
Datas devem sair em AAAA-MM-DD e horários em HH:MM. Hoje é {timezone.localdate().isoformat()}.
{config.ia_instrucoes}
'''.strip()


def _dentro_horario_ia(config, agora=None):
    if not config.ia_restringir_horario:
        return True
    hora = timezone.localtime(agora or timezone.now()).time().replace(tzinfo=None)
    inicio = config.ia_horario_inicio
    fim = config.ia_horario_fim
    if inicio < fim:
        return inicio <= hora < fim
    return hora >= inicio or hora < fim


def _mensagem_fora_horario(conversa):
    config = conversa.configuracao
    conversa.ativa = True
    conversa.atendimento_humano = True
    conversa.etapa = 'aguardando_atendimento_humano'
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO
    conversa.save(update_fields=[
        'ativa', 'atendimento_humano', 'etapa', 'etapa_crm', 'updated_at',
    ])
    try:
        return config.ia_mensagem_fora_horario.format(
            inicio=config.ia_horario_inicio.strftime('%H:%M'),
            fim=config.ia_horario_fim.strftime('%H:%M'),
        )
    except (KeyError, ValueError):
        logger.warning(
            'Mensagem fora do horário inválida na configuração %s.',
            config.pk,
        )
        return (
            'Olá! Recebemos sua mensagem, mas nosso time não está disponível agora. '
            'Um atendente entrará em contato assim que possível.'
        )


def _marcar_atendimento_ia(conversa):
    campos = []
    if not conversa.ativa:
        conversa.ativa = True
        campos.append('ativa')
    if conversa.etapa != 'ia_atendimento':
        conversa.etapa = 'ia_atendimento'
        campos.append('etapa')
    if campos:
        conversa.save(update_fields=[*campos, 'updated_at'])


def _mensagem_capacidades(config):
    if config.modo_atendimento == config.ModoAtendimento.AGENDA:
        recursos = 'fazer um agendamento, consultar horários ou falar com um atendente'
    elif config.modo_atendimento == config.ModoAtendimento.CATALOGO:
        recursos = 'comprar produtos, acompanhar seu pedido ou falar com um atendente'
    else:
        recursos = (
            'fazer um agendamento, comprar produtos ou falar com um atendente'
        )
    return f'Posso ajudar você a {recursos}. O que você precisa?'


def _encaminhar_erro_ia(conversa):
    config = conversa.configuracao
    conversa.ativa = True
    conversa.atendimento_humano = True
    conversa.etapa = 'aguardando_atendimento_humano'
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO
    conversa.save(update_fields=[
        'ativa', 'atendimento_humano', 'etapa', 'etapa_crm', 'updated_at',
    ])
    return config.ia_mensagem_erro.strip() or (
        'Estou com uma instabilidade no atendimento automático agora. '
        'Recebi sua mensagem e vou encaminhá-la para nossa equipe continuar com você.'
    )


def resposta_fora_horario(conversa):
    config = conversa.configuracao
    if (
        not config.ia_ativa
        or conversa.atendimento_humano
        or _dentro_horario_ia(config)
    ):
        return None
    return _mensagem_fora_horario(conversa)


def _periodo(hora):
    if hora.hour < 12:
        return 'manha'
    if hora.hour < 18:
        return 'tarde'
    return 'noite'


def _salvar_contexto_agenda(conversa, dados):
    contexto = dict(conversa.contexto or {})
    atual = dict(contexto.get('ia_agenda') or {})
    atual.update({chave: valor for chave, valor in dados.items() if valor not in (None, '')})
    contexto['ia_agenda'] = atual
    conversa.contexto = contexto
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO
    conversa.save(update_fields=['contexto', 'etapa_crm', 'updated_at'])
    return atual


def _servico_e_profissional(filial, estado):
    servico = Produto.objects.for_filial(filial).filter(
        pk=estado.get('servico_id'), ativo=True, agendavel=True,
    ).first()
    if not servico:
        return None, None
    profissionais = ProfissionalAgenda.objects.filter(
        filial=filial,
        ativo=True,
        servicos_vinculados__servico=servico,
        servicos_vinculados__ativo=True,
    ).select_related('funcionario').distinct()
    profissional = profissionais.filter(pk=estado.get('profissional_id')).first()
    if not profissional and profissionais.count() == 1:
        profissional = profissionais.first()
    return servico, profissional


def _consultar_agenda(conversa, decisao, negocio, filial, alias):
    estado = _salvar_contexto_agenda(conversa, {
        'servico_id': decisao.get('servico_id'),
        'profissional_id': decisao.get('profissional_id'),
        'data': decisao.get('data'),
        'periodo': decisao.get('periodo'),
    })
    if not estado.get('servico_id'):
        nomes = ', '.join(item['nome'] for item in negocio['servicos'][:8])
        return f'Qual serviço você quer agendar? Temos: {nomes}.' if nomes else (
            'Ainda não há serviços disponíveis para agendamento.'
        )
    try:
        dia = date.fromisoformat(estado.get('data') or '')
        if not timezone.localdate() <= dia <= timezone.localdate() + timedelta(days=60):
            raise ValueError
    except ValueError:
        return 'Para qual dia você quer o atendimento? Pode responder, por exemplo, “amanhã”.'

    with tenant_db(alias):
        servico, profissional = _servico_e_profissional(filial, estado)
        if not servico:
            return 'Não encontrei esse serviço. Qual dos serviços disponíveis você deseja?'
        if not profissional:
            profissionais = [
                item['nome'] for item in next(
                    (item for item in negocio['servicos'] if item['id'] == servico.pk),
                    {'profissionais': []},
                )['profissionais']
            ]
            if profissionais:
                return 'Com qual profissional você prefere? ' + ', '.join(profissionais) + '.'
            return 'Não há profissional disponível para esse serviço.'
        horarios = listar_horarios(profissional, [servico], dia)

    if not horarios:
        return 'Não encontrei horários disponíveis nesse dia. Quer tentar outra data?'
    periodo = estado.get('periodo')
    if not periodo:
        periodos = []
        for codigo, rotulo in (('manha', 'manhã'), ('tarde', 'tarde'), ('noite', 'noite')):
            if any(_periodo(timezone.localtime(item)) == codigo for item in horarios):
                periodos.append(rotulo)
        return 'Tenho horários disponíveis pela ' + ', '.join(periodos) + '. Qual período você prefere?'
    filtrados = [
        timezone.localtime(item).strftime('%H:%M')
        for item in horarios
        if _periodo(timezone.localtime(item)) == periodo
    ]
    if not filtrados:
        return f'Não há horários disponíveis nesse período. Quer consultar outro período ou dia?'
    _salvar_contexto_agenda(conversa, {
        'profissional_id': profissional.pk,
        'horarios_oferecidos': filtrados,
    })
    return 'Os horários disponíveis são: ' + ', '.join(filtrados[:12]) + '. Qual você prefere?'


def _formatar_confirmacao(config, agendamento):
    valores = {
        'nome': agendamento.pessoa_atendida_nome,
        'servicos': ', '.join(item.descricao for item in agendamento.itens.all()),
        'profissional': str(agendamento.profissional),
        'data': timezone.localtime(agendamento.inicio).strftime('%d/%m/%Y'),
        'horario': timezone.localtime(agendamento.inicio).strftime('%H:%M'),
    }
    texto = config.mensagem_confirmacao_agendamento
    for chave, valor in valores.items():
        texto = texto.replace(f'{{{chave}}}', str(valor))
    return texto


def _agendar(conversa, decisao, negocio, filial, alias):
    estado = _salvar_contexto_agenda(conversa, {
        'servico_id': decisao.get('servico_id'),
        'profissional_id': decisao.get('profissional_id'),
        'data': decisao.get('data'),
        'periodo': decisao.get('periodo'),
        'horario': decisao.get('horario'),
        'nome_cliente': decisao.get('nome_cliente'),
    })
    nome = (
        estado.get('nome_cliente')
        or (conversa.cliente.nome_display if conversa.cliente_id else '')
        or conversa.nome_contato
    ).strip()
    if not nome or nome.casefold() in {'cliente', 'whatsapp'}:
        return 'Antes de confirmar, qual é o seu nome?'
    try:
        dia = date.fromisoformat(estado.get('data') or '')
        hora = datetime.strptime(estado.get('horario') or '', '%H:%M').time()
    except ValueError:
        return _consultar_agenda(conversa, decisao, negocio, filial, alias)

    with tenant_db(alias):
        servico, profissional = _servico_e_profissional(filial, estado)
        if not servico or not profissional:
            return _consultar_agenda(conversa, decisao, negocio, filial, alias)
        disponiveis = listar_horarios(profissional, [servico], dia)
        inicio = next(
            (item for item in disponiveis if timezone.localtime(item).time().replace(second=0, microsecond=0) == hora),
            None,
        )
        if not inicio:
            return 'Esse horário não está mais disponível. Vou consultar novamente para você.\n\n' + _consultar_agenda(
                conversa, decisao, negocio, filial, alias,
            )
        try:
            cliente = obter_ou_criar_cliente(filial, nome, conversa.telefone)
            agendamento = criar_agendamento(
                filial=filial,
                profissional=profissional,
                servicos=[servico],
                inicio=inicio,
                pessoa_atendida_nome=nome,
                cliente=cliente,
                telefone=conversa.telefone,
                origem=Agendamento.Origem.WHATSAPP_NAO_OFICIAL,
                observacao='Agendamento realizado pelo agente de IA no WhatsApp.',
            )
            texto_confirmacao = _formatar_confirmacao(conversa.configuracao, agendamento)
        except ValidationError as exc:
            return 'Não consegui confirmar: ' + ' '.join(exc.messages)

    contexto = dict(conversa.contexto or {})
    contexto.pop('ia_agenda', None)
    contexto['agendamento_id'] = agendamento.pk
    contexto['agendamento_db_alias'] = alias
    conversa.contexto = contexto
    conversa.etapa = 'agendado_ia'
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDADO
    conversa.agendamento_confirmado_em = timezone.now()
    conversa.cliente_id = getattr(cliente, 'pk', None) if alias == 'default' else conversa.cliente_id
    conversa.save(update_fields=[
        'contexto', 'etapa', 'etapa_crm', 'agendamento_confirmado_em',
        'cliente_id', 'updated_at',
    ])
    return texto_confirmacao


def processar_com_ia(conversa, texto, mensagem):
    config = conversa.configuracao
    if not config.ia_ativa or conversa.atendimento_humano:
        return None
    # Confirmações transacionais continuam determinísticas: a IA não decide
    # nem inventa dados em etapas que alteram agenda ou pedido.
    if conversa.etapa in ETAPAS_DETERMINISTICAS:
        return None
    aviso_fora_horario = resposta_fora_horario(conversa)
    if aviso_fora_horario:
        return aviso_fora_horario
    _marcar_atendimento_ia(conversa)
    filial, alias, negocio = _catalogo_negocio(conversa)
    entrada = json.dumps({
        'mensagem_atual': texto,
        'cliente_conhecido': conversa.cliente.nome_display if conversa.cliente_id else '',
        'estado_agenda': (conversa.contexto or {}).get('ia_agenda') or {},
        'historico': _historico(conversa),
        'negocio': negocio,
    }, ensure_ascii=False)
    try:
        resultado = OpenAIWhatsAppClient(config).responder_estruturado(
            conversa=conversa,
            mensagem=mensagem,
            instrucoes=_instrucoes(conversa),
            entrada=entrada,
            schema=SCHEMA_DECISAO,
        )
    except IAWhatsAppError as exc:
        logger.warning('Agente de IA indisponível na conversa %s: %s', conversa.pk, exc)
        return _encaminhar_erro_ia(conversa)
    decisao = resultado.conteudo
    acao = decisao['acao']
    if acao == 'consultar_agenda' and filial:
        return _consultar_agenda(conversa, decisao, negocio, filial, alias)
    if acao == 'agendar' and filial:
        return _agendar(conversa, decisao, negocio, filial, alias)
    if acao == 'abrir_catalogo':
        link = _link_catalogo(conversa)
        mensagem_decisao = decisao['mensagem'].strip()
        return (
            f'{mensagem_decisao}\n\n{link}'.strip()
            if link else mensagem_decisao or _mensagem_capacidades(config)
        )
    if acao == 'abrir_agenda':
        link = _link_agendamento(conversa)
        mensagem_decisao = decisao['mensagem'].strip()
        return (
            f'{mensagem_decisao}\n\n{link}'.strip()
            if link else mensagem_decisao or _mensagem_capacidades(config)
        )
    if acao == 'atendente':
        conversa.atendimento_humano = True
        conversa.etapa = 'atendimento_humano'
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO
        conversa.save(update_fields=['atendimento_humano', 'etapa', 'etapa_crm', 'updated_at'])
        return decisao['mensagem'].strip() or config.mensagem_transferencia
    if acao == 'encerrar':
        conversa.ativa = False
        conversa.etapa = 'encerrada'
        conversa.save(update_fields=['ativa', 'etapa', 'updated_at'])
        return decisao['mensagem'].strip() or config.mensagem_encerramento
    if acao == 'mostrar_menu':
        return decisao['mensagem'].strip() or _mensagem_capacidades(config)
    return decisao['mensagem'].strip() or _mensagem_capacidades(config)
