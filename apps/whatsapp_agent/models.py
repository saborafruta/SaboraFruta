import base64
import hashlib
import uuid
from datetime import time

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


def _fernet():
    segredo = getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    chave = base64.urlsafe_b64encode(hashlib.sha256(segredo.encode('utf-8')).digest())
    return Fernet(chave)


class ConfiguracaoWhatsApp(FilialScopedModel):
    class Status(models.TextChoices):
        NAO_CONFIGURADO = 'nao_configurado', 'Não configurado'
        DESCONECTADO = 'desconectado', 'Desconectado'
        AGUARDANDO_QR = 'aguardando_qr', 'Aguardando QR Code'
        CONECTADO = 'conectado', 'Conectado'
        ERRO = 'erro', 'Erro'

    provedor = models.CharField(max_length=30, default='evolution')
    gateway_url = models.URLField(max_length=300, blank=True)
    api_key_criptografada = models.TextField(blank=True)
    instancia = models.SlugField(max_length=80)
    webhook_secret = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NAO_CONFIGURADO)
    numero_conectado = models.CharField(max_length=24, blank=True)
    nome_conectado = models.CharField(max_length=120, blank=True)
    agente_ativo = models.BooleanField(default=False)
    mensagem_saudacao = models.TextField(
        default='Olá! Sou o assistente virtual. Posso ajudar você a agendar um serviço.',
    )
    mensagem_transferencia = models.TextField(
        default='Certo. Vou pausar o atendimento automático para uma pessoa continuar com você.',
    )
    mensagem_encerramento = models.TextField(
        default=(
            'Conversa encerrada. Obrigado pelo contato! 👋\n'
            'Quando precisar, envie *oi* para começar novamente.'
        ),
    )
    mensagem_opcao_invalida = models.TextField(default='Não entendi essa opção.')
    mensagem_confirmacao_agendamento = models.TextField(
        default=(
            'Olá, {nome}! ✅\n\n'
            'Seu agendamento foi confirmado:\n\n'
            '*Serviço:* {servicos}\n'
            '*Profissional:* {profissional}\n'
            '*Data:* {data}\n'
            '*Horário:* {horario}\n\n'
            'Seu horário já está reservado. Até lá!'
        ),
        help_text=(
            'Variáveis disponíveis: {nome}, {servicos}, {profissional}, '
            '{data} e {horario}.'
        ),
    )
    mensagem_lembrete_agendamento = models.TextField(
        default=(
            'Olá, {nome}! 👋\n\n'
            'Este é um lembrete do seu agendamento:\n\n'
            '*Serviço:* {servicos}\n'
            '*Profissional:* {profissional}\n'
            '*Data:* {data}\n'
            '*Horário:* {horario}\n\n'
            'Esperamos você!'
        ),
        help_text=(
            'Variáveis disponíveis: {nome}, {servicos}, {profissional}, '
            '{data} e {horario}.'
        ),
    )
    recuperacao_agendamento_ativa = models.BooleanField(default=False)
    recuperacao_atraso_minutos = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(5), MaxValueValidator(10080)],
        help_text='Tempo após o início do agendamento antes de enviar o acompanhamento.',
    )
    recuperacao_horario_inicio = models.TimeField(default=time(8, 0))
    recuperacao_horario_fim = models.TimeField(default=time(19, 0))
    mensagem_recuperacao_agendamento = models.TextField(
        default=(
            'Oi, {nome}! Você conseguiu escolher seu horário? 😊\n\n'
            'Se precisar, posso ajudar por aqui.\n\n'
            '*1.* Continuar o agendamento\n'
            '*2.* Tenho uma dúvida\n'
            '*3.* Não quero agendar agora\n\n'
            '{link}'
        ),
        help_text='Variáveis disponíveis: {nome} e {link}.',
    )
    mensagem_motivo_nao_agendamento = models.TextField(
        default=(
            'Tudo bem! Se puder, conte o principal motivo. Isso nos ajuda a melhorar:\n\n'
            '*1.* Não encontrei um horário\n'
            '*2.* O valor não serviu para mim\n'
            '*3.* Não encontrei o serviço\n'
            '*4.* Tive dificuldade para agendar\n'
            '*5.* Vou agendar depois\n\n'
            'Você também pode escrever outro motivo ou digitar *pular*.'
        ),
    )
    mensagem_feedback_agendamento = models.TextField(
        default='Obrigado pela resposta! Quando precisar, é só enviar *oi*. 👋',
    )
    encerramento_automatico_ativo = models.BooleanField(default=False)
    tempo_inatividade_minutos = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(1), MaxValueValidator(10080)],
        help_text='Tempo sem mensagens antes de encerrar a conversa, entre 1 minuto e 7 dias.',
    )
    ultima_conexao_em = models.DateTimeField(null=True, blank=True)
    ultimo_evento_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.TextField(blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'whatsapp_configuracoes'
        constraints = [
            models.UniqueConstraint(fields=['filial'], name='whatsapp_configuracao_filial_unica'),
            models.UniqueConstraint(fields=['instancia'], name='whatsapp_instancia_unica'),
        ]

    def __str__(self):
        return f'{self.filial} — {self.instancia}'

    @property
    def api_key_configurada(self):
        return bool(self.api_key_criptografada)

    def definir_api_key(self, valor):
        valor = (valor or '').strip()
        if valor:
            self.api_key_criptografada = _fernet().encrypt(valor.encode('utf-8')).decode('ascii')

    def obter_api_key(self):
        if not self.api_key_criptografada:
            return ''
        try:
            return _fernet().decrypt(self.api_key_criptografada.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError):
            return ''


class MenuWhatsApp(FilialScopedModel):
    configuracao = models.ForeignKey(
        ConfiguracaoWhatsApp, on_delete=models.CASCADE, related_name='menus',
    )
    codigo = models.SlugField(max_length=60)
    nome = models.CharField(max_length=80)
    mensagem = models.TextField(default='Como posso ajudar?')
    principal = models.BooleanField(default=False)
    ativo = models.BooleanField(default=True)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'whatsapp_menus'
        ordering = ['ordem', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['configuracao', 'codigo'], name='whatsapp_menu_codigo_unico',
            ),
        ]

    def __str__(self):
        return self.nome


class OpcaoMenuWhatsApp(TimestampedModel):
    class Acao(models.TextChoices):
        AGENDA = 'agenda', 'Enviar link da agenda'
        ATENDIMENTO_HUMANO = 'atendimento_humano', 'Transferir para atendente'
        MENSAGEM = 'mensagem', 'Enviar uma mensagem'
        ABRIR_MENU = 'abrir_menu', 'Abrir outro menu'
        MENU_PRINCIPAL = 'menu_principal', 'Voltar ao menu principal'
        ENCERRAR = 'encerrar', 'Encerrar conversa'

    menu = models.ForeignKey(MenuWhatsApp, on_delete=models.CASCADE, related_name='opcoes')
    chave = models.CharField(max_length=20)
    titulo = models.CharField(max_length=120)
    acao = models.CharField(max_length=32, choices=Acao.choices)
    mensagem = models.TextField(blank=True)
    palavras_chave = models.TextField(
        blank=True,
        help_text='Palavras alternativas separadas por vírgula ou uma por linha.',
    )
    menu_destino = models.ForeignKey(
        MenuWhatsApp, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    voltar_ao_menu = models.BooleanField(default=False)
    ativo = models.BooleanField(default=True)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'whatsapp_menu_opcoes'
        ordering = ['ordem', 'id']
        constraints = [
            models.UniqueConstraint(fields=['menu', 'chave'], name='whatsapp_opcao_chave_unica'),
        ]

    def __str__(self):
        return f'{self.chave} — {self.titulo}'


class ConversaWhatsApp(FilialScopedModel):
    class EtapaCRM(models.TextChoices):
        NOVA = 'nova', 'Novas conversas'
        INTERESSADO = 'interessado', 'Interessados'
        AGENDAMENTO_INICIADO = 'agendamento_iniciado', 'Agendamento iniciado'
        AGUARDANDO_CLIENTE = 'aguardando_cliente', 'Aguardando cliente'
        AGENDADO = 'agendado', 'Agendados'
        ATENDIMENTO_HUMANO = 'atendimento_humano', 'Atendimento humano'
        CONCLUIDO = 'concluido', 'Concluídos'
        NAO_CONVERTIDO = 'nao_convertido', 'Não convertidos'

    configuracao = models.ForeignKey(
        ConfiguracaoWhatsApp, on_delete=models.CASCADE, related_name='conversas',
    )
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conversas_whatsapp',
    )
    remote_jid = models.CharField(max_length=100)
    telefone = models.CharField(max_length=24, db_index=True)
    nome_contato = models.CharField(max_length=150, blank=True)
    etapa = models.CharField(max_length=40, default='inicio')
    etapa_crm = models.CharField(
        max_length=32,
        choices=EtapaCRM.choices,
        default=EtapaCRM.NOVA,
        db_index=True,
    )
    contexto = models.JSONField(default=dict, blank=True)
    atendimento_humano = models.BooleanField(default=False)
    ativa = models.BooleanField(default=True)
    ultima_mensagem_em = models.DateTimeField(null=True, blank=True)
    agendamento_iniciado_em = models.DateTimeField(null=True, blank=True)
    acompanhamento_enviado_em = models.DateTimeField(null=True, blank=True)
    agendamento_confirmado_em = models.DateTimeField(null=True, blank=True)
    motivo_nao_agendamento = models.CharField(max_length=180, blank=True)

    class Meta:
        db_table = 'whatsapp_conversas'
        ordering = ['-ultima_mensagem_em', '-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['configuracao', 'remote_jid'], name='whatsapp_conversa_remota_unica',
            ),
        ]

    def __str__(self):
        return self.nome_contato or self.telefone


class MensagemWhatsApp(TimestampedModel):
    class Direcao(models.TextChoices):
        ENTRADA = 'entrada', 'Recebida'
        SAIDA = 'saida', 'Enviada'

    conversa = models.ForeignKey(ConversaWhatsApp, on_delete=models.CASCADE, related_name='mensagens')
    direcao = models.CharField(max_length=10, choices=Direcao.choices)
    identificador_externo = models.CharField(max_length=160, blank=True)
    tipo = models.CharField(max_length=30, default='texto')
    texto = models.TextField(blank=True)
    status = models.CharField(max_length=30, blank=True)
    dados_evento = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'whatsapp_mensagens'
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['conversa', 'identificador_externo'],
                condition=~models.Q(identificador_externo=''),
                name='whatsapp_mensagem_externa_unica',
            ),
        ]
