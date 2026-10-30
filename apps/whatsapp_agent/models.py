import base64
import hashlib
import uuid
from datetime import time, timedelta
from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models.base import FilialScopedModel, TimestampedModel


def _fernet():
    segredo = getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    chave = base64.urlsafe_b64encode(hashlib.sha256(segredo.encode('utf-8')).digest())
    return Fernet(chave)


def status_pedido_notificados_padrao():
    return [
        'aprovado', 'em_separacao', 'pendente_caixa', 'pago',
        'pronto', 'saiu_entrega', 'entregue', 'cancelado',
    ]


def dias_aviso_boleto_padrao():
    return [-3, -1, 0, 1, 3]


class ConfiguracaoWhatsApp(FilialScopedModel):
    class Provedor(models.TextChoices):
        EVOLUTION = 'evolution', 'Evolution API (não oficial)'
        META_CLOUD = 'meta_cloud', 'WhatsApp Cloud API (oficial)'

    class Status(models.TextChoices):
        NAO_CONFIGURADO = 'nao_configurado', 'Não configurado'
        DESCONECTADO = 'desconectado', 'Desconectado'
        AGUARDANDO_QR = 'aguardando_qr', 'Aguardando QR Code'
        CONECTADO = 'conectado', 'Conectado'
        ERRO = 'erro', 'Erro'

    class ModoAtendimento(models.TextChoices):
        AGENDA = 'agenda', 'Somente agendamento'
        CATALOGO = 'catalogo', 'Somente catálogo e pedidos'
        AMBOS = 'ambos', 'Agendamento e catálogo'

    provedor = models.CharField(
        max_length=30,
        choices=Provedor.choices,
        default=Provedor.EVOLUTION,
    )
    gateway_url = models.URLField(max_length=300, blank=True)
    api_key_criptografada = models.TextField(blank=True)
    meta_phone_number_id = models.CharField(max_length=40, blank=True)
    meta_business_account_id = models.CharField(max_length=40, blank=True)
    meta_graph_version = models.CharField(max_length=12, default='v23.0')
    meta_access_token_criptografado = models.TextField(blank=True)
    meta_verify_token_criptografado = models.TextField(blank=True)
    meta_app_secret_criptografado = models.TextField(blank=True)
    instancia = models.SlugField(max_length=80)
    webhook_secret = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NAO_CONFIGURADO)
    numero_conectado = models.CharField(max_length=24, blank=True)
    nome_conectado = models.CharField(max_length=120, blank=True)
    agente_ativo = models.BooleanField(default=False)
    ia_ativa = models.BooleanField(
        default=False,
        help_text='Usa IA como atendente principal em qualquer mensagem recebida.',
    )
    ia_restringir_horario = models.BooleanField(
        default=True,
        help_text='Fora desta janela, envia o aviso automático e encaminha para atendimento humano.',
    )
    ia_horario_inicio = models.TimeField(default=time(8, 0))
    ia_horario_fim = models.TimeField(default=time(20, 0))
    ia_mensagem_fora_horario = models.TextField(
        default=(
            'Olá! Recebemos sua mensagem, mas nosso time não está disponível agora. '
            'Nosso atendimento funciona das {inicio} às {fim}. '
            'Assim que um atendente estiver disponível, entraremos em contato com você.'
        ),
        help_text='Variáveis disponíveis: {inicio} e {fim}.',
    )
    ia_mensagem_erro = models.TextField(
        default=(
            'Estou com uma instabilidade no atendimento automático agora. '
            'Recebi sua mensagem e vou encaminhá-la para nossa equipe continuar com você.'
        ),
        help_text=(
            'Enviada somente quando a IA não consegue responder. A conversa é '
            'encaminhada automaticamente para atendimento humano.'
        ),
    )
    ia_audio_ativo = models.BooleanField(
        default=True,
        help_text='Transcreve áudios recebidos e processa a transcrição como uma mensagem normal.',
    )
    ia_modelo = models.CharField(max_length=80, default='gpt-4o-mini')
    ia_modelo_transcricao = models.CharField(
        max_length=80,
        default='gpt-4o-mini-transcribe',
    )
    ia_api_key_criptografada = models.TextField(blank=True)
    ia_cotacao_dolar = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('5.50'),
        validators=[MinValueValidator(0.01), MaxValueValidator(100)],
        help_text='Cotação usada apenas para estimar o custo em reais.',
    )
    ia_instrucoes = models.TextField(
        blank=True,
        help_text='Orientações adicionais de atendimento para esta filial.',
    )
    modo_atendimento = models.CharField(
        max_length=16,
        choices=ModoAtendimento.choices,
        default=ModoAtendimento.AGENDA,
    )
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
    mensagem_resumo_pedido = models.TextField(
        default=(
            '🧾 *Resumo do pedido {numero}*\n\n'
            '{itens}\n\n'
            '*Subtotal:* {subtotal}\n'
            '*Frete:* {frete}\n'
            '*Total:* {total}\n'
            '*Recebimento:* {entrega}\n'
            '*Pagamento:* {pagamento}\n'
            '*Observação:* {observacao}'
        ),
        help_text=(
            'Variáveis: {numero}, {nome}, {itens}, {subtotal}, {frete}, {total}, '
            '{entrega}, {pagamento} e {observacao}.'
        ),
    )
    mensagem_pedido_recebido = models.TextField(
        default='Pedido {numero} confirmado! Agora a loja fará a aprovação e iniciará a separação.',
        help_text='Variável disponível: {numero}.',
    )
    mensagem_atualizacao_pedido = models.TextField(
        default=(
            '📦 *Atualização do pedido {numero}*\n\n'
            'Novo status: *{status}*\n'
            'Total: {total}'
        ),
        help_text='Variáveis disponíveis: {numero}, {status}, {nome} e {total}.',
    )
    mensagem_pedido_pronto_retirada = models.TextField(
        default=(
            '✅ *Pedido {numero} pronto para retirada!*\n\n'
            'Olá, {nome}! Seu pedido já foi separado e pode ser retirado na loja.\n'
            'Total: {total}'
        ),
        help_text='Variáveis disponíveis: {numero}, {nome} e {total}.',
    )
    pedido_status_notificados = models.JSONField(
        default=status_pedido_notificados_padrao,
        blank=True,
        help_text='Etapas do pedido que enviam uma atualização automática ao cliente.',
    )
    pedido_resposta_confirmar = models.CharField(max_length=20, default='1')
    pedido_resposta_alterar = models.CharField(max_length=20, default='2')
    pedido_resposta_cancelar = models.CharField(max_length=20, default='3')
    cobranca_ativa = models.BooleanField(default=False)
    cobranca_exigir_consentimento = models.BooleanField(
        default=True,
        help_text='Envia somente para clientes com autorização registrada.',
    )
    cobranca_horario_inicio = models.TimeField(default=time(9, 0))
    cobranca_horario_fim = models.TimeField(default=time(18, 0))
    cobranca_intervalo_minutos = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
    )
    cobranca_limite_diario = models.PositiveIntegerField(
        default=50,
        validators=[MinValueValidator(1), MaxValueValidator(5000)],
    )
    cobranca_max_tentativas = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    cobranca_dias_relativos = models.JSONField(default=dias_aviso_boleto_padrao, blank=True)
    cobranca_mensagem = models.TextField(
        default=(
            'Olá, {nome}! Este é um lembrete da {empresa}.\n\n'
            'O boleto {documento}, no valor de {valor}, vence em {vencimento}.\n'
            '{link}\n\nCaso já tenha efetuado o pagamento, desconsidere esta mensagem.'
        ),
        help_text='Variáveis: {nome}, {empresa}, {documento}, {valor}, {vencimento} e {link}.',
    )
    cobranca_template_meta = models.SlugField(
        max_length=512,
        default='aviso_vencimento_boleto',
        blank=True,
    )
    cobranca_template_idioma = models.CharField(max_length=12, default='pt_BR')
    ultimo_envio_cobranca_em = models.DateTimeField(null=True, blank=True)
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

    def _definir_segredo(self, campo, valor):
        valor = (valor or '').strip()
        if valor:
            setattr(self, campo, _fernet().encrypt(valor.encode('utf-8')).decode('ascii'))

    def _obter_segredo(self, campo):
        valor = getattr(self, campo, '')
        if not valor:
            return ''
        try:
            return _fernet().decrypt(valor.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError):
            return ''

    def definir_meta_access_token(self, valor):
        self._definir_segredo('meta_access_token_criptografado', valor)

    def obter_meta_access_token(self):
        return self._obter_segredo('meta_access_token_criptografado')

    def definir_meta_verify_token(self, valor):
        self._definir_segredo('meta_verify_token_criptografado', valor)

    def obter_meta_verify_token(self):
        return self._obter_segredo('meta_verify_token_criptografado')

    def definir_meta_app_secret(self, valor):
        self._definir_segredo('meta_app_secret_criptografado', valor)

    def obter_meta_app_secret(self):
        return self._obter_segredo('meta_app_secret_criptografado')

    @property
    def meta_credenciais_configuradas(self):
        return bool(self.meta_phone_number_id and self.obter_meta_access_token())

    @property
    def ia_api_key_configurada(self):
        return bool(self.ia_api_key_criptografada or getattr(settings, 'OPENAI_API_KEY', ''))

    def definir_ia_api_key(self, valor):
        valor = (valor or '').strip()
        if valor:
            self.ia_api_key_criptografada = _fernet().encrypt(
                valor.encode('utf-8'),
            ).decode('ascii')

    def obter_ia_api_key(self):
        if self.ia_api_key_criptografada:
            try:
                return _fernet().decrypt(
                    self.ia_api_key_criptografada.encode('ascii'),
                ).decode('utf-8')
            except (InvalidToken, ValueError):
                return ''
        return getattr(settings, 'OPENAI_API_KEY', '')


class ConexaoWhatsAppVendedor(TimestampedModel):
    """Número comercial de um vendedor, conectado ao CRM por QR Code."""

    class Status(models.TextChoices):
        NAO_CONFIGURADO = 'nao_configurado', 'Não configurado'
        PENDENTE = 'pendente', 'Pendente de conexão'
        CONECTADO = 'conectado', 'Conectado'
        DESCONECTADO = 'desconectado', 'Desconectado'
        ERRO = 'erro', 'Erro'

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.PROTECT,
        related_name='conexoes_whatsapp_vendedores',
    )
    filial_padrao = models.ForeignKey(
        'core.Filial', on_delete=models.PROTECT, null=True, blank=True,
        related_name='conexoes_whatsapp_vendedores',
        help_text='Filial operacional sugerida ao abrir o CRM deste número.',
    )
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='conexoes_whatsapp_crm',
    )
    meta_phone_number_id = models.CharField(max_length=40, blank=True)
    meta_business_account_id = models.CharField(max_length=40, blank=True)
    meta_graph_version = models.CharField(max_length=12, default='v23.0')
    meta_access_token_criptografado = models.TextField(blank=True)
    meta_verify_token_criptografado = models.TextField(blank=True)
    meta_app_secret_criptografado = models.TextField(blank=True)
    webhook_secret = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    numero_conectado = models.CharField(max_length=24, blank=True)
    nome_conectado = models.CharField(max_length=120, blank=True)
    coexistencia_ativa = models.BooleanField(default=True)
    somente_atendimento_humano = models.BooleanField(
        default=True,
        help_text=(
            'Envia somente textos digitados por pessoas e nunca usa modelos '
            'aprovados ou automações para iniciar uma conversa.'
        ),
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.NAO_CONFIGURADO,
        db_index=True,
    )
    ultimo_evento_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'whatsapp_conexoes_vendedores'
        ordering = ['vendedor__nome']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'vendedor'], condition=models.Q(ativo=True),
                name='whatsapp_conexao_vendedor_ativa_unica',
            ),
            models.UniqueConstraint(
                fields=['meta_phone_number_id'],
                condition=~models.Q(meta_phone_number_id=''),
                name='whatsapp_conexao_phone_number_unico',
            ),
            models.UniqueConstraint(
                fields=['numero_conectado'], condition=~models.Q(numero_conectado=''),
                name='whatsapp_conexao_numero_unico',
            ),
        ]

    def clean(self):
        super().clean()
        erros = {}
        if self.vendedor_id and self.empresa_id:
            if self.vendedor.empresa_id != self.empresa_id:
                erros['vendedor'] = 'O vendedor pertence a outra empresa.'
        if self.filial_padrao_id and self.empresa_id:
            if self.filial_padrao.empresa_id != self.empresa_id:
                erros['filial_padrao'] = 'A filial padrão pertence a outra empresa.'
        if erros:
            raise ValidationError(erros)

    def _definir_segredo(self, campo, valor):
        valor = (valor or '').strip()
        if valor:
            setattr(self, campo, _fernet().encrypt(valor.encode('utf-8')).decode('ascii'))

    def _obter_segredo(self, campo):
        valor = getattr(self, campo, '')
        if not valor:
            return ''
        try:
            return _fernet().decrypt(valor.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError):
            return ''

    def definir_meta_access_token(self, valor):
        self._definir_segredo('meta_access_token_criptografado', valor)

    def obter_meta_access_token(self):
        return self._obter_segredo('meta_access_token_criptografado')

    def definir_meta_verify_token(self, valor):
        self._definir_segredo('meta_verify_token_criptografado', valor)

    def obter_meta_verify_token(self):
        return self._obter_segredo('meta_verify_token_criptografado')

    def definir_meta_app_secret(self, valor):
        self._definir_segredo('meta_app_secret_criptografado', valor)

    def obter_meta_app_secret(self):
        return self._obter_segredo('meta_app_secret_criptografado')

    @property
    def meta_credenciais_configuradas(self):
        return bool(self.meta_phone_number_id and self.obter_meta_access_token())

    @property
    def instancia(self):
        """Nome estável e isolado da sessão do vendedor no gateway de QR Code."""
        return f'ited-crm-{self.webhook_secret.hex}'

    def __str__(self):
        numero = self.numero_conectado or 'número pendente'
        return f'{self.vendedor} — {numero}'


class ConfiguracaoWhatsAppCentral(TimestampedModel):
    """Número institucional do iTED, usado somente para mensagens gerenciais."""

    class Status(models.TextChoices):
        NAO_CONFIGURADO = 'nao_configurado', 'Não configurado'
        DESCONECTADO = 'desconectado', 'Desconectado'
        AGUARDANDO_QR = 'aguardando_qr', 'Aguardando QR Code'
        CONECTADO = 'conectado', 'Conectado'
        ERRO = 'erro', 'Erro'

    instancia = models.SlugField(max_length=80, unique=True, default='ited-central-dashboard')
    webhook_secret = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.NAO_CONFIGURADO,
    )
    numero_conectado = models.CharField(max_length=24, blank=True)
    nome_conectado = models.CharField(max_length=120, blank=True)
    resumos_ativos = models.BooleanField(
        default=True,
        help_text='Ativa a preparação automática dos resumos diários.',
    )
    horario_inicio = models.TimeField(default=time(8, 0))
    horario_fim = models.TimeField(default=time(18, 0))
    intervalo_entre_envios_minutos = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
        help_text='Uma mensagem por vez, respeitando este intervalo mínimo.',
    )
    limite_diario = models.PositiveIntegerField(
        default=200,
        validators=[MinValueValidator(1), MaxValueValidator(5000)],
    )
    max_tentativas = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    mensagem_resumo = models.TextField(
        default=(
            '📊 *Resumo diário — {filial}*\n'
            '🗓️ {data}\n\n'
            '💰 *Vendas:* {vendas}\n'
            '💵 *Recebido:* {recebido}\n'
            '🧾 *Quantidade de vendas:* {quantidade_vendas}\n'
            '🎯 *Ticket médio:* {ticket_medio}\n'
            '🏷️ *Descontos:* {descontos}\n'
            '↩️ *Cancelamentos:* {cancelamentos}\n\n'
            '⚠️ *Estoque crítico:* {estoque_critico}\n'
            '⏳ *Produtos próximos do vencimento:* {vencimentos}\n'
            '📌 *Contas a receber vencidas:* {contas_vencidas}\n'
            '📈 *Comparação com o dia anterior:* {comparacao}'
            '{agenda}'
        ),
        help_text=(
            'Variáveis: {empresa}, {filial}, {data}, {entradas_financeiras}, '
            '{vendas}, {recebido}, {quantidade_vendas}, {ticket_medio}, '
            '{descontos}, {cancelamentos}, '
            '{estoque_critico}, {vencimentos}, {contas_vencidas}, {comparacao} e {agenda}.'
        ),
    )
    ultima_conexao_em = models.DateTimeField(null=True, blank=True)
    ultimo_envio_em = models.DateTimeField(null=True, blank=True)
    ultimo_evento_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.TextField(blank=True)

    class Meta:
        db_table = 'whatsapp_configuracao_central'

    def __str__(self):
        return f'WhatsApp central iTED — {self.instancia}'

    @property
    def gateway_url(self):
        return ''

    def obter_api_key(self):
        return ''

    @classmethod
    def carregar(cls):
        configuracao, _ = cls.objects.using('default').get_or_create(pk=1)
        return configuracao


class EnvioResumoWhatsApp(TimestampedModel):
    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        ENVIANDO = 'enviando', 'Enviando'
        ENVIADO = 'enviado', 'Enviado'
        ERRO = 'erro', 'Erro'
        CANCELADO = 'cancelado', 'Cancelado'

    configuracao = models.ForeignKey(
        ConfiguracaoWhatsAppCentral,
        on_delete=models.PROTECT,
        related_name='envios',
    )
    tenant_alias = models.SlugField(max_length=80, blank=True, default='default')
    empresa_nome = models.CharField(max_length=150)
    filial_nome = models.CharField(max_length=150)
    filial_cnpj = models.CharField(max_length=14)
    destinatario_nome = models.CharField(max_length=120)
    telefone = models.CharField(max_length=13)
    data_referencia = models.DateField(db_index=True)
    metricas = models.JSONField(default=dict)
    mensagem = models.TextField()
    agendado_para = models.DateTimeField(db_index=True)
    disparo_manual = models.BooleanField(
        default=False,
        help_text='Permite iniciar este envio fora da janela da rotina automática.',
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDENTE, db_index=True,
    )
    tentativas = models.PositiveSmallIntegerField(default=0)
    enviado_em = models.DateTimeField(null=True, blank=True)
    identificador_externo = models.CharField(max_length=160, blank=True)
    ultimo_erro = models.TextField(blank=True)

    class Meta:
        db_table = 'whatsapp_envios_resumo'
        ordering = ['-data_referencia', 'agendado_para', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_alias', 'filial_cnpj', 'telefone', 'data_referencia'],
                name='whatsapp_resumo_filial_destino_dia_unico',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'agendado_para']),
            models.Index(fields=['tenant_alias', 'filial_cnpj', 'data_referencia']),
        ]

    def __str__(self):
        return f'{self.filial_nome} → {self.telefone} ({self.data_referencia:%d/%m/%Y})'


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
        CATALOGO = 'catalogo', 'Enviar link do catálogo'
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
        ConfiguracaoWhatsApp, on_delete=models.CASCADE, null=True, blank=True,
        related_name='conversas',
    )
    conexao_vendedor = models.ForeignKey(
        ConexaoWhatsAppVendedor, on_delete=models.CASCADE, null=True, blank=True,
        related_name='conversas',
    )
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conversas_whatsapp',
    )
    remote_jid = models.CharField(max_length=100)
    telefone = models.CharField(max_length=24, db_index=True)
    nome_contato = models.CharField(max_length=150, blank=True)
    tenant_alias = models.SlugField(max_length=80, blank=True)
    cliente_operacional_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    cliente_grupo_replicacao = models.UUIDField(null=True, blank=True, db_index=True)
    fora_carteira = models.BooleanField(default=False, db_index=True)
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
    ultima_mensagem_cliente_em = models.DateTimeField(
        null=True, blank=True, db_index=True,
    )
    agendamento_iniciado_em = models.DateTimeField(null=True, blank=True)
    acompanhamento_enviado_em = models.DateTimeField(null=True, blank=True)
    agendamento_confirmado_em = models.DateTimeField(null=True, blank=True)
    motivo_nao_agendamento = models.CharField(max_length=180, blank=True)

    class Meta:
        db_table = 'whatsapp_conversas'
        ordering = ['-ultima_mensagem_em', '-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['configuracao', 'remote_jid'],
                condition=models.Q(ativa=True, configuracao__isnull=False),
                name='whatsapp_conversa_remota_ativa_unica',
            ),
            models.UniqueConstraint(
                fields=['conexao_vendedor', 'remote_jid'],
                condition=models.Q(ativa=True, conexao_vendedor__isnull=False),
                name='whatsapp_conversa_vendedor_remota_ativa_unica',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(configuracao__isnull=False, conexao_vendedor__isnull=True)
                    | models.Q(configuracao__isnull=True, conexao_vendedor__isnull=False)
                ),
                name='whatsapp_conversa_canal_unico',
            ),
        ]

    def __str__(self):
        return self.nome_contato or self.telefone

    @property
    def janela_atendimento_expira_em(self):
        if not self.ultima_mensagem_cliente_em:
            return None
        return self.ultima_mensagem_cliente_em + timedelta(hours=24)

    @property
    def janela_atendimento_ativa(self):
        expira_em = self.janela_atendimento_expira_em
        return bool(expira_em and timezone.now() <= expira_em)


class MensagemWhatsApp(TimestampedModel):
    class Direcao(models.TextChoices):
        ENTRADA = 'entrada', 'Recebida'
        SAIDA = 'saida', 'Enviada'

    class Origem(models.TextChoices):
        CLIENTE = 'cliente', 'Cliente'
        CELULAR = 'celular', 'WhatsApp Business no celular'
        CRM = 'crm', 'CRM do iTED'
        AUTOMACAO = 'automacao', 'Automação'
        SISTEMA = 'sistema', 'Sistema'

    conversa = models.ForeignKey(ConversaWhatsApp, on_delete=models.CASCADE, related_name='mensagens')
    direcao = models.CharField(max_length=10, choices=Direcao.choices)
    identificador_externo = models.CharField(max_length=160, blank=True)
    tipo = models.CharField(max_length=30, default='texto')
    texto = models.TextField(blank=True)
    texto_original = models.TextField(blank=True)
    origem = models.CharField(
        max_length=16, choices=Origem.choices, default=Origem.SISTEMA, db_index=True,
    )
    autor_nome = models.CharField(max_length=120, blank=True)
    autor_email = models.EmailField(blank=True)
    status = models.CharField(max_length=30, blank=True)
    dados_evento = models.JSONField(default=dict, blank=True)
    editada_em = models.DateTimeField(null=True, blank=True)
    apagada_em = models.DateTimeField(null=True, blank=True)

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


class ConsentimentoCobrancaWhatsApp(TimestampedModel):
    tenant_alias = models.SlugField(max_length=80, default='default')
    filial_cnpj = models.CharField(max_length=14)
    cliente_id = models.BigIntegerField()
    telefone = models.CharField(max_length=15)
    autorizado = models.BooleanField(default=True)
    autorizado_em = models.DateTimeField(null=True, blank=True)
    revogado_em = models.DateTimeField(null=True, blank=True)
    origem = models.CharField(max_length=30, default='financeiro')
    registrado_por = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'whatsapp_consentimentos_cobranca'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_alias', 'filial_cnpj', 'cliente_id'],
                name='whatsapp_consent_cobranca_cliente_uniq',
            ),
        ]


class EnvioCobrancaWhatsApp(TimestampedModel):
    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        ENVIANDO = 'enviando', 'Enviando'
        ENVIADO = 'enviado', 'Enviado'
        ERRO = 'erro', 'Erro'
        CANCELADO = 'cancelado', 'Cancelado'

    configuracao = models.ForeignKey(
        ConfiguracaoWhatsApp,
        on_delete=models.PROTECT,
        related_name='envios_cobranca',
    )
    tenant_alias = models.SlugField(max_length=80, default='default')
    filial_cnpj = models.CharField(max_length=14)
    conta_receber_id = models.BigIntegerField()
    boleto_id = models.BigIntegerField(null=True, blank=True)
    cliente_id = models.BigIntegerField()
    cliente_nome = models.CharField(max_length=150)
    telefone = models.CharField(max_length=15)
    documento = models.CharField(max_length=200, blank=True)
    data_vencimento = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    evento = models.CharField(max_length=30)
    mensagem = models.TextField()
    parametros_template = models.JSONField(default=list, blank=True)
    agendado_para = models.DateTimeField(db_index=True)
    disparo_manual = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDENTE,
        db_index=True,
    )
    tentativas = models.PositiveSmallIntegerField(default=0)
    enviado_em = models.DateTimeField(null=True, blank=True)
    identificador_externo = models.CharField(max_length=200, blank=True)
    ultimo_erro = models.TextField(blank=True)

    class Meta:
        db_table = 'whatsapp_envios_cobranca'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_alias', 'conta_receber_id', 'evento', 'data_vencimento'],
                name='whatsapp_cobranca_titulo_evento_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'agendado_para']),
            models.Index(fields=['tenant_alias', 'filial_cnpj', 'data_vencimento']),
        ]


class UsoIAWhatsApp(FilialScopedModel):
    class Tipo(models.TextChoices):
        RESPOSTA = 'resposta', 'Resposta do agente'
        TRANSCRICAO = 'transcricao', 'Transcrição de áudio'

    configuracao = models.ForeignKey(
        ConfiguracaoWhatsApp,
        on_delete=models.CASCADE,
        related_name='usos_ia',
    )
    conversa = models.ForeignKey(
        ConversaWhatsApp,
        on_delete=models.CASCADE,
        related_name='usos_ia',
    )
    mensagem = models.ForeignKey(
        MensagemWhatsApp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usos_ia',
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    modelo = models.CharField(max_length=80)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_entrada_cache = models.PositiveIntegerField(default=0)
    tokens_saida = models.PositiveIntegerField(default=0)
    tokens_total = models.PositiveIntegerField(default=0)
    segundos_audio = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    custo_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    cotacao_dolar = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    custo_brl = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    metadados = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'whatsapp_usos_ia'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['filial', '-created_at']),
            models.Index(fields=['conversa', '-created_at']),
        ]
