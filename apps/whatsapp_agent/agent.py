import re
import unicodedata
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.utils import timezone

from apps.agenda.models import Agendamento, ProfissionalAgenda
from apps.agenda.services import criar_agendamento, listar_horarios
from apps.cadastros.models import Cliente
from apps.produtos.models import Produto


SIM = {'sim', 's', 'ok', 'confirmo', 'confirmar', 'pode', 'isso', 'correto'}
NAO = {'nao', 'n', 'cancelar', 'cancela'}
HUMANO = {'atendente', 'humano', 'pessoa', 'falar com atendente', 'falar com uma pessoa'}
SAUDACOES = {
    'oi', 'ola', 'olá', 'bom dia', 'boa tarde', 'boa noite',
    'e ai', 'e aí', 'opa', 'menu',
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
    return next((item for item in candidatos if somente_digitos(item.celular or item.telefone).endswith(sufixo)), None)


def _servicos(filial):
    return list(
        Produto.objects.for_filial(filial).filter(
            ativo=True, agendavel=True, tipo_produto=Produto.TipoProduto.SERVICO,
        ).order_by('descricao')[:20]
    )


def _formatar_moeda(valor):
    return f'{valor:.2f}'.replace('.', ',')


def _menu_servicos(configuracao):
    servicos = _servicos(configuracao.filial)
    if not servicos:
        return 'Ainda não há serviços disponíveis para agendamento. Digite *atendente* para falar com uma pessoa.'
    itens = '\n'.join(
        f'{indice}. {item.descricao} — R$ {_formatar_moeda(item.preco_venda)}'
        for indice, item in enumerate(servicos, 1)
    )
    return f'{configuracao.mensagem_saudacao}\n\nEscolha um ou mais serviços pelo número:\n{itens}\n\nExemplo: *1, 2*'


def _selecionar_servicos(filial, texto):
    servicos = _servicos(filial)
    indices = []
    for valor in re.findall(r'\d+', texto):
        indice = int(valor) - 1
        if 0 <= indice < len(servicos) and indice not in indices:
            indices.append(indice)
    if indices:
        return [servicos[indice] for indice in indices]
    texto_normalizado = normalizar(texto)
    encontrados = [item for item in servicos if normalizar(item.descricao) in texto_normalizado]
    return encontrados


def _profissionais(filial, servico_ids):
    return list(
        ProfissionalAgenda.objects.for_filial(filial).filter(
            ativo=True, servicos_vinculados__ativo=True,
            servicos_vinculados__servico_id__in=servico_ids,
        ).annotate(
            quantidade_servicos=Count('servicos_vinculados__servico_id', distinct=True),
        ).filter(quantidade_servicos=len(set(servico_ids))).select_related('funcionario')
    )


def _interpretar_data(texto):
    hoje = timezone.localdate()
    valor = normalizar(texto)
    if 'depois de amanha' in valor:
        return hoje + timedelta(days=2)
    if 'amanha' in valor:
        return hoje + timedelta(days=1)
    if valor == 'hoje' or 'para hoje' in valor:
        return hoje
    encontrado = re.search(r'\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b', valor)
    if encontrado:
        dia, mes = int(encontrado.group(1)), int(encontrado.group(2))
        ano = int(encontrado.group(3)) if encontrado.group(3) else hoje.year
        if ano < 100:
            ano += 2000
        try:
            resultado = date(ano, mes, dia)
            if not encontrado.group(3) and resultado < hoje:
                resultado = date(ano + 1, mes, dia)
            return resultado
        except ValueError:
            return None
    dias = {
        'segunda': 0, 'terca': 1, 'quarta': 2, 'quinta': 3,
        'sexta': 4, 'sabado': 5, 'domingo': 6,
    }
    for nome, numero in dias.items():
        if nome in valor:
            distancia = (numero - hoje.weekday()) % 7
            return hoje + timedelta(days=distancia or 7)
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _interpretar_indice(texto, quantidade):
    encontrado = re.search(r'\d+', texto)
    if not encontrado:
        return None
    indice = int(encontrado.group()) - 1
    return indice if 0 <= indice < quantidade else None


def _resumo_confirmacao(conversa):
    contexto = conversa.contexto
    servicos = list(Produto.objects.filter(pk__in=contexto['servicos']).order_by('descricao'))
    profissional = ProfissionalAgenda.objects.select_related('funcionario').get(pk=contexto['profissional'])
    inicio = datetime.fromisoformat(contexto['inicio'])
    if timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio)
    nomes = ', '.join(item.descricao for item in servicos)
    return (
        f'Confirma o agendamento de *{nomes}* com *{profissional}* para '
        f'*{timezone.localtime(inicio):%d/%m às %H:%M}*, em nome de *{contexto["nome"]}*?\n\n'
        'Responda *sim* para confirmar ou *não* para recomeçar.'
    )


def _salvar_cliente(conversa):
    nome = conversa.contexto['nome'].strip()[:150]
    cliente = conversa.cliente or localizar_cliente(conversa.filial, conversa.telefone)
    if cliente:
        if normalizar(cliente.nome_display) != normalizar(nome):
            cliente.razao_social = nome
            cliente.save(update_fields=['razao_social', 'updated_at'])
    else:
        cliente = Cliente.objects.create(
            filial=conversa.filial, tipo_pessoa='F', razao_social=nome,
            celular=conversa.telefone, consumidor_final=True,
        )
    conversa.cliente = cliente
    return cliente


def reiniciar(conversa):
    conversa.etapa = 'aguardando_servico'
    conversa.contexto = {}
    conversa.atendimento_humano = False
    conversa.save(update_fields=['etapa', 'contexto', 'atendimento_humano', 'updated_at'])
    return _menu_servicos(conversa.configuracao)


def processar_mensagem(conversa, texto):
    valor = normalizar(texto)
    if any(comando == valor or comando in valor for comando in HUMANO):
        conversa.atendimento_humano = True
        conversa.save(update_fields=['atendimento_humano', 'updated_at'])
        return conversa.configuracao.mensagem_transferencia
    if conversa.atendimento_humano:
        return None
    if valor in {'menu', 'inicio', 'recomecar', 'reiniciar', 'cancelar'}:
        return reiniciar(conversa)
    if conversa.etapa == 'inicio':
        conversa.etapa = 'aguardando_servico'
        conversa.save(update_fields=['etapa', 'updated_at'])
        return _menu_servicos(conversa.configuracao)

    if conversa.etapa == 'aguardando_servico':
        if valor in SAUDACOES:
            return _menu_servicos(conversa.configuracao)
        servicos = _selecionar_servicos(conversa.filial, texto)
        if not servicos:
            return 'Não consegui identificar o serviço. Responda com o número mostrado no menu ou digite *menu*.'
        profissionais = _profissionais(conversa.filial, [item.pk for item in servicos])
        if not profissionais:
            return 'Nenhum profissional está configurado para essa combinação. Digite *menu* ou *atendente*.'
        conversa.contexto = {'servicos': [item.pk for item in servicos]}
        if len(profissionais) == 1:
            conversa.contexto['profissional'] = profissionais[0].pk
            conversa.etapa = 'aguardando_data'
            resposta = f'Ótimo! O atendimento será com *{profissionais[0]}*. Para qual dia você quer agendar?'
        else:
            conversa.contexto['profissionais'] = [item.pk for item in profissionais]
            conversa.etapa = 'aguardando_profissional'
            lista = '\n'.join(f'{indice}. {item}' for indice, item in enumerate(profissionais, 1))
            resposta = f'Escolha o profissional:\n{lista}'
        conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
        return resposta

    if conversa.etapa == 'aguardando_profissional':
        ids = conversa.contexto.get('profissionais', [])
        indice = _interpretar_indice(texto, len(ids))
        if indice is None:
            return 'Responda com o número do profissional desejado.'
        profissional = ProfissionalAgenda.objects.select_related('funcionario').get(pk=ids[indice])
        conversa.contexto['profissional'] = profissional.pk
        conversa.etapa = 'aguardando_data'
        conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
        return f'Perfeito, com *{profissional}*. Para qual dia você quer agendar? Exemplo: amanhã ou 15/10.'

    if conversa.etapa == 'aguardando_data':
        data_escolhida = _interpretar_data(texto)
        if not data_escolhida or data_escolhida < timezone.localdate():
            return 'Não entendi a data. Envie, por exemplo, *amanhã* ou *15/10*.'
        profissional = ProfissionalAgenda.objects.get(pk=conversa.contexto['profissional'])
        servicos = Produto.objects.filter(pk__in=conversa.contexto['servicos'])
        horarios = listar_horarios(profissional, servicos, data_escolhida)[:12]
        if not horarios:
            return f'Não encontrei horário em {data_escolhida:%d/%m}. Envie outra data.'
        conversa.contexto['data'] = data_escolhida.isoformat()
        conversa.contexto['horarios'] = [item.isoformat() for item in horarios]
        conversa.etapa = 'aguardando_horario'
        conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
        lista = '\n'.join(f'{indice}. {timezone.localtime(item):%H:%M}' for indice, item in enumerate(horarios, 1))
        return f'Horários disponíveis em *{data_escolhida:%d/%m}*:\n{lista}\n\nEscolha pelo número.'

    if conversa.etapa == 'aguardando_horario':
        horarios = conversa.contexto.get('horarios', [])
        hora = re.search(r'\b(\d{1,2}):(\d{2})\b', texto)
        indice = next(
            (i for i, item in enumerate(horarios) if item[11:16] == hora.group(0).zfill(5)),
            None,
        ) if hora else _interpretar_indice(texto, len(horarios))
        if indice is None:
            return 'Escolha um dos horários pelo número da lista.'
        conversa.contexto['inicio'] = horarios[indice]
        if conversa.cliente:
            conversa.etapa = 'confirmando_nome_existente'
            conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
            return f'Encontrei o cadastro em nome de *{conversa.cliente.nome_display}*. Está correto? Responda *sim* ou envie o nome correto.'
        conversa.etapa = 'aguardando_nome'
        conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
        return f'Para confirmar, qual é o seu nome? O número final *{conversa.telefone[-4:]}* será usado no cadastro.'

    if conversa.etapa == 'confirmando_nome_existente':
        if valor in SIM:
            conversa.contexto['nome'] = conversa.cliente.nome_display
        elif valor in {'nao', 'n'}:
            conversa.etapa = 'aguardando_nome'
            conversa.save(update_fields=['etapa', 'updated_at'])
            return 'Tudo bem. Qual é o nome correto?'
        else:
            conversa.contexto['nome'] = texto.strip()[:150]
        conversa.etapa = 'confirmacao'
        conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
        return _resumo_confirmacao(conversa)

    if conversa.etapa == 'aguardando_nome':
        nome = re.sub(r'^(meu nome (e|é)|sou|e|é)\s+', '', texto.strip(), flags=re.I).strip()
        if len(nome) < 2:
            return 'Por favor, informe seu nome para concluir o cadastro.'
        conversa.contexto['nome'] = nome[:150]
        conversa.etapa = 'confirmacao'
        conversa.save(update_fields=['contexto', 'etapa', 'updated_at'])
        return _resumo_confirmacao(conversa)

    if conversa.etapa == 'confirmacao':
        if valor in NAO:
            return reiniciar(conversa)
        if valor not in SIM:
            return 'Responda *sim* para confirmar ou *não* para recomeçar.'
        cliente = _salvar_cliente(conversa)
        profissional = ProfissionalAgenda.objects.get(pk=conversa.contexto['profissional'])
        servicos = list(Produto.objects.filter(pk__in=conversa.contexto['servicos']))
        inicio = datetime.fromisoformat(conversa.contexto['inicio'])
        try:
            agendamento = criar_agendamento(
                filial=conversa.filial, profissional=profissional, servicos=servicos,
                inicio=inicio, pessoa_atendida_nome=conversa.contexto['nome'],
                cliente=cliente, telefone=conversa.telefone,
                origem=Agendamento.Origem.WHATSAPP_NAO_OFICIAL,
            )
        except ValidationError:
            conversa.etapa = 'aguardando_data'
            conversa.contexto.pop('horarios', None)
            conversa.contexto.pop('inicio', None)
            conversa.save(update_fields=['cliente', 'etapa', 'contexto', 'updated_at'])
            return 'Esse horário acabou de ficar indisponível. Envie outra data para eu consultar novamente.'
        conversa.etapa = 'inicio'
        conversa.contexto = {}
        conversa.save(update_fields=['cliente', 'etapa', 'contexto', 'updated_at'])
        inicio_local = timezone.localtime(agendamento.inicio)
        return f'Agendamento confirmado! ✅\n*{inicio_local:%d/%m às %H:%M}* com *{agendamento.profissional}*.\nAté lá!'

    return reiniciar(conversa)
