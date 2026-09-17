"""
Parâmetros do Sistema — identidade visual, contatos e configuração fiscal.

A identificação (nome fantasia, razão social, CNPJ, IE, IM) e o endereço
permanecem na ``Filial``; a tela de Parâmetros os edita, mas não duplica o
armazenamento. Este módulo guarda apenas o que ainda não existia: a logo,
o e-mail secundário e a configuração de emissão por documento fiscal.
"""
from decimal import Decimal
from math import isfinite

from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models

from .base import TimestampedModel


LAYOUT_ETIQUETA_VENDA_PADRAO = {
    'logo': {'x': 3, 'y': 10, 'w': 33, 'h': 80},
    'empresa': {'x': 39, 'y': 8, 'w': 58, 'h': 15},
    'cliente': {'x': 39, 'y': 27, 'w': 58, 'h': 24},
    'venda': {'x': 39, 'y': 55, 'w': 27, 'h': 12},
    'data': {'x': 69, 'y': 55, 'w': 28, 'h': 12},
    'mensagem': {'x': 39, 'y': 72, 'w': 58, 'h': 20},
}


def layout_etiqueta_venda_padrao():
    return {chave: dict(posicao) for chave, posicao in LAYOUT_ETIQUETA_VENDA_PADRAO.items()}


def normalizar_layout_etiqueta_venda(valor):
    """Limita o layout ao interior da etiqueta e ignora chaves desconhecidas."""
    layout = layout_etiqueta_venda_padrao()
    if not isinstance(valor, dict):
        return layout
    for chave, padrao in layout.items():
        recebido = valor.get(chave)
        if not isinstance(recebido, dict):
            continue
        try:
            numeros = [
                float(recebido.get(campo, padrao[campo]))
                for campo in ('x', 'y', 'w', 'h')
            ]
        except (TypeError, ValueError):
            continue
        if not all(isfinite(numero) for numero in numeros):
            continue
        x_recebido, y_recebido, largura_recebida, altura_recebida = numeros
        largura = min(100.0, max(8.0, largura_recebida))
        altura = min(100.0, max(6.0, altura_recebida))
        x = min(100.0 - largura, max(0.0, x_recebido))
        y = min(100.0 - altura, max(0.0, y_recebido))
        layout[chave] = {
            'x': round(x, 2), 'y': round(y, 2),
            'w': round(largura, 2), 'h': round(altura, 2),
        }
    return layout


class ParametrosSistema(TimestampedModel):
    """Parâmetros gerais de uma filial (um registro por filial)."""

    filial = models.OneToOneField(
        'core.Filial',
        on_delete=models.CASCADE,
        related_name='parametros_sistema',
    )
    logo = models.ImageField(
        upload_to='sistema/logo/',
        blank=True,
        null=True,
        help_text='Logomarca exibida no topo do sistema e na tela de login.',
    )
    logo_url = models.URLField(
        max_length=500,
        blank=True,
        help_text='URL externa da logo (alternativa ao upload). Não desaparece em redeploys.',
    )
    email_secundario = models.EmailField(max_length=120, blank=True)
    controlar_entrega_contas_receber = models.BooleanField(
        default=False,
        verbose_name='Acompanhar entrega no contas a receber',
        help_text='Exibe situação e previsão de entrega somente nesta filial. Não altera pagamentos.',
    )
    checkout_venda_ativo = models.BooleanField(
        default=False,
        verbose_name='Utilizar checkout de venda',
        help_text='Exibe o Checkout no menu desta filial. Quando desativado, a tela fica indisponível.',
    )
    checkout_busca_nome_senha_hash = models.CharField(
        max_length=128,
        blank=True,
        editable=False,
        verbose_name='Senha da busca por nome no checkout',
        help_text='Hash da senha usada para liberar a busca de produtos por nome no checkout.',
    )
    certificado_digital = models.FileField(
        upload_to='sistema/certificados/',
        blank=True,
        null=True,
        help_text='Arquivo do certificado digital A1 (.pfx ou .p12).',
    )
    senha_certificado = models.CharField(
        max_length=255,
        blank=True,
        help_text='Senha do certificado digital. Use apenas em ambiente controlado.',
    )
    certificado_base64 = models.TextField(
        blank=True,
        default='',
        help_text='Conteúdo do certificado A1 codificado em base64. '
                  'Preenchido automaticamente ao fazer upload. '
                  'Persiste entre redeploys (diferente do FileField).',
    )
    focusnfe_token_principal = models.CharField(
        max_length=255,
        blank=True,
        help_text='Token Principal de Producao usado somente para gerenciar a empresa na Focus.',
    )
    nfce_csc_id = models.CharField(max_length=20, blank=True)
    nfce_csc_token = models.CharField(max_length=120, blank=True)
    nfce_contingencia_automatica = models.BooleanField(
        default=True,
        verbose_name='Ativar contingencia automatica da NFC-e na Focus',
        help_text=(
            'Permite que a Focus alterne para contingencia offline quando a SEFAZ '
            'estiver indisponivel. Nao cobre queda total da internet da loja.'
        ),
    )
    comunicador_offline_instalador = models.FileField(
        upload_to='fiscal/comunicador-offline/',
        blank=True,
        null=True,
        max_length=500,
        validators=[FileExtensionValidator(allowed_extensions=['exe', 'msi', 'zip'])],
        help_text='Instalador oficial fornecido pela Focus NFe (.exe, .msi ou .zip).',
    )
    comunicador_offline_versao = models.CharField(
        max_length=40,
        blank=True,
        help_text='Versao informada pela Focus para este instalador.',
    )
    comunicador_offline_sha256 = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        help_text='Assinatura SHA-256 calculada no upload para conferir a integridade.',
    )
    email_envio_automatico = models.BooleanField(default=False)
    email_resposta = models.EmailField(max_length=120, blank=True)
    texto_padrao_email = models.TextField(blank=True)
    informacoes_complementares_padrao = models.TextField(blank=True)

    class Meta:
        db_table = 'parametros_sistema'
        verbose_name = 'Parâmetros do Sistema'
        verbose_name_plural = 'Parâmetros do Sistema'

    def __str__(self):
        return f'Parâmetros — {self.filial}'


class ConfiguracaoEtiquetaVenda(TimestampedModel):
    """Layout da etiqueta impressa depois da finalização de uma venda."""

    filial = models.OneToOneField(
        'core.Filial',
        on_delete=models.CASCADE,
        related_name='configuracao_etiqueta_venda',
    )
    ativa = models.BooleanField(default=False)
    largura_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('60.00'),
        validators=[MinValueValidator(Decimal('20')), MaxValueValidator(Decimal('300'))],
        help_text='Largura física da etiqueta, em milímetros.',
    )
    altura_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('40.00'),
        validators=[MinValueValidator(Decimal('20')), MaxValueValidator(Decimal('300'))],
        help_text='Altura física da etiqueta, em milímetros.',
    )
    margem_interna_mm = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('10'))],
        help_text='Área de segurança entre o conteúdo e as bordas da etiqueta.',
    )
    deslocamento_horizontal_mm = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('-1.00'),
        validators=[MinValueValidator(Decimal('-10')), MaxValueValidator(Decimal('10'))],
        help_text='Use valor negativo para levar toda a impressão para a esquerda.',
    )
    deslocamento_vertical_mm = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('-10')), MaxValueValidator(Decimal('10'))],
        help_text='Use valor negativo para subir toda a impressão.',
    )
    alta_nitidez = models.BooleanField(
        default=False,
        help_text='Reforça contraste, contornos do texto e renderização da logo.',
    )
    impressora_nome = models.CharField(
        max_length=150,
        blank=True,
        help_text='Nome da impressora de etiquetas que o operador deve selecionar.',
    )
    texto_rodape = models.CharField(
        max_length=300,
        default='Obrigado pela sua preferência!',
        blank=True,
    )
    tamanho_fonte_mensagem_mm = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('2.70'),
        validators=[MinValueValidator(Decimal('1')), MaxValueValidator(Decimal('8'))],
        help_text='Tamanho máximo da fonte da mensagem na etiqueta, em milímetros.',
    )
    exibir_logo = models.BooleanField(default=True)
    exibir_nome_empresa = models.BooleanField(default=True)
    exibir_nome_cliente = models.BooleanField(default=True)
    exibir_numero_venda = models.BooleanField(default=True)
    exibir_data_venda = models.BooleanField(default=True)
    layout_elementos = models.JSONField(default=layout_etiqueta_venda_padrao, blank=True)

    class Meta:
        db_table = 'configuracoes_etiqueta_venda'
        verbose_name = 'Configuração de etiqueta de venda'
        verbose_name_plural = 'Configurações de etiqueta de venda'

    def __str__(self):
        return f'Etiqueta de venda — {self.filial}'

    def layout_normalizado(self):
        return normalizar_layout_etiqueta_venda(self.layout_elementos)


class ParametroDocumentoFiscal(TimestampedModel):
    """Configuração de emissão de um tipo de documento fiscal."""

    class TipoDocumento(models.TextChoices):
        NFE = 'nfe', 'NF-e'
        NFCE = 'nfce', 'NFC-e'
        CTE = 'cte', 'CT-e'
        CTE_OS = 'cte_os', 'CT-e OS'
        MDFE = 'mdfe', 'MDF-e'
        NFCOM = 'nfcom', 'NFCom'
        NFSE = 'nfse', 'NFS-e'
        NFSE_NACIONAL = 'nfse_nacional', 'NFS-e Nacional'

    class Ambiente(models.IntegerChoices):
        PRODUCAO = 1, 'Produção'
        HOMOLOGACAO = 2, 'Homologação'

    parametros = models.ForeignKey(
        ParametrosSistema,
        on_delete=models.CASCADE,
        related_name='documentos_fiscais',
    )
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices)
    habilitado = models.BooleanField(
        default=False,
        help_text='Quando ativo, o documento fica disponível para emissão.',
    )
    serie = models.PositiveSmallIntegerField(default=1)
    proximo_numero = models.BigIntegerField(default=1)
    ambiente = models.SmallIntegerField(
        choices=Ambiente.choices,
        default=Ambiente.HOMOLOGACAO,
    )
    cfop_padrao = models.CharField(max_length=5, blank=True)
    natureza_operacao = models.CharField(max_length=100, blank=True)
    tipo_operacao = models.CharField(max_length=1, default='1', blank=True)
    finalidade_nfe = models.PositiveSmallIntegerField(default=1)
    indicador_destino = models.PositiveSmallIntegerField(default=1)
    indicador_consumidor_final = models.PositiveSmallIntegerField(default=1)
    presenca_comprador = models.PositiveSmallIntegerField(default=1)
    modalidade_frete = models.PositiveSmallIntegerField(default=9)
    enviar_email = models.BooleanField(default=False)
    informacoes_complementares = models.TextField(blank=True)

    class Meta:
        db_table = 'parametros_documento_fiscal'
        verbose_name = 'Parâmetro de Documento Fiscal'
        verbose_name_plural = 'Parâmetros de Documentos Fiscais'
        unique_together = [('parametros', 'tipo_documento')]
        ordering = ['parametros', 'tipo_documento']

    def __str__(self):
        return f'{self.get_tipo_documento_display()} — {self.parametros.filial}'
