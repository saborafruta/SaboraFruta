import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.agenda.models import Agendamento, ProfissionalAgenda, ProfissionalServico
from apps.agenda.services import criar_agendamento, listar_horarios
from apps.cadastros.services.cep_service import CepService
from apps.catalogo.models import (
    CatalogoConfiguracao,
    CupomCatalogo,
    ItemPedidoCatalogo,
    PedidoCatalogo,
)
from apps.catalogo.services import (
    formatar_resumo,
    localizar_cliente,
    notificar_novo_pedido,
    obter_ou_criar_cliente,
)
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.tenant_context import tenant_db
from apps.produtos.models import Produto

from .agent import _destino_agenda, normalizar
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
                'responder', 'consultar_agenda', 'consultar_datas', 'agendar', 'abrir_agenda',
                'abrir_catalogo', 'gerenciar_pedido', 'confirmar_pedido',
                'cancelar_pedido', 'atendente', 'encerrar', 'mostrar_menu',
            ],
        },
        'mensagem': {'type': 'string'},
        'servico_id': {'type': ['integer', 'null']},
        'servico_ids': {
            'type': 'array',
            'items': {'type': 'integer'},
        },
        'confirmou_servicos': {'type': 'boolean'},
        'profissional_id': {'type': ['integer', 'null']},
        'aceita_qualquer_profissional': {'type': 'boolean'},
        'aceita_qualquer_periodo': {'type': 'boolean'},
        'dias_semana': {
            'type': 'array',
            'items': {'type': 'integer', 'minimum': 0, 'maximum': 6},
        },
        'data': {'type': 'string'},
        'periodo': {'type': 'string', 'enum': ['', 'manha', 'tarde', 'noite']},
        'horario': {'type': 'string'},
        'nome_cliente': {'type': 'string'},
        'confirmou_nome': {'type': 'boolean'},
        'itens_pedido': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'produto_id': {'type': 'integer'},
                    'quantidade': {'type': 'integer', 'minimum': 0, 'maximum': 999},
                    'operacao': {'type': 'string', 'enum': ['adicionar', 'definir', 'remover']},
                },
                'required': ['produto_id', 'quantidade', 'operacao'],
            },
        },
        'avancar_pedido': {'type': 'boolean'},
        'modalidade': {'type': 'string', 'enum': ['', 'entrega', 'retirada']},
        'cep': {'type': 'string'},
        'logradouro': {'type': 'string'},
        'numero_endereco': {'type': 'string'},
        'complemento': {'type': 'string'},
        'bairro': {'type': 'string'},
        'cidade': {'type': 'string'},
        'uf': {'type': 'string'},
        'forma_pagamento': {'type': 'string', 'enum': ['', 'pix', 'dinheiro', 'cartao', 'a_combinar']},
        'troco_para': {'type': 'string'},
        'observacao': {'type': 'string'},
        'sem_observacao': {'type': 'boolean'},
        'cupom_codigo': {'type': 'string'},
        'data_entrega': {'type': 'string'},
        'hora_entrega': {'type': 'string'},
        'confirmou_pedido': {'type': 'boolean'},
    },
    'required': [
        'acao', 'mensagem', 'servico_id', 'servico_ids', 'confirmou_servicos',
        'profissional_id', 'aceita_qualquer_profissional',
        'aceita_qualquer_periodo', 'dias_semana', 'data',
        'periodo', 'horario', 'nome_cliente', 'confirmou_nome',
        'itens_pedido', 'avancar_pedido', 'modalidade', 'cep', 'logradouro',
        'numero_endereco', 'complemento', 'bairro', 'cidade', 'uf',
        'forma_pagamento', 'troco_para', 'observacao', 'sem_observacao',
        'cupom_codigo', 'data_entrega', 'hora_entrega', 'confirmou_pedido',
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
            ).exclude(tipo_produto=Produto.TipoProduto.SERVICO).select_related(
                'categoria',
            ).order_by('-catalogo_destaque', 'catalogo_ordem', 'descricao')[:60]
        )
        configuracao_catalogo, _ = CatalogoConfiguracao.objects.get_or_create(filial=filial)
        cliente = localizar_cliente(filial, conversa.telefone)
    return filial, alias, {
        'servicos': list(servicos.values()),
        'produtos': [
            {
                'id': item.pk,
                'nome': item.descricao,
                'descricao': item.catalogo_descricao,
                'preco': str(item.preco_venda),
                'categoria': item.categoria.nome if item.categoria_id else 'Outros',
                'quantidade_maxima': item.catalogo_quantidade_maxima,
            }
            for item in produtos
        ],
        'catalogo': {
            'ativo': bool(configuracao_catalogo and configuracao_catalogo.ativo),
            'entrega_ativa': bool(configuracao_catalogo and configuracao_catalogo.entrega_ativa),
            'retirada_ativa': bool(configuracao_catalogo and configuracao_catalogo.retirada_ativa),
            'pedido_minimo': str(configuracao_catalogo.pedido_minimo if configuracao_catalogo else 0),
        },
        'cliente': ({
            'nome': cliente.nome_display,
            'cep': cliente.cep,
            'logradouro': cliente.endereco,
            'numero': cliente.numero,
            'complemento': cliente.complemento,
            'bairro': cliente.bairro,
            'cidade': cliente.cidade,
            'uf': cliente.uf,
        } if cliente else None),
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
consultada pelo sistema depois da sua decisão. Assim que identificar intenção de agendamento, use
consultar_agenda, mesmo que ainda faltem dados. Extraia serviço, dia e período em qualquer ordem e
preserve os dados que já constam em estado_agenda. Use servico_ids com todos os serviços escolhidos;
servico_id deve repetir o primeiro por compatibilidade. O sistema oferecerá serviços complementares.
Quando o cliente perguntar quais dias ou datas possuem vaga, quando existe horário, qual é a primeira
data livre ou disser que aceita qualquer dia, use consultar_datas. Não escolha uma data por ele e não
o obrigue a tentar datas uma por uma. Se a mensagem não responder à pergunta anterior, responda ao
que o cliente realmente perguntou e depois faça somente a próxima pergunta necessária do fluxo.
Ao consultar datas, dias_semana pode restringir a busca: segunda=0, terça=1, quarta=2, quinta=3,
sexta=4, sábado=5 e domingo=6. “Fim de semana” significa [5, 6]. Sem restrição, use [].
Preencha serviço, profissional, data, período ou horário somente quando o cliente tiver informado
esse dado explicitamente na conversa. “Quero agendar” não informa nenhum desses dados. Se o cliente
perguntar qual data está sendo consultada, responda à pergunta com base em estado_agenda em vez de
repetir os horários.
Quando o cliente disser que não quer adicionais ou terminar de escolhê-los, marque
confirmou_servicos=true. Nunca escreva horários por conta própria: o sistema fará a consulta. Quando
o cliente escolher um horário oferecido, use agendar. Se houver mais de um profissional e ele não
escolher, deixe profissional_id nulo. Marque aceita_qualquer_profissional=true somente quando ele
disser “qualquer um”, “tanto faz” ou “sem preferência”. Se a pergunta atual for sobre manhã, tarde
ou noite e ele aceitar qualquer opção, marque aceita_qualquer_periodo=true. Se faltar o nome, ainda
use agendar: o sistema fará a pergunta.
Interprete respostas naturais no contexto: “tanto faz” e “sem preferência” significam aceitar uma
opção disponível; “o primeiro” pode significar o primeiro horário oferecido; “mais cedo” e “mais
tarde” se referem aos horários já apresentados; “na verdade” corrige a escolha anterior. Em dúvida,
confirme o que entendeu com uma pergunta curta, sem reiniciar a conversa.
Para pedidos, dúvidas de produtos ou intenção de compra, use abrir_catalogo para iniciar ou
gerenciar_pedido para continuar. Todo o pedido acontece nesta conversa: nunca ofereça nem envie link.
Enquanto estado_pedido.fluxo_ativo estiver presente, toda resposta relacionada ao pedido deve usar
gerenciar_pedido, confirmar_pedido ou cancelar_pedido; não abandone o fluxo com responder.
Use somente os produtos, IDs e preços informados em negocio. Em itens_pedido, extraia todos os itens
citados e suas quantidades; use adicionar, definir ou remover conforme o pedido do cliente. Quando ele
disser que terminou os itens ou quer continuar, marque avancar_pedido=true. Depois extraia, um passo de
cada vez, entrega ou retirada; CEP e endereço; pagamento; troco apenas em dinheiro; e observação.
Quando o sistema apresentar o resumo final, só use confirmar_pedido e confirmou_pedido=true se o
cliente confirmar claramente. Para desistência use cancelar_pedido. Se o telefone já localizou um
cadastro, confirme o nome; confirmou_nome=true somente quando a pessoa confirmar. Se corrigir o nome,
preencha nome_cliente e confirmou_nome=true. A consulta de CEP, frete, limites, preços e gravação são
feitas pelo sistema, nunca os invente.
Se a pessoa informar cupom ou desejar agendar a entrega, extraia cupom_codigo, data_entrega e
hora_entrega. Esses dados são opcionais e só devem ser preenchidos quando forem mencionados.
Informar um nome não equivale a confirmar o cadastro: confirmou_nome só pode ser true depois de uma
pergunta explícita do sistema sobre nome e número receber uma confirmação clara.
Use atendente quando a pessoa pedir um humano e encerrar quando pedir para finalizar. Se a pessoa pedir menu ou opções, use
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
    contexto = dict(conversa.contexto or {})
    contexto['ia_encaminhamento'] = 'fora_horario'
    conversa.contexto = contexto
    conversa.save(update_fields=[
        'ativa', 'atendimento_humano', 'etapa', 'etapa_crm', 'contexto', 'updated_at',
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
    contexto = dict(conversa.contexto or {})
    contexto['ia_encaminhamento'] = 'erro'
    conversa.contexto = contexto
    conversa.save(update_fields=[
        'ativa', 'atendimento_humano', 'etapa', 'etapa_crm', 'contexto', 'updated_at',
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


def _ids_servicos(decisao, estado=None):
    ids = []
    for valor in decisao.get('servico_ids') or []:
        if valor and valor not in ids:
            ids.append(valor)
    servico_id = decisao.get('servico_id')
    if servico_id and servico_id not in ids:
        ids.insert(0, servico_id)
    if not ids and estado:
        ids = list(estado.get('servico_ids') or [])
        if not ids and estado.get('servico_id'):
            ids = [estado['servico_id']]
    return ids


def _servicos_e_profissional(filial, estado):
    ids = _ids_servicos({}, estado)
    encontrados = {
        item.pk: item
        for item in Produto.objects.for_filial(filial).filter(
            pk__in=ids, ativo=True, agendavel=True,
        )
    }
    servicos = [encontrados[item_id] for item_id in ids if item_id in encontrados]
    if not servicos or len(servicos) != len(ids):
        return [], None, ProfissionalAgenda.objects.none()
    profissionais = ProfissionalAgenda.objects.filter(filial=filial, ativo=True)
    for servico in servicos:
        profissionais = profissionais.filter(
            servicos_vinculados__servico=servico,
            servicos_vinculados__ativo=True,
        )
    profissionais = profissionais.select_related('funcionario').distinct()
    profissional = profissionais.filter(pk=estado.get('profissional_id')).first()
    if not profissional and profissionais.count() == 1:
        profissional = profissionais.first()
    return servicos, profissional, profissionais


def _complementares(negocio, selecionados):
    selecionados = set(selecionados)
    profissionais_compativeis = None
    for item in negocio['servicos']:
        if item['id'] not in selecionados:
            continue
        ids = {profissional['id'] for profissional in item['profissionais']}
        profissionais_compativeis = (
            ids if profissionais_compativeis is None
            else profissionais_compativeis & ids
        )
    return [
        item for item in negocio['servicos']
        if item['id'] not in selecionados
        and (
            profissionais_compativeis is None
            or profissionais_compativeis & {
                profissional['id'] for profissional in item['profissionais']
            }
        )
    ][:4]


def _intencao_generica_agendamento(texto, negocio):
    valor = normalizar(texto)
    intencao = (
        'agendar' in valor
        or 'agendamento' in valor
        or 'marcar horario' in valor
        or 'marcar um horario' in valor
    )
    if not intencao:
        return False
    palavras_especificas = {
        palavra
        for item in negocio.get('servicos', [])
        for palavra in normalizar(item['nome']).split()
        if len(palavra) >= 4
    }
    informou_servico = bool(palavras_especificas & set(valor.split()))
    informou_periodo = any(item in valor.split() for item in ('manha', 'tarde', 'noite'))
    informou_data_ou_hora = bool(re.search(
        r'\b(hoje|amanha|segunda|terca|quarta|quinta|sexta|sabado|domingo|\d{1,2}[:h/]\d{0,2})\b',
        valor,
    ))
    return not (informou_servico or informou_periodo or informou_data_ou_hora)


def _resposta_pergunta_data(conversa, texto):
    valor = normalizar(texto)
    if not any(expressao in valor for expressao in (
        'que dia', 'qual dia', 'para que dia', 'pra que dia', 'qual data', 'para quando',
    )):
        return ''
    estado = dict((conversa.contexto or {}).get('ia_agenda') or {})
    if not estado.get('fluxo_ativo'):
        return ''
    try:
        dia = date.fromisoformat(estado.get('data') or '')
    except (TypeError, ValueError):
        return 'Ainda não definimos o dia. Para qual data você quer o atendimento?'
    semana = ('segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo')
    return f'Estou consultando {semana[dia.weekday()]}, {dia.strftime("%d/%m/%Y")}. Quer manter essa data?'


def _pedido_datas_disponiveis(texto):
    valor = normalizar(texto)
    expressoes = (
        'quais datas', 'qual data tem', 'que data tem', 'datas disponiveis',
        'quais dias', 'qual dia tem', 'que dia tem', 'dias disponiveis',
        'quando tem vaga', 'quando tem horario', 'quando tem disponivel',
        'primeira data', 'primeiro dia', 'proxima data', 'proximo dia',
        'qualquer data', 'qualquer dia', 'dia que tiver', 'data que tiver',
        'fim de semana', 'outra data', 'outro dia', 'mais datas',
    )
    menciona_dia = any(item in valor for item in (
        'segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo',
    ))
    menciona_vaga = any(item in valor for item in ('vaga', 'horario', 'disponivel'))
    return any(expressao in valor for expressao in expressoes) or (menciona_dia and menciona_vaga)


def _dias_semana_texto(texto):
    valor = normalizar(texto)
    if 'fim de semana' in valor:
        return [5, 6]
    nomes = (
        ('segunda', 0), ('terca', 1), ('quarta', 2), ('quinta', 3),
        ('sexta', 4), ('sabado', 5), ('domingo', 6),
    )
    return [indice for nome, indice in nomes if nome in valor]


def _periodo_texto(texto):
    valor = normalizar(texto)
    for termo, codigo in (('manha', 'manha'), ('tarde', 'tarde'), ('noite', 'noite')):
        if termo in valor:
            return codigo
    return ''


def _limpar_data_consultada(conversa):
    contexto = dict(conversa.contexto or {})
    estado = dict(contexto.get('ia_agenda') or {})
    for chave in ('data', 'horarios_oferecidos', 'datas_oferecidas'):
        estado.pop(chave, None)
    estado['fluxo_ativo'] = True
    contexto['ia_agenda'] = estado
    conversa.contexto = contexto
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO
    conversa.save(update_fields=['contexto', 'etapa_crm', 'updated_at'])
    return estado


def _periodos_disponiveis(horarios):
    encontrados = []
    for codigo, rotulo in (('manha', 'manhã'), ('tarde', 'tarde'), ('noite', 'noite')):
        if any(_periodo(timezone.localtime(item)) == codigo for item in horarios):
            encontrados.append(rotulo)
    if len(encontrados) <= 1:
        return ''.join(encontrados)
    return ', '.join(encontrados[:-1]) + ' e ' + encontrados[-1]


def _consultar_datas_disponiveis(conversa, decisao, negocio, filial, alias):
    estado = _limpar_data_consultada(conversa)
    servico_ids = _ids_servicos(decisao, estado)
    if not servico_ids:
        nomes = ', '.join(item['nome'] for item in negocio.get('servicos', [])[:8])
        if not nomes:
            return 'Ainda não há serviços disponíveis para agendamento.'
        return (
            'Consigo consultar as próximas datas, sim. '
            f'Antes, qual serviço você deseja? Temos: {nomes}.'
        )
    estado = _salvar_contexto_agenda(conversa, {
        'servico_id': servico_ids[0],
        'servico_ids': servico_ids,
        'profissional_id': decisao.get('profissional_id'),
        'periodo': decisao.get('periodo'),
        'dias_semana': decisao.get('dias_semana') or None,
        'aceita_qualquer_profissional': decisao.get('aceita_qualquer_profissional') or None,
        'aceita_qualquer_periodo': decisao.get('aceita_qualquer_periodo') or None,
    })
    with tenant_db(alias):
        servicos, profissional, profissionais = _servicos_e_profissional(filial, estado)
        if not servicos:
            return 'Não encontrei esse serviço. Qual dos serviços disponíveis você deseja?'
        candidatos = [profissional] if profissional else list(profissionais)
        if not candidatos:
            return 'Não há profissional disponível para realizar esse serviço.'
        periodo = estado.get('periodo')
        dias_semana = set(estado.get('dias_semana') or [])
        datas = []
        hoje = timezone.localdate()
        for deslocamento in range(46):
            dia = hoje + timedelta(days=deslocamento)
            if dias_semana and dia.weekday() not in dias_semana:
                continue
            horarios_dia = []
            for candidato in candidatos:
                horarios = listar_horarios(candidato, servicos, dia)
                if periodo:
                    horarios = [
                        item for item in horarios
                        if _periodo(timezone.localtime(item)) == periodo
                    ]
                horarios_dia.extend(horarios)
            if horarios_dia:
                datas.append((dia, horarios_dia))
            if len(datas) == 6:
                break

    if not datas:
        detalhe_periodo = f' {_rotulo_periodo(estado.get("periodo"))}' if estado.get('periodo') else ''
        return (
            f'Não encontrei datas disponíveis nos próximos 45 dias{detalhe_periodo}. '
            'Quer tentar outro período, serviço ou falar com um atendente?'
        )
    semana = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')
    opcoes = []
    for dia, horarios in datas:
        turnos = _periodos_disponiveis(horarios)
        complemento = f' — {turnos}' if turnos else ''
        opcoes.append(f'{semana[dia.weekday()]} {dia.strftime("%d/%m")}{complemento}')
    _salvar_contexto_agenda(conversa, {
        'datas_oferecidas': [dia.isoformat() for dia, _ in datas],
    })
    nomes_servicos = ', '.join(item.descricao for item in servicos)
    return (
        f'Considerando {nomes_servicos}, encontrei estas próximas datas: '
        + '; '.join(opcoes)
        + '. Qual delas fica melhor para você?'
    )


def _rotulo_periodo(periodo):
    return {
        'manha': 'pela manhã',
        'tarde': 'à tarde',
        'noite': 'à noite',
    }.get(periodo, '')


def _salvar_contexto_pedido(conversa, dados):
    contexto = dict(conversa.contexto or {})
    estado = dict(contexto.get('ia_pedido') or {})
    for chave, valor in dados.items():
        if valor is not None:
            estado[chave] = valor
    estado['fluxo_ativo'] = True
    contexto['ia_pedido'] = estado
    conversa.contexto = contexto
    conversa.etapa = 'ia_pedido'
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.INTERESSADO
    conversa.save(update_fields=['contexto', 'etapa', 'etapa_crm', 'updated_at'])
    return estado


def _dinheiro(valor):
    return f'R$ {Decimal(valor or 0):.2f}'.replace('.', ',')


def _listar_produtos_whatsapp(produtos):
    if not produtos:
        return 'Ainda não há produtos disponíveis para pedido.'
    grupos = {}
    for produto in produtos[:30]:
        grupos.setdefault(produto.get('categoria') or 'Outros', []).append(produto)
    linhas = ['Estes são os produtos disponíveis:']
    for categoria, itens in grupos.items():
        linhas.append(f'\n*{categoria}*')
        linhas.extend(
            f'• {item["nome"]} — {_dinheiro(item["preco"])}'
            for item in itens
        )
    linhas.append('\nDiga quais itens deseja e a quantidade de cada um.')
    return '\n'.join(linhas)


def _produtos_por_id(negocio):
    return {item['id']: item for item in negocio.get('produtos', [])}


def _resumo_carrinho(estado, negocio):
    produtos = _produtos_por_id(negocio)
    linhas = []
    subtotal = Decimal('0')
    for produto_id, quantidade in (estado.get('itens') or {}).items():
        produto = produtos.get(int(produto_id))
        if not produto or int(quantidade) <= 0:
            continue
        total = Decimal(produto['preco']) * int(quantidade)
        subtotal += total
        linhas.append(f'• {quantidade}x {produto["nome"]} — {_dinheiro(total)}')
    return linhas, subtotal


def _aplicar_itens_pedido(estado, decisao, negocio):
    produtos = _produtos_por_id(negocio)
    itens = {str(chave): int(valor) for chave, valor in (estado.get('itens') or {}).items()}
    erros = []
    for alteracao in decisao.get('itens_pedido') or []:
        produto = produtos.get(alteracao.get('produto_id'))
        if not produto:
            erros.append('Um dos produtos informados não está disponível.')
            continue
        chave = str(produto['id'])
        quantidade = max(0, int(alteracao.get('quantidade') or 0))
        operacao = alteracao.get('operacao')
        if operacao == 'remover':
            itens.pop(chave, None)
            continue
        nova_quantidade = quantidade if operacao == 'definir' else itens.get(chave, 0) + quantidade
        if nova_quantidade > int(produto['quantidade_maxima'] or 99):
            erros.append(
                f'A quantidade máxima de {produto["nome"]} é {produto["quantidade_maxima"]}.'
            )
            continue
        if nova_quantidade:
            itens[chave] = nova_quantidade
        else:
            itens.pop(chave, None)
    estado['itens'] = itens
    if decisao.get('itens_pedido'):
        estado['itens_confirmados'] = False
        estado['aguardando_confirmacao'] = False
    return erros


def _opcoes_modalidade(config):
    opcoes = []
    if config.entrega_ativa:
        opcoes.append('entrega')
    if config.retirada_ativa:
        opcoes.append('retirada na loja')
    return ' ou '.join(opcoes)


def _preencher_endereco_por_cep(estado, cep):
    try:
        endereco = CepService.consultar(cep)
    except DadosInvalidosError as exc:
        return str(exc)
    if not endereco:
        return 'Não encontrei esse CEP. Confira os números e envie novamente.'
    estado.update({
        'cep': endereco.get('cep', ''),
        'logradouro': endereco.get('endereco', ''),
        'complemento': estado.get('complemento') or endereco.get('complemento', ''),
        'bairro': endereco.get('bairro', ''),
        'cidade': endereco.get('cidade', ''),
        'uf': endereco.get('uf', ''),
    })
    return ''


def _calcular_pedido(estado, negocio, config):
    linhas, subtotal = _resumo_carrinho(estado, negocio)
    frete, a_combinar = config.calcular_frete(subtotal, estado.get('modalidade'))
    frete = Decimal(frete or 0)
    desconto = Decimal(estado.get('valor_desconto') or 0)
    return linhas, subtotal, desconto, frete, a_combinar, max(Decimal('0'), subtotal - desconto) + frete


def _resumo_pre_confirmacao(estado, negocio, config):
    linhas, subtotal, desconto, frete, a_combinar, total = _calcular_pedido(estado, negocio, config)
    modalidade = 'Entrega' if estado.get('modalidade') == 'entrega' else 'Retirada na loja'
    pagamento = dict(PedidoCatalogo.Pagamento.choices).get(estado.get('forma_pagamento'), '')
    frete_texto = 'A combinar' if a_combinar else _dinheiro(frete)
    endereco = ''
    if estado.get('modalidade') == 'entrega':
        endereco = (
            f'\n*Endereço:* {estado.get("logradouro")}, {estado.get("numero_endereco")}'
            f'{" — " + estado.get("complemento") if estado.get("complemento") else ""}, '
            f'{estado.get("bairro")}, {estado.get("cidade")}/{estado.get("uf")} — '
            f'CEP {estado.get("cep")}'
        )
        if estado.get('data_entrega') and estado.get('hora_entrega'):
            try:
                data_rotulo = datetime.strptime(estado['data_entrega'], '%Y-%m-%d').strftime('%d/%m/%Y')
            except ValueError:
                data_rotulo = estado['data_entrega']
            endereco += f'\n*Entrega agendada:* {data_rotulo} às {estado["hora_entrega"]}'
    troco = ''
    if estado.get('forma_pagamento') == 'dinheiro':
        troco = f'\n*Troco:* {estado.get("troco_para") or "Não precisa"}'
    return (
        '*Confira seu pedido*\n'
        + '\n'.join(linhas)
        + f'\n\n*Subtotal:* {_dinheiro(subtotal)}'
        + (f'\n*Cupom {estado.get("cupom_codigo")}:* - {_dinheiro(desconto)}' if desconto else '')
        + f'\n*Frete:* {frete_texto}'
        + f'\n*Total previsto:* {_dinheiro(total)}'
        + f'\n*Recebimento:* {modalidade}'
        + endereco
        + f'\n*Pagamento:* {pagamento}'
        + troco
        + f'\n*Observação:* {estado.get("observacao") or "Nenhuma"}'
        + '\n\nEstá tudo certo? Responda *sim* para confirmar ou diga o que deseja alterar.'
    )


def _criar_pedido_whatsapp(conversa, estado, negocio, filial, alias, config):
    produtos = _produtos_por_id(negocio)
    linhas, subtotal, desconto, frete, a_combinar, total = _calcular_pedido(estado, negocio, config)
    if not linhas:
        return None, 'Seu carrinho está vazio.'
    if subtotal < config.pedido_minimo:
        return None, f'O pedido mínimo é {_dinheiro(config.pedido_minimo)}.'
    endereco = {
        'cep': estado.get('cep', ''), 'logradouro': estado.get('logradouro', ''),
        'numero': estado.get('numero_endereco', ''), 'complemento': estado.get('complemento', ''),
        'bairro': estado.get('bairro', ''), 'cidade': estado.get('cidade', ''),
        'uf': estado.get('uf', ''),
    }
    troco = None
    if estado.get('forma_pagamento') == PedidoCatalogo.Pagamento.DINHEIRO and estado.get('troco_para'):
        try:
            troco = Decimal(str(estado['troco_para']).replace('R$', '').replace(' ', '').replace(',', '.'))
        except InvalidOperation:
            return None, 'Não entendi o valor do troco. Informe, por exemplo, “troco para 50”.'
    entrega_em = None
    if estado.get('data_entrega') or estado.get('hora_entrega'):
        try:
            entrega_em = timezone.make_aware(
                datetime.strptime(
                    f'{estado.get("data_entrega", "")} {estado.get("hora_entrega", "")}',
                    '%Y-%m-%d %H:%M',
                ),
                timezone.get_current_timezone(),
            )
            if entrega_em < timezone.now() + timedelta(hours=config.prazo_minimo_entrega_horas):
                raise ValueError
        except ValueError:
            return None, 'A data ou o horário da entrega não é válido. Informe outra opção.'
    with tenant_db(alias), transaction.atomic(using=alias):
        cupom = None
        if estado.get('cupom_codigo'):
            cupom = CupomCatalogo.objects.filter(
                filial=filial, codigo__iexact=estado['cupom_codigo'], ativo=True,
            ).first()
            if not cupom or cupom.motivo_indisponivel(subtotal):
                return None, 'Esse cupom não está mais disponível. Remova-o ou informe outro código.'
            desconto = cupom.calcular_desconto(subtotal)
            total = max(Decimal('0'), subtotal - desconto) + frete
        cliente = obter_ou_criar_cliente(
            filial, estado['nome_cliente'][:150], conversa.telefone,
            endereco if estado.get('modalidade') == 'entrega' else None,
        )
        pedido = PedidoCatalogo.objects.create(
            filial=filial,
            numero=f'CAT-{timezone.now():%y%m%d%H%M%S}',
            cliente=cliente,
            conversa_id=conversa.pk,
            status=PedidoCatalogo.Status.AGUARDANDO_LOJA,
            nome_cliente=estado['nome_cliente'][:150],
            telefone=conversa.telefone,
            modalidade=estado['modalidade'],
            forma_pagamento=estado['forma_pagamento'],
            troco_para=troco,
            endereco_entrega=endereco if estado.get('modalidade') == 'entrega' else {},
            entrega_em=entrega_em,
            observacao='Pedido feito pelo agente de IA no WhatsApp.' + (
                f' {estado["observacao"]}' if estado.get('observacao') else ''
            ),
            subtotal=subtotal,
            cupom=cupom,
            codigo_cupom=cupom.codigo if cupom else '',
            valor_desconto=desconto,
            valor_frete=frete,
            frete_a_combinar=a_combinar,
            total=total,
            confirmado_cliente_em=timezone.now(),
        )
        for produto_id, quantidade in estado['itens'].items():
            produto_dado = produtos[int(produto_id)]
            produto = Produto.objects.for_filial(filial).get(pk=int(produto_id))
            ItemPedidoCatalogo.objects.create(
                pedido=pedido,
                produto=produto,
                descricao=produto_dado['nome'],
                quantidade=quantidade,
                valor_unitario=Decimal(produto_dado['preco']),
                valor_total=Decimal(produto_dado['preco']) * quantidade,
            )
        notificar_novo_pedido(pedido)
        resumo = formatar_resumo(pedido, conversa.configuracao)
    return pedido, resumo


def _gerenciar_pedido(conversa, decisao, texto, negocio, filial, alias):
    with tenant_db(alias):
        config, _ = CatalogoConfiguracao.objects.get_or_create(filial=filial)
    if not config.ativo:
        return 'O catálogo desta filial está indisponível no momento.'
    if not negocio.get('produtos'):
        return 'Ainda não há produtos disponíveis para pedido.'
    estado = dict((conversa.contexto or {}).get('ia_pedido') or {})
    cliente = negocio.get('cliente') or {}
    if not estado:
        estado = {
            'itens': {},
            'nome_cliente': cliente.get('nome', ''),
            'nome_confirmado': False,
        }
        for chave in ('cep', 'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'uf'):
            if cliente.get(chave):
                estado['numero_endereco' if chave == 'numero' else chave] = cliente[chave]
        if all(estado.get(chave) for chave in (
            'cep', 'logradouro', 'numero_endereco', 'bairro', 'cidade', 'uf',
        )):
            estado['cep_consultado'] = True
    if decisao.get('nome_cliente'):
        estado['nome_cliente'] = decisao['nome_cliente'].strip()[:150]
        estado['nome_confirmado'] = bool(decisao.get('confirmou_nome'))
    elif decisao.get('confirmou_nome') and estado.get('nome_cliente'):
        estado['nome_confirmado'] = True
    erros_itens = _aplicar_itens_pedido(estado, decisao, negocio)
    if erros_itens:
        _salvar_contexto_pedido(conversa, estado)
        return ' '.join(erros_itens)
    if decisao.get('avancar_pedido') and estado.get('itens'):
        estado['itens_confirmados'] = True
    cep_anterior = estado.get('cep')
    for chave in (
        'modalidade', 'cep', 'logradouro', 'complemento', 'bairro', 'cidade', 'uf',
        'forma_pagamento', 'observacao', 'data_entrega', 'hora_entrega',
    ):
        if decisao.get(chave):
            estado[chave] = decisao[chave].strip() if isinstance(decisao[chave], str) else decisao[chave]
    if decisao.get('cupom_codigo'):
        estado['cupom_codigo'] = decisao['cupom_codigo'].strip().upper()
    if decisao.get('cep') and estado.get('cep') != cep_anterior:
        estado.pop('cep_consultado', None)
    if decisao.get('numero_endereco'):
        estado['numero_endereco'] = decisao['numero_endereco'].strip()
    if decisao.get('troco_para'):
        estado['troco_para'] = decisao['troco_para'].strip()
        estado['troco_respondido'] = True
    if decisao.get('sem_observacao'):
        estado['observacao'] = ''
        estado['observacao_respondida'] = True
    elif decisao.get('observacao'):
        estado['observacao_respondida'] = True
    valor_texto = normalizar(texto)
    if estado.get('aguardando_troco') and any(item in valor_texto for item in ('sem troco', 'nao precisa', 'nao quero')):
        estado['troco_para'] = ''
        estado['troco_respondido'] = True
    if estado.get('aguardando_observacao') and valor_texto in {'nao', 'nenhuma', 'sem observacao', 'nao tenho'}:
        estado['observacao'] = ''
        estado['observacao_respondida'] = True

    if not estado.get('nome_cliente'):
        _salvar_contexto_pedido(conversa, estado)
        return 'Antes de começarmos, qual é o seu nome?'
    if not estado.get('nome_confirmado'):
        _salvar_contexto_pedido(conversa, estado)
        if cliente:
            return f'Encontrei seu cadastro como *{estado["nome_cliente"]}*. Esse nome está correto?'
        return (
            f'Vou cadastrar *{estado["nome_cliente"]}* usando este WhatsApp '
            f'(*{conversa.telefone}*). Os dados estão corretos?'
        )
    if not estado.get('itens'):
        _salvar_contexto_pedido(conversa, estado)
        return _listar_produtos_whatsapp(negocio['produtos'])
    if not estado.get('itens_confirmados'):
        _salvar_contexto_pedido(conversa, estado)
        linhas, subtotal = _resumo_carrinho(estado, negocio)
        return (
            'Seu carrinho está assim:\n' + '\n'.join(linhas)
            + f'\n*Subtotal:* {_dinheiro(subtotal)}'
            + '\n\nDeseja adicionar ou remover algum item? Se terminou, responda “continuar”.'
        )
    _linhas, subtotal_atual = _resumo_carrinho(estado, negocio)
    if subtotal_atual < config.pedido_minimo:
        estado['itens_confirmados'] = False
        _salvar_contexto_pedido(conversa, estado)
        return (
            f'O pedido mínimo é {_dinheiro(config.pedido_minimo)} e seu carrinho está em '
            f'{_dinheiro(subtotal_atual)}. O que deseja adicionar?'
        )
    if estado.get('cupom_codigo'):
        with tenant_db(alias):
            cupom = CupomCatalogo.objects.filter(
                filial=filial, codigo__iexact=estado['cupom_codigo'], ativo=True,
            ).first()
            motivo = cupom.motivo_indisponivel(subtotal_atual) if cupom else 'Cupom não encontrado.'
            if motivo:
                estado.pop('cupom_codigo', None)
                estado.pop('valor_desconto', None)
                _salvar_contexto_pedido(conversa, estado)
                return f'Não consegui aplicar o cupom: {motivo} Você tem outro código?'
            estado['valor_desconto'] = str(cupom.calcular_desconto(subtotal_atual))
    if not estado.get('modalidade'):
        _salvar_contexto_pedido(conversa, estado)
        return f'Como deseja receber: {_opcoes_modalidade(config)}?'
    if estado['modalidade'] == 'entrega' and not config.entrega_ativa:
        estado.pop('modalidade', None)
        _salvar_contexto_pedido(conversa, estado)
        return f'Entrega não está disponível. A opção atual é: {_opcoes_modalidade(config)}.'
    if estado['modalidade'] == 'retirada' and not config.retirada_ativa:
        estado.pop('modalidade', None)
        _salvar_contexto_pedido(conversa, estado)
        return f'Retirada não está disponível. A opção atual é: {_opcoes_modalidade(config)}.'
    if estado['modalidade'] == 'entrega':
        if estado.get('cep') and not estado.get('cep_consultado'):
            erro_cep = _preencher_endereco_por_cep(estado, estado['cep'])
            if erro_cep:
                estado.pop('cep', None)
                _salvar_contexto_pedido(conversa, estado)
                return erro_cep
            estado['cep_consultado'] = True
        if not estado.get('cep'):
            _salvar_contexto_pedido(conversa, estado)
            return 'Qual é o CEP da entrega? Envie os 8 números.'
        faltantes = [
            rotulo for campo, rotulo in (
                ('logradouro', 'rua/avenida'), ('numero_endereco', 'número'),
                ('bairro', 'bairro'), ('cidade', 'cidade'), ('uf', 'UF'),
            ) if not estado.get(campo)
        ]
        if faltantes:
            _salvar_contexto_pedido(conversa, estado)
            preenchido = ', '.join(item for item in (
                estado.get('logradouro'), estado.get('bairro'),
                f'{estado.get("cidade")}/{estado.get("uf")}' if estado.get('cidade') else '',
            ) if item)
            prefixo = f'Localizei *{preenchido}*. ' if preenchido else ''
            return prefixo + f'Agora informe: {", ".join(faltantes)}.'
        if bool(estado.get('data_entrega')) != bool(estado.get('hora_entrega')):
            _salvar_contexto_pedido(conversa, estado)
            faltante = 'o horário' if estado.get('data_entrega') else 'a data'
            return f'Para agendar a entrega, informe também {faltante}.'
    if not estado.get('forma_pagamento'):
        _salvar_contexto_pedido(conversa, estado)
        return 'Qual será a forma de pagamento: Pix, dinheiro ou cartão?'
    if estado['forma_pagamento'] == 'dinheiro' and not estado.get('troco_respondido'):
        estado['aguardando_troco'] = True
        _salvar_contexto_pedido(conversa, estado)
        return 'Vai precisar de troco? Se sim, troco para qual valor?'
    if not estado.get('observacao_respondida'):
        estado['aguardando_observacao'] = True
        _salvar_contexto_pedido(conversa, estado)
        return 'Deseja adicionar alguma observação ao pedido? Se não, responda “sem observação”.'
    estado['aguardando_confirmacao'] = True
    _salvar_contexto_pedido(conversa, estado)
    if decisao.get('confirmou_pedido') or decisao.get('acao') == 'confirmar_pedido':
        pedido, resposta = _criar_pedido_whatsapp(conversa, estado, negocio, filial, alias, config)
        if not pedido:
            return resposta
        contexto = dict(conversa.contexto or {})
        contexto.pop('ia_pedido', None)
        contexto['catalogo_pedido_id'] = pedido.pk
        contexto['catalogo_db_alias'] = alias
        conversa.contexto = contexto
        conversa.etapa = 'encerrada'
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.CONCLUIDO
        conversa.ativa = False
        conversa.atendimento_humano = False
        conversa.save(update_fields=[
            'contexto', 'etapa', 'etapa_crm', 'ativa', 'atendimento_humano', 'updated_at',
        ])
        return resposta
    return _resumo_pre_confirmacao(estado, negocio, config)


def _cancelar_pedido_ia(conversa):
    contexto = dict(conversa.contexto or {})
    contexto.pop('ia_pedido', None)
    conversa.contexto = contexto
    conversa.etapa = 'ia_atendimento'
    conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
    return 'Tudo bem, cancelei esse pedido. Posso ajudar com outra coisa?'


def _confirmacao_natural_pedido(texto, *, aceitar_nao=False):
    """Reconhece confirmações comuns sem depender da decisão probabilística da IA."""
    valor = re.sub(r'[^a-z0-9 ]+', ' ', normalizar(texto))
    valor = ' '.join(valor.split())
    if not valor:
        return False

    confirmacoes_sem_mais_itens = (
        'nao quero mais nada', 'nao precisa adicionar mais',
        'nao vou adicionar mais', 'nao quero adicionar mais',
        'nao precisa de mais nada', 'sem mais itens',
        'vai ser so isso', 'vai ser so esses', 'vai ser so estes',
        'so os dois itens', 'somente os dois itens',
    )
    if any(frase in valor for frase in confirmacoes_sem_mais_itens):
        return True
    if aceitar_nao and valor in {'nao', 'nao vou querer mais', 'nao quero mais'}:
        return True
    if 'nao' in valor.split():
        return False

    pedidos_de_alteracao = (
        'nao esta certo', 'nao ta certo', 'esta errado', 'ta errado',
        'quero alterar', 'quero corrigir', 'quero trocar',
        'adicionar', 'acrescentar', 'incluir mais', 'remover',
        'retirar um item', 'tirar um item', 'mudar a quantidade',
    )
    if any(frase in valor for frase in pedidos_de_alteracao):
        return False
    if re.search(r'\b(adicion|acrescent|inclu|remov|retir|tir|troc|alter|mud|corrig)\w*', valor):
        return False

    confirmacoes = (
        'sim', 'ok', 'okay', 'certo', 'correto', 'confirmo', 'confirmado',
        'isso', 'isso mesmo', 'esta certo', 'ta certo', 'tudo certo',
        'esta tudo certo', 'ta tudo certo',
        'pode continuar', 'pode seguir', 'pode prosseguir', 'pode avancar',
        'pode fechar', 'fechar pedido', 'finalizar', 'finaliza', 'concluir',
        'prosseguir', 'avancar', 'pronto', 'beleza', 'so isso',
        'somente isso', 'esses mesmos', 'estes mesmos', 'pode ser',
        'pode', 'pode ir', 'pode confirmar', 'pode concluir', 'segue',
        'fechou', 'perfeito', 'combinado', 'de acordo', 'aprovado',
        'manda ver', 'e isso', 'e isso ai', 'vai ser isso mesmo',
        'so esses', 'so estes', 'sao so esses', 'sao so estes',
    )
    if valor.startswith('continu'):
        return True
    return valor in confirmacoes or any(
        valor.startswith(f'{confirmacao} ') for confirmacao in confirmacoes
    )


def _decisao_confirmacao_pedido(estado):
    if estado.get('nome_cliente') and not estado.get('nome_confirmado'):
        return {'acao': 'gerenciar_pedido', 'confirmou_nome': True}
    if estado.get('aguardando_confirmacao'):
        return {'acao': 'confirmar_pedido', 'confirmou_pedido': True}
    if estado.get('itens') and not estado.get('itens_confirmados'):
        return {'acao': 'gerenciar_pedido', 'avancar_pedido': True}
    return None


def _consultar_agenda(conversa, decisao, negocio, filial, alias):
    estado_anterior = dict((conversa.contexto or {}).get('ia_agenda') or {})
    servico_ids = _ids_servicos(decisao, estado_anterior)
    if estado_anterior.get('aguardando_complementos'):
        anteriores = _ids_servicos({}, estado_anterior)
        servico_ids = anteriores + [
            item_id for item_id in servico_ids if item_id not in anteriores
        ]
    confirmou_servicos = bool(decisao.get('confirmou_servicos'))
    if (
        estado_anterior.get('aguardando_complementos')
        and len(servico_ids) > len(_ids_servicos({}, estado_anterior))
    ):
        confirmou_servicos = True
    estado = _salvar_contexto_agenda(conversa, {
        'fluxo_ativo': True,
        'servico_id': servico_ids[0] if servico_ids else None,
        'servico_ids': servico_ids or None,
        'complementos_confirmados': confirmou_servicos or None,
        'profissional_id': decisao.get('profissional_id'),
        'aceita_qualquer_profissional': decisao.get('aceita_qualquer_profissional') or None,
        'aceita_qualquer_periodo': decisao.get('aceita_qualquer_periodo') or None,
        'data': decisao.get('data'),
        'periodo': decisao.get('periodo'),
    })
    if not servico_ids:
        if not negocio['servicos']:
            return 'Ainda não há serviços disponíveis para agendamento.'
        if not estado.get('periodo') and not estado.get('aceita_qualquer_periodo'):
            return 'Qual período você prefere: manhã, tarde ou noite?'
        nomes = ', '.join(item['nome'] for item in negocio['servicos'][:8])
        return f'Qual serviço você quer agendar? Temos: {nomes}.'
    if not estado.get('complementos_confirmados'):
        adicionais = _complementares(negocio, servico_ids)
        if adicionais:
            _salvar_contexto_agenda(conversa, {'aguardando_complementos': True})
            escolhidos = [
                item['nome'] for item in negocio['servicos'] if item['id'] in servico_ids
            ]
            opcoes = ', '.join(item['nome'] for item in adicionais)
            return (
                f'Certo, vou incluir {", ".join(escolhidos)}. '
                f'Deseja adicionar também {opcoes}? Você pode escolher ou responder “não”.'
            )
        estado = _salvar_contexto_agenda(conversa, {
            'complementos_confirmados': True,
            'aguardando_complementos': False,
        })
    try:
        dia = date.fromisoformat(estado.get('data') or '')
        if not timezone.localdate() <= dia <= timezone.localdate() + timedelta(days=60):
            raise ValueError
    except ValueError:
        return 'Para qual dia você quer o atendimento? Pode responder, por exemplo, “amanhã”.'

    with tenant_db(alias):
        servicos, profissional, profissionais = _servicos_e_profissional(filial, estado)
        if not servicos:
            return 'Não encontrei esse serviço. Qual dos serviços disponíveis você deseja?'
        if not profissional and estado.get('aceita_qualquer_profissional'):
            periodo_escolhido = estado.get('periodo')
            candidatos = []
            for candidato in profissionais:
                opcoes = listar_horarios(candidato, servicos, dia)
                if periodo_escolhido:
                    opcoes = [
                        item for item in opcoes
                        if _periodo(timezone.localtime(item)) == periodo_escolhido
                    ]
                if opcoes:
                    candidatos.append((opcoes[0], candidato))
            if candidatos:
                profissional = min(candidatos, key=lambda item: item[0])[1]
        if not profissional:
            nomes_profissionais = [item.funcionario.nome for item in profissionais]
            if nomes_profissionais:
                return 'Com qual profissional você prefere? ' + ', '.join(nomes_profissionais) + '.'
            return 'Não há profissional disponível para esse serviço.'
        horarios = listar_horarios(profissional, servicos, dia)

    if not horarios:
        return 'Não encontrei horários disponíveis nesse dia. Quer tentar outra data?'
    periodo = estado.get('periodo')
    if not periodo and estado.get('aceita_qualquer_periodo') and horarios:
        periodo = _periodo(timezone.localtime(horarios[0]))
        estado = _salvar_contexto_agenda(conversa, {'periodo': periodo})
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
    semana = ('segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo')
    nomes_servicos = ', '.join(item.descricao for item in servicos)
    return (
        f'Para {semana[dia.weekday()]}, {dia.strftime("%d/%m")}, '
        f'{_rotulo_periodo(periodo)}, os horários disponíveis para {nomes_servicos} são: '
        + ', '.join(filtrados[:12])
        + '. Qual você prefere?'
    )


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
    estado_anterior = dict((conversa.contexto or {}).get('ia_agenda') or {})
    servico_ids = _ids_servicos(decisao, estado_anterior)
    anteriores = _ids_servicos({}, estado_anterior)
    servico_ids = anteriores + [
        item_id for item_id in servico_ids if item_id not in anteriores
    ]
    estado = _salvar_contexto_agenda(conversa, {
        'servico_id': servico_ids[0] if servico_ids else None,
        'servico_ids': servico_ids or None,
        'complementos_confirmados': True,
        'profissional_id': decisao.get('profissional_id'),
        'data': decisao.get('data'),
        'periodo': decisao.get('periodo'),
        'horario': decisao.get('horario'),
        'nome_cliente': decisao.get('nome_cliente'),
        'nome_confirmado': decisao.get('confirmou_nome') or None,
    })
    with tenant_db(alias):
        cliente_existente = localizar_cliente(filial, conversa.telefone)
    nome = (
        estado.get('nome_cliente')
        or (cliente_existente.nome_display if cliente_existente else '')
        or (conversa.cliente.nome_display if conversa.cliente_id else '')
    ).strip()
    if not nome or nome.casefold() in {'cliente', 'whatsapp'}:
        return 'Antes de confirmar, qual é o seu nome?'
    if decisao.get('nome_cliente') and decisao.get('confirmou_nome'):
        estado = _salvar_contexto_agenda(conversa, {
            'nome_cliente': nome,
            'nome_confirmado': True,
        })
    elif not estado.get('nome_confirmado'):
        _salvar_contexto_agenda(conversa, {'nome_cliente': nome})
        if cliente_existente:
            return f'Encontrei seu cadastro como *{nome}*. Posso confirmar o agendamento nesse nome?'
        return (
            f'Vou cadastrar *{nome}* usando este WhatsApp (*{conversa.telefone}*). '
            'Os dados estão corretos?'
        )
    try:
        dia = date.fromisoformat(estado.get('data') or '')
        hora = datetime.strptime(estado.get('horario') or '', '%H:%M').time()
    except ValueError:
        return _consultar_agenda(conversa, decisao, negocio, filial, alias)

    with tenant_db(alias):
        servicos, profissional, _ = _servicos_e_profissional(filial, estado)
        if not servicos or not profissional:
            return _consultar_agenda(conversa, decisao, negocio, filial, alias)
        disponiveis = listar_horarios(profissional, servicos, dia)
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
                servicos=servicos,
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
    conversa.etapa = 'encerrada'
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDADO
    conversa.ativa = False
    conversa.atendimento_humano = False
    conversa.agendamento_confirmado_em = timezone.now()
    conversa.cliente_id = getattr(cliente, 'pk', None) if alias == 'default' else conversa.cliente_id
    conversa.save(update_fields=[
        'contexto', 'etapa', 'etapa_crm', 'ativa', 'atendimento_humano',
        'agendamento_confirmado_em', 'cliente_id', 'updated_at',
    ])
    return texto_confirmacao


def processar_com_ia(conversa, texto, mensagem):
    config = conversa.configuracao
    if not config.ia_ativa:
        return None
    saudacao_simples = normalizar(texto) in {
        'oi', 'ola', 'bom dia', 'boa tarde', 'boa noite', 'e ai', 'opa',
    }
    if (
        conversa.atendimento_humano
        and conversa.etapa == 'aguardando_atendimento_humano'
        and saudacao_simples
        and _dentro_horario_ia(config)
    ):
        conversa.atendimento_humano = False
        conversa.etapa = 'ia_atendimento'
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.NOVA
        conversa.save(update_fields=[
            'atendimento_humano', 'etapa', 'etapa_crm', 'updated_at',
        ])
    if conversa.atendimento_humano:
        return None
    # Confirmações transacionais continuam determinísticas: a IA não decide
    # nem inventa dados em etapas que alteram agenda ou pedido.
    if conversa.etapa in ETAPAS_DETERMINISTICAS:
        return None
    aviso_fora_horario = resposta_fora_horario(conversa)
    if aviso_fora_horario:
        return aviso_fora_horario
    if saudacao_simples:
        contexto = dict(conversa.contexto or {})
        contexto.pop('ia_agenda', None)
        contexto.pop('ia_encaminhamento', None)
        conversa.contexto = contexto
        conversa.ativa = True
        conversa.etapa = 'ia_atendimento'
        conversa.save(update_fields=['contexto', 'ativa', 'etapa', 'updated_at'])
        return 'Olá! Como posso ajudar você hoje?'
    _marcar_atendimento_ia(conversa)
    filial, alias, negocio = _catalogo_negocio(conversa)
    estado_pedido = dict((conversa.contexto or {}).get('ia_pedido') or {})
    confirmando_carrinho = bool(
        estado_pedido.get('itens') and not estado_pedido.get('itens_confirmados')
    )
    if (
        filial
        and estado_pedido
        and _confirmacao_natural_pedido(texto, aceitar_nao=confirmando_carrinho)
    ):
        decisao_confirmacao = _decisao_confirmacao_pedido(estado_pedido)
        if decisao_confirmacao:
            return _gerenciar_pedido(
                conversa, decisao_confirmacao, texto, negocio, filial, alias,
            )
    if filial and _pedido_datas_disponiveis(texto):
        return _consultar_datas_disponiveis(conversa, {
            'servico_id': None,
            'servico_ids': [],
            'confirmou_servicos': False,
            'profissional_id': None,
            'periodo': _periodo_texto(texto),
            'aceita_qualquer_profissional': False,
            'aceita_qualquer_periodo': False,
            'dias_semana': _dias_semana_texto(texto),
        }, negocio, filial, alias)
    resposta_data = _resposta_pergunta_data(conversa, texto)
    if resposta_data:
        return resposta_data
    if filial and _intencao_generica_agendamento(texto, negocio):
        return _consultar_agenda(conversa, {
            'servico_id': None,
            'servico_ids': [],
            'confirmou_servicos': False,
            'profissional_id': None,
            'aceita_qualquer_profissional': False,
            'aceita_qualquer_periodo': False,
            'data': '',
            'periodo': '',
        }, negocio, filial, alias)
    entrada = json.dumps({
        'mensagem_atual': texto,
        'cliente_conhecido': conversa.cliente.nome_display if conversa.cliente_id else '',
        'estado_agenda': (conversa.contexto or {}).get('ia_agenda') or {},
        'estado_pedido': (conversa.contexto or {}).get('ia_pedido') or {},
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
    if acao == 'consultar_datas' and filial:
        return _consultar_datas_disponiveis(conversa, decisao, negocio, filial, alias)
    if acao == 'consultar_agenda' and filial:
        return _consultar_agenda(conversa, decisao, negocio, filial, alias)
    if acao == 'agendar' and filial:
        return _agendar(conversa, decisao, negocio, filial, alias)
    if acao in {'abrir_catalogo', 'gerenciar_pedido', 'confirmar_pedido'} and filial:
        return _gerenciar_pedido(conversa, decisao, texto, negocio, filial, alias)
    if acao == 'cancelar_pedido':
        return _cancelar_pedido_ia(conversa)
    if acao == 'abrir_agenda' and filial:
        return _consultar_agenda(conversa, decisao, negocio, filial, alias)
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
