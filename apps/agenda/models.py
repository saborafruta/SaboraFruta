from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


class ProfissionalAgenda(FilialScopedModel):
    funcionario = models.OneToOneField(
        'cadastros.Funcionario', on_delete=models.PROTECT, related_name='perfil_agenda',
    )
    servicos = models.ManyToManyField(
        'produtos.Produto', through='ProfissionalServico', related_name='profissionais_agenda',
    )
    intervalo_padrao_minutos = models.PositiveSmallIntegerField(default=0)
    cor = models.CharField(max_length=7, default='#2563eb')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'agenda_profissionais'
        ordering = ['funcionario__nome']
        constraints = [
            models.UniqueConstraint(fields=['filial', 'funcionario'], name='agenda_profissional_filial_unico'),
        ]

    def __str__(self):
        return self.funcionario.nome


class ProfissionalServico(TimestampedModel):
    profissional = models.ForeignKey(ProfissionalAgenda, on_delete=models.CASCADE, related_name='servicos_vinculados')
    servico = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='profissionais_vinculados')
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'agenda_profissionais_servicos'
        constraints = [
            models.UniqueConstraint(fields=['profissional', 'servico'], name='agenda_profissional_servico_unico'),
        ]

    def clean(self):
        if self.servico_id and (
            self.servico.tipo_produto != self.servico.TipoProduto.SERVICO
            or not self.servico.agendavel
        ):
            raise ValidationError({'servico': 'Selecione um serviço disponível para agendamento.'})


class JornadaTrabalho(FilialScopedModel):
    class DiaSemana(models.IntegerChoices):
        SEGUNDA = 0, 'Segunda-feira'
        TERCA = 1, 'Terça-feira'
        QUARTA = 2, 'Quarta-feira'
        QUINTA = 3, 'Quinta-feira'
        SEXTA = 4, 'Sexta-feira'
        SABADO = 5, 'Sábado'
        DOMINGO = 6, 'Domingo'

    profissional = models.ForeignKey(ProfissionalAgenda, on_delete=models.CASCADE, related_name='jornadas')
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    inicio = models.TimeField()
    fim = models.TimeField()
    pausa_inicio = models.TimeField(null=True, blank=True)
    pausa_fim = models.TimeField(null=True, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'agenda_jornadas'
        ordering = ['dia_semana', 'inicio']
        constraints = [
            models.UniqueConstraint(fields=['profissional', 'dia_semana'], name='agenda_jornada_dia_unico'),
        ]

    def clean(self):
        if self.inicio and self.fim and self.inicio >= self.fim:
            raise ValidationError({'fim': 'O fim da jornada deve ser posterior ao início.'})
        if bool(self.pausa_inicio) != bool(self.pausa_fim):
            raise ValidationError('Informe o início e o fim da pausa.')
        if self.pausa_inicio and not (self.inicio < self.pausa_inicio < self.pausa_fim < self.fim):
            raise ValidationError('A pausa precisa estar dentro da jornada.')


class BloqueioAgenda(FilialScopedModel):
    profissional = models.ForeignKey(
        ProfissionalAgenda, on_delete=models.CASCADE, null=True, blank=True, related_name='bloqueios',
        help_text='Em branco, bloqueia toda a filial.',
    )
    inicio = models.DateTimeField(db_index=True)
    fim = models.DateTimeField(db_index=True)
    motivo = models.CharField(max_length=160)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'agenda_bloqueios'
        ordering = ['inicio']

    def clean(self):
        if self.inicio and self.fim and self.inicio >= self.fim:
            raise ValidationError({'fim': 'O fim do bloqueio deve ser posterior ao início.'})


class Agendamento(FilialScopedModel):
    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Aguardando confirmação'
        CONFIRMADO = 'confirmado', 'Confirmado'
        CHEGOU = 'chegou', 'Cliente chegou'
        EM_ATENDIMENTO = 'em_atendimento', 'Em atendimento'
        CONCLUIDO = 'concluido', 'Concluído'
        CANCELADO = 'cancelado', 'Cancelado'
        NAO_COMPARECEU = 'nao_compareceu', 'Não compareceu'

    class Origem(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        WHATSAPP_NAO_OFICIAL = 'whatsapp_nao_oficial', 'WhatsApp não oficial'
        WHATSAPP_OFICIAL = 'whatsapp_oficial', 'WhatsApp oficial'
        LINK = 'link', 'Link de agendamento'

    cliente = models.ForeignKey('cadastros.Cliente', on_delete=models.PROTECT, null=True, blank=True, related_name='agendamentos')
    profissional = models.ForeignKey(ProfissionalAgenda, on_delete=models.PROTECT, related_name='agendamentos')
    pessoa_atendida_nome = models.CharField(max_length=150)
    telefone_contato = models.CharField(max_length=20, blank=True)
    inicio = models.DateTimeField(db_index=True)
    fim = models.DateTimeField(db_index=True)
    fim_com_intervalo = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.CONFIRMADO, db_index=True)
    origem = models.CharField(max_length=24, choices=Origem.choices, default=Origem.MANUAL)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)
    criado_por = models.ForeignKey('core.Usuario', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'agenda_agendamentos'
        ordering = ['inicio']
        indexes = [models.Index(fields=['filial', 'profissional', 'inicio'])]

    def clean(self):
        if self.inicio and self.fim and self.inicio >= self.fim:
            raise ValidationError({'fim': 'O fim deve ser posterior ao início.'})
        if self.fim and self.fim_com_intervalo and self.fim_com_intervalo < self.fim:
            raise ValidationError({'fim_com_intervalo': 'O intervalo não pode terminar antes do serviço.'})

    def __str__(self):
        return f'{self.pessoa_atendida_nome} - {self.inicio:%d/%m/%Y %H:%M}'


class AgendamentoItem(TimestampedModel):
    agendamento = models.ForeignKey(Agendamento, on_delete=models.CASCADE, related_name='itens')
    servico = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='+')
    descricao = models.CharField(max_length=150)
    duracao_minutos = models.PositiveSmallIntegerField()
    intervalo_minutos = models.PositiveSmallIntegerField(default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'agenda_agendamento_itens'
        ordering = ['ordem', 'id']
