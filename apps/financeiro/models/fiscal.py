"""Bloco 11 — Fiscal e tributário."""
from django.db import models
from apps.core.models import Empresa, Filial, Usuario
from apps.cadastros.models import Transportadora
from apps.core.models.base import TimestampedModel, ActiveModel
from apps.core.models.base import FilialManager as FilialAwareManager
from apps.core.services.exceptions import DadosInvalidosError
from ..constants.enums import TipoDocumentoFiscal, StatusDocumentoFiscal


class DocumentoFiscalProtegidoError(DadosInvalidosError):
    """Tentaram apagar um documento que já existe fora deste sistema."""


class ClasseFiscal(ActiveModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="financeiro_classes_fiscais")
    codigo = models.CharField(max_length=20)
    descricao = models.CharField(max_length=100)
    cst_icms_padrao = models.CharField(max_length=3, blank=True)
    csosn_padrao = models.CharField(max_length=3, blank=True)
    cst_pis_padrao = models.CharField(max_length=2, blank=True)
    cst_cofins_padrao = models.CharField(max_length=2, blank=True)
    cst_ipi_padrao = models.CharField(max_length=2, blank=True)
    pis_cofins_monofasico = models.BooleanField(default=False)
    ipi_suspenso = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financeiro_classes_fiscais"
        verbose_name = "Classe fiscal"
        verbose_name_plural = "Classes fiscais"
        unique_together = [("empresa", "codigo")]

    def __str__(self):
        return f"{self.codigo} – {self.descricao}"


class ClasseFiscalAliquota(models.Model):
    classe_fiscal = models.ForeignKey(
        ClasseFiscal, on_delete=models.CASCADE, related_name="aliquotas"
    )
    uf_destino = models.CharField(max_length=2)
    icms_interno = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    icms_interestadual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    icms_importado = models.DecimalField(max_digits=5, decimal_places=2, default=4)
    reducao_base_icms = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tem_st = models.BooleanField(default=False)
    mva_original = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    mva_ajustado = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    pauta_fiscal = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    icms_st = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fcp = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fcpst = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fcp_retido = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tem_difal = models.BooleanField(default=False)
    difal_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    ipi = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    pis = models.DecimalField(max_digits=5, decimal_places=2, default=0.65)
    cofins = models.DecimalField(max_digits=5, decimal_places=2, default=3)
    iss = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Reforma tributária
    ibs = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cbs = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_aliquota = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vigencia_inicio = models.DateField()
    vigencia_fim = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financeiro_classe_fiscal_aliquotas"
        verbose_name = "Alíquota fiscal"
        verbose_name_plural = "Alíquotas fiscais"
        indexes = [
            models.Index(fields=["classe_fiscal", "uf_destino", "vigencia_inicio"]),
        ]
        ordering = ["-vigencia_inicio"]


class NaturezaOperacao(ActiveModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    descricao = models.CharField(max_length=100)
    tipo = models.CharField(max_length=30)
    cfop_dentro_estado = models.CharField(max_length=5)
    cfop_fora_estado = models.CharField(max_length=5, blank=True)
    cfop_exportacao = models.CharField(max_length=5, blank=True)
    gera_nfe = models.BooleanField(default=True)
    gera_nfce = models.BooleanField(default=False)
    movimenta_estoque = models.BooleanField(default=True)
    movimenta_financeiro = models.BooleanField(default=True)
    tipo_movimentacao_estoque = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financeiro_naturezas_operacao"
        verbose_name = "Natureza de operação"
        verbose_name_plural = "Naturezas de operação"

    def __str__(self):
        return f"{self.descricao} ({self.cfop_dentro_estado})"


# Documentos que já existem fora deste sistema. Apagar a linha local não
# desfaz nada na SEFAZ -- só faz o ERP discordar do Fisco, e ainda libera o
# número para ser reusado, que é exatamente o que "inutilizada" e "cancelada"
# existem para impedir.
STATUS_IRREVERSIVEIS_NA_SEFAZ = (
    StatusDocumentoFiscal.AUTORIZADA,
    StatusDocumentoFiscal.CANCELADA,
    StatusDocumentoFiscal.DENEGADA,
    StatusDocumentoFiscal.INUTILIZADA,
)


class DocumentoFiscalQuerySet(models.QuerySet):
    """
    Recusa apagar em massa o que já chegou à SEFAZ.

    O GUARDA PRECISA ESTAR AQUI, e não só no `delete()` do model: o Django não
    chama `Model.delete()` num `queryset.delete()`, então um
    `.filter(...).delete()` passaria por cima da regra sem nem tocá-la.
    """

    def delete(self, *args, **kwargs):
        protegidos = self.filter(status__in=STATUS_IRREVERSIVEIS_NA_SEFAZ)
        primeiro = protegidos.first()
        if primeiro is not None:
            raise DocumentoFiscalProtegidoError(
                f'{protegidos.count()} documento(s) fiscal(is) deste conjunto '
                f'já existem na SEFAZ (o primeiro é {primeiro}). Cancele na '
                'SEFAZ; as linhas aqui ficam como histórico.'
            )
        return super().delete(*args, **kwargs)


class DocumentoFiscalManager(
    FilialAwareManager.from_queryset(DocumentoFiscalQuerySet)
):
    """O manager de filial, agora também com o guarda de exclusão."""


class DocumentoFiscal(TimestampedModel):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="documentos_fiscais")
    tipo_documento = models.CharField(max_length=10, choices=TipoDocumentoFiscal.choices)
    origem_tipo = models.CharField(max_length=30, blank=True)
    origem_id = models.BigIntegerField(null=True, blank=True)
    numero = models.BigIntegerField()
    serie = models.PositiveSmallIntegerField()
    chave = models.CharField(max_length=44, unique=True, null=True, blank=True)

    natureza_operacao = models.ForeignKey(
        NaturezaOperacao, on_delete=models.SET_NULL, null=True, blank=True,
    )
    natureza_operacao_descricao = models.CharField(max_length=100, blank=True)
    tipo_operacao = models.CharField(max_length=1, blank=True)
    finalidade_nfe = models.PositiveSmallIntegerField(null=True, blank=True)
    indicador_destino = models.PositiveSmallIntegerField(null=True, blank=True)
    indicador_consumidor_final = models.PositiveSmallIntegerField(null=True, blank=True)
    presenca_comprador = models.PositiveSmallIntegerField(null=True, blank=True)

    emitente_cnpj = models.CharField(max_length=14)
    destinatario_tipo = models.CharField(max_length=10, blank=True)
    destinatario_id = models.BigIntegerField(null=True, blank=True)
    destinatario_snapshot = models.JSONField()

    transportadora = models.ForeignKey(Transportadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='documentos_transportadora')
    veiculo = models.ForeignKey('cadastros.Transportadora', on_delete=models.SET_NULL, null=True, blank=True, related_name='documentos_veiculo')
    modalidade_frete = models.PositiveSmallIntegerField(null=True, blank=True)

    valor_produtos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_frete = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_seguro = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_outras_despesas = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ipi_devolucao = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_st = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_fcp = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_fcpst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ibs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_cbs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_is = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)

    status = models.CharField(
        max_length=20, choices=StatusDocumentoFiscal.choices,
        default=StatusDocumentoFiscal.PENDENTE,
    )
    codigo_status_sefaz = models.CharField(max_length=3, blank=True)
    mensagem_sefaz = models.TextField(blank=True)
    protocolo = models.CharField(max_length=20, blank=True)

    # O QUE ESTE DOCUMENTO VAI TRANSMITIR, guardado quando ele foi conferido
    # e numerado.
    #
    # A EMISSAO E A TRANSMISSAO SAO DOIS MOMENTOS, e entre eles a operacao
    # continua andando: a venda ganha um item, o retorno e' recontado, a
    # carga muda. Remontar o payload na hora de transmitir mandaria para a
    # SEFAZ numeros diferentes dos que o ERP registrou nesta nota -- e a
    # divergencia so' apareceria no XML autorizado.
    payload_envio = models.JSONField(default=dict, blank=True)

    xml_enviado = models.TextField(blank=True)
    xml_assinado = models.TextField(blank=True)
    xml_retorno = models.TextField(blank=True)
    xml_cancelamento = models.TextField(blank=True)
    xml_carta_correcao = models.TextField(blank=True)
    pdf_danfe_url = models.URLField(max_length=500, blank=True)

    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True)
    tentativas_envio = models.PositiveSmallIntegerField(default=0)

    data_emissao = models.DateTimeField()
    data_entrada_saida = models.DateTimeField(null=True, blank=True)
    data_autorizacao = models.DateTimeField(null=True, blank=True)
    data_cancelamento = models.DateTimeField(null=True, blank=True)

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="documentos_fiscais")

    objects = DocumentoFiscalManager()

    class Meta:
        db_table = "documentos_fiscais"
        verbose_name = "Documento fiscal"
        verbose_name_plural = "Documentos fiscais"
        ordering = ["-data_emissao"]
        indexes = [
            models.Index(fields=["filial", "tipo_documento", "status"]),
            models.Index(fields=["chave"]),
            models.Index(fields=["origem_tipo", "origem_id"]),
        ]

    def __str__(self):
        return f"{self.tipo_documento.upper()} {self.numero}/{self.serie}"

    def delete(self, *args, **kwargs):
        """
        Documento que chegou à SEFAZ não se apaga.

        Uma NF-e autorizada existe nos registros do Fisco. Apagar a linha daqui
        não a desfaz lá -- só faz o ERP contar uma história diferente da que o
        Fisco tem. O caminho para desfazer é o cancelamento ou a carta de
        correção, e os dois preservam o documento.
        """
        if self.status in STATUS_IRREVERSIVEIS_NA_SEFAZ:
            raise DocumentoFiscalProtegidoError(
                f'{self} está {self.get_status_display().lower()} na SEFAZ e '
                'não pode ser excluído. Cancele na SEFAZ; a linha aqui fica '
                'como histórico.'
            )
        return super().delete(*args, **kwargs)


class ItemDocumentoFiscal(models.Model):
    documento_fiscal = models.ForeignKey(
        DocumentoFiscal, on_delete=models.CASCADE, related_name="itens"
    )
    produto = models.ForeignKey(
        "produtos.Produto", on_delete=models.SET_NULL, null=True, blank=True,
    )
    numero_item = models.PositiveSmallIntegerField()
    codigo_produto = models.CharField(max_length=30)
    descricao = models.CharField(max_length=150)
    ncm = models.CharField(max_length=8)
    cest = models.CharField(max_length=7, blank=True)
    cfop = models.CharField(max_length=5)
    unidade = models.CharField(max_length=6)
    origem_produto = models.PositiveSmallIntegerField()

    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    valor_bruto = models.DecimalField(max_digits=14, decimal_places=2)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_acrescimo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)

    # ICMS
    cst_icms = models.CharField(max_length=3, blank=True)
    modalidade_bc_icms = models.PositiveSmallIntegerField(null=True, blank=True)
    reducao_bc_icms = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    base_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_icms = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms_desonerado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    motivo_desoneracao = models.PositiveSmallIntegerField(null=True, blank=True)

    # ST
    modalidade_bc_st = models.PositiveSmallIntegerField(null=True, blank=True)
    mva_st = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    reducao_bc_st = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    base_st = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_st = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_st = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    base_st_retido = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_st_retido = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms_substituto = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # FCP
    base_fcp = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_fcp = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_fcp = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    base_fcpst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_fcpst = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_fcpst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_fcpst_retido = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # DIFAL
    base_difal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_difal_origem = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    aliquota_difal_destino = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_difal_uf_destino = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_difal_uf_origem = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # IPI
    cst_ipi = models.CharField(max_length=2, blank=True)
    codigo_enquadramento_ipi = models.CharField(max_length=3, blank=True)
    base_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_ipi = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    quantidade_total_ipi = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    valor_por_unidade_ipi = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    # PIS
    cst_pis = models.CharField(max_length=2, blank=True)
    base_pis = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_pis_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    aliquota_pis_reais = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # COFINS
    cst_cofins = models.CharField(max_length=2, blank=True)
    base_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_cofins_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    aliquota_cofins_reais = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # SN / ISS
    aliquota_credito_sn = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_credito_sn = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_iss = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_iss = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Reforma tributária
    base_ibs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_ibs = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_ibs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    base_cbs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_cbs = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_cbs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    base_is = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    aliquota_is = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_is = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Rastreabilidade
    lote = models.ForeignKey(
        "estoque.LoteProduto", on_delete=models.SET_NULL, null=True, blank=True,
    )
    numero_lote = models.CharField(max_length=60, blank=True)
    data_validade_produto = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "itens_documento_fiscal"
        verbose_name = "Item de documento fiscal"
        verbose_name_plural = "Itens de documento fiscal"
        ordering = ["documento_fiscal", "numero_item"]


class NFEDadosExportacao(models.Model):
    documento_fiscal = models.OneToOneField(
        DocumentoFiscal, on_delete=models.CASCADE, related_name="dados_exportacao"
    )
    uf_embarque = models.CharField(max_length=2)
    local_embarque = models.CharField(max_length=60)
    local_despacho = models.CharField(max_length=60, blank=True)
    ato_concessorio_drawback = models.CharField(max_length=20, blank=True)
    numero_registro_exportacao = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "nfe_dados_exportacao"


class NFEDadosImportacao(models.Model):
    documento_fiscal = models.ForeignKey(DocumentoFiscal, on_delete=models.CASCADE,
                                          related_name="dados_importacao")
    numero_di = models.CharField(max_length=10)
    data_registro = models.DateField()
    local_desembaraco = models.CharField(max_length=60)
    uf_desembaraco = models.CharField(max_length=2)
    data_desembaraco = models.DateField()
    via_transporte = models.PositiveSmallIntegerField()
    valor_afrmm = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    forma_importacao = models.CharField(max_length=1, blank=True)
    cnpj_adquirente = models.CharField(max_length=14, blank=True)
    codigo_exportador = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "nfe_dados_importacao"


class NFSEConfiguracaoMunicipio(ActiveModel):
    filial = models.ForeignKey(Filial, on_delete=models.CASCADE)
    codigo_municipio_ibge = models.CharField(max_length=7)
    nome_municipio = models.CharField(max_length=80, blank=True)
    provedor = models.CharField(max_length=40, blank=True)
    codigo_acesso = models.CharField(max_length=100, blank=True)
    senha_hash = models.CharField(max_length=255, blank=True)
    certificado_path = models.CharField(max_length=500, blank=True)
    aliquota_iss = models.DecimalField(max_digits=5, decimal_places=2)
    codigo_servico_padrao = models.CharField(max_length=10, blank=True)
    ambiente = models.PositiveSmallIntegerField(default=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "nfse_configuracao_municipios"


class CartaCorrecao(models.Model):
    documento_fiscal = models.ForeignKey(DocumentoFiscal, on_delete=models.CASCADE,
                                          related_name="cartas_correcao")
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    numero_sequencial = models.PositiveSmallIntegerField()
    texto_correcao = models.TextField()
    status = models.CharField(max_length=20, default="pendente")
    protocolo = models.CharField(max_length=20, blank=True)
    xml_retorno = models.TextField(blank=True)
    data_envio = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cartas_correcao"
        ordering = ["-created_at"]


class InutilizacaoNumeracao(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    tipo_documento = models.CharField(max_length=10)
    serie = models.PositiveSmallIntegerField()
    numero_inicial = models.BigIntegerField()
    numero_final = models.BigIntegerField()
    justificativa = models.TextField()
    status = models.CharField(max_length=20, default="pendente")
    protocolo = models.CharField(max_length=20, blank=True)
    xml_retorno = models.TextField(blank=True)
    data_inutilizacao = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inutilizacoes_numeracao"


class LogIntegracaoFiscal(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs_integracao_fiscal",
    )
    documento_fiscal = models.ForeignKey(
        DocumentoFiscal, on_delete=models.SET_NULL, null=True, blank=True,
    )
    provedor = models.CharField(max_length=20)
    acao = models.CharField(max_length=30)
    endpoint = models.CharField(max_length=200, blank=True)
    request_json = models.TextField(blank=True)
    response_json = models.TextField(blank=True)
    codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)
    codigo_status_sefaz = models.CharField(max_length=3, blank=True)
    sucesso = models.BooleanField(null=True, blank=True)
    tempo_resposta_ms = models.IntegerField(null=True, blank=True)
    tentativa = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "log_integracoes_fiscais"
        ordering = ["-created_at"]


class IdempotenciaFiscal(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.CASCADE)
    chave = models.CharField(max_length=150, unique=True, db_index=True)
    tipo_documento = models.CharField(max_length=10)
    documento_fiscal = models.ForeignKey(
        DocumentoFiscal, on_delete=models.SET_NULL, null=True, blank=True,
    )
    status = models.CharField(max_length=20)
    response_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "idempotencia_fiscal"
        ordering = ["-created_at"]
