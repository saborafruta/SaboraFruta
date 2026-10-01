from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic

from .models import Agendamento, AgendamentoItem, BloqueioAgenda, JornadaTrabalho


STATUS_OCUPAM_HORARIO = [
    Agendamento.Status.PENDENTE,
    Agendamento.Status.CONFIRMADO,
    Agendamento.Status.CHEGOU,
    Agendamento.Status.EM_ATENDIMENTO,
]


def resumo_servicos(servicos, profissional=None):
    servicos = list(servicos)
    if not servicos:
        raise ValidationError('Selecione pelo menos um serviço.')
    if any(not item.agendavel or not item.duracao_servico_minutos for item in servicos):
        raise ValidationError('Todos os serviços precisam estar disponíveis para agendamento.')
    if profissional:
        permitidos = set(profissional.servicos_vinculados.filter(ativo=True).values_list('servico_id', flat=True))
        if any(item.pk not in permitidos for item in servicos):
            raise ValidationError('O profissional não realiza um dos serviços selecionados.')
    duracao = sum(item.duracao_servico_minutos for item in servicos)
    intervalo = max([profissional.intervalo_padrao_minutos if profissional else 0] + [item.intervalo_apos_servico_minutos for item in servicos])
    valor = sum(item.preco_venda for item in servicos)
    return duracao, intervalo, valor


def _localizar(data, hora):
    return timezone.make_aware(datetime.combine(data, hora), timezone.get_current_timezone())


def horario_disponivel(profissional, inicio, duracao_minutos, intervalo_minutos=0, ignorar_agendamento=None):
    if timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio, timezone.get_current_timezone())
    if inicio <= timezone.now():
        return False
    fim = inicio + timedelta(minutes=duracao_minutos)
    fim_com_intervalo = fim + timedelta(minutes=intervalo_minutos)
    jornada = JornadaTrabalho.objects.filter(
        profissional=profissional, filial=profissional.filial,
        dia_semana=timezone.localtime(inicio).weekday(), ativo=True,
    ).first()
    if not jornada:
        return False
    inicio_local = timezone.localtime(inicio)
    fim_local = timezone.localtime(fim_com_intervalo)
    jornada_inicio = _localizar(inicio_local.date(), jornada.inicio)
    jornada_fim = _localizar(inicio_local.date(), jornada.fim)
    if inicio < jornada_inicio or fim_com_intervalo > jornada_fim:
        return False
    if jornada.pausa_inicio:
        pausa_inicio = _localizar(inicio_local.date(), jornada.pausa_inicio)
        pausa_fim = _localizar(inicio_local.date(), jornada.pausa_fim)
        if inicio < pausa_fim and fim_com_intervalo > pausa_inicio:
            return False
    bloqueado = BloqueioAgenda.objects.filter(
        filial=profissional.filial, ativo=True, inicio__lt=fim_com_intervalo, fim__gt=inicio,
    ).filter(Q(profissional=profissional) | Q(profissional__isnull=True)).exists()
    if bloqueado:
        return False
    ocupados = Agendamento.objects.filter(
        filial=profissional.filial, profissional=profissional,
        status__in=STATUS_OCUPAM_HORARIO, inicio__lt=fim_com_intervalo,
        fim_com_intervalo__gt=inicio,
    )
    if ignorar_agendamento:
        ocupados = ocupados.exclude(pk=ignorar_agendamento.pk)
    return not ocupados.exists()


def listar_horarios(profissional, servicos, data, passo_minutos=15):
    duracao, intervalo, _ = resumo_servicos(servicos, profissional)
    jornada = JornadaTrabalho.objects.filter(
        profissional=profissional, filial=profissional.filial,
        dia_semana=data.weekday(), ativo=True,
    ).first()
    if not jornada:
        return []
    atual = _localizar(data, jornada.inicio)
    limite = _localizar(data, jornada.fim)
    horarios = []
    while atual + timedelta(minutes=duracao + intervalo) <= limite:
        if horario_disponivel(profissional, atual, duracao, intervalo):
            horarios.append(atual)
        atual += timedelta(minutes=passo_minutos)
    return horarios


@tenant_atomic
def criar_agendamento(*, filial, profissional, servicos, inicio, pessoa_atendida_nome, cliente=None, telefone='', origem=Agendamento.Origem.MANUAL, observacao='', usuario=None):
    if timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio, timezone.get_current_timezone())
    if profissional.filial_id != filial.pk:
        raise ValidationError('O profissional não pertence à filial informada.')
    if cliente and not cliente.__class__.objects.for_filial(filial).filter(pk=cliente.pk).exists():
        raise ValidationError('O cliente não pertence à filial informada.')
    profissional = profissional.__class__.objects.select_for_update().get(pk=profissional.pk)
    duracao, intervalo, valor = resumo_servicos(servicos, profissional)
    if not horario_disponivel(profissional, inicio, duracao, intervalo):
        raise ValidationError('Este horário não está mais disponível.')
    fim = inicio + timedelta(minutes=duracao)
    agendamento = Agendamento.objects.create(
        filial=filial, profissional=profissional, cliente=cliente,
        pessoa_atendida_nome=pessoa_atendida_nome, telefone_contato=telefone,
        inicio=inicio, fim=fim, fim_com_intervalo=fim + timedelta(minutes=intervalo),
        origem=origem, valor_total=valor, observacao=observacao, criado_por=usuario,
    )
    AgendamentoItem.objects.bulk_create([
        AgendamentoItem(
            agendamento=agendamento, servico=servico, descricao=servico.descricao,
            duracao_minutos=servico.duracao_servico_minutos,
            intervalo_minutos=servico.intervalo_apos_servico_minutos,
            valor=servico.preco_venda, ordem=ordem,
        )
        for ordem, servico in enumerate(servicos, start=1)
    ])
    return agendamento
