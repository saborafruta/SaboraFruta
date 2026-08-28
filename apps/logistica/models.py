from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import FilialScopedModel, TimestampedModel


class RomaneioCarga(FilialScopedModel):
    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        EM_CARREGAMENTO = "em_carregamento", "Em carregamento"
        EM_ROTA = "em_rota", "Em rota"
        ENTREGUE = "entregue", "Entregue"
        CANCELADO = "cancelado", "Cancelado"

    numero = models.PositiveIntegerField(db_index=True)
    data = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="romaneios_carga",
    )
    transportadora = models.ForeignKey(
        "cadastros.Transportadora",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="romaneios_carga",
    )
    motorista_nome = models.CharField(max_length=120, blank=True)
    motorista_documento = models.CharField(max_length=30, blank=True)
    veiculo_placa = models.CharField(max_length=10, blank=True)
    veiculo_descricao = models.CharField(max_length=100, blank=True)
    # A VIAGEM E' A CAMADA DE CIMA. O romaneio responde por ENTREGA -- cada
    # item dele e' um cliente com endereco. Mercadoria que sai sem comprador
    # nao cabe aqui, e por isso a carga fisica passou a ser da viagem. Fica
    # opcional: romaneio antigo, e romaneio de entrega pura, seguem sem ela.
    viagem = models.ForeignKey(
        'logistica.Viagem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='romaneios',
    )
    origem = models.CharField(max_length=160, blank=True)
    destino_rota = models.CharField(max_length=160, blank=True)
    observacao = models.TextField(blank=True)
    peso_total_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    volume_total = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = "logistica_romaneios_carga"
        ordering = ["-data", "-numero"]
        unique_together = [("filial", "numero")]
        indexes = [
            models.Index(fields=["filial", "status", "-data"]),
            models.Index(fields=["filial", "-numero"]),
        ]
        verbose_name = "Romaneio de Carga"
        verbose_name_plural = "Romaneios de Carga"

    def __str__(self):
        return f"Romaneio #{self.numero:06d}"

    def recalcular_totais(self):
        totais = self.itens.aggregate(
            peso_total=Sum("peso_kg"),
            volume_total=Sum("volumes"),
            valor_total=Sum("valor"),
        )
        self.peso_total_kg = totais["peso_total"] or Decimal("0")
        self.volume_total = totais["volume_total"] or Decimal("0")
        self.valor_total = totais["valor_total"] or Decimal("0")
        self.save(update_fields=["peso_total_kg", "volume_total", "valor_total", "updated_at"])


class ItemRomaneioCarga(TimestampedModel):
    class StatusEntrega(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        CARREGADO = "carregado", "Carregado"
        ENTREGUE = "entregue", "Entregue"
        NAO_ENTREGUE = "nao_entregue", "Nao entregue"
        CANCELADO = "cancelado", "Cancelado"

    romaneio = models.ForeignKey(RomaneioCarga, on_delete=models.CASCADE, related_name="itens")
    venda_pdv = models.ForeignKey(
        "pdv.VendaPDV",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_romaneio",
    )
    pedido_venda = models.ForeignKey(
        "vendas.PedidoVenda",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_romaneio",
    )
    ordem = models.PositiveIntegerField(default=0)
    cliente_nome = models.CharField(max_length=180)
    documento = models.CharField(max_length=60, blank=True)
    endereco_entrega = models.JSONField(default=dict, blank=True)
    status_entrega = models.CharField(
        max_length=30,
        choices=StatusEntrega.choices,
        default=StatusEntrega.PENDENTE,
        db_index=True,
    )
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_itens_romaneio_carga"
        ordering = ["ordem", "id"]
        indexes = [
            models.Index(fields=["romaneio", "status_entrega"]),
            models.Index(fields=["venda_pdv"]),
            models.Index(fields=["pedido_venda"]),
        ]
        verbose_name = "Item do Romaneio"
        verbose_name_plural = "Itens do Romaneio"

    def __str__(self):
        return f"{self.cliente_nome} - {self.romaneio}"


class OrdemColeta(FilialScopedModel):
    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        SOLICITADA = "solicitada", "Solicitada"
        PROGRAMADA = "programada", "Programada"
        COLETADA = "coletada", "Coletada"
        CANCELADA = "cancelada", "Cancelada"

    class TipoSolicitante(models.TextChoices):
        CLIENTE = "cliente", "Cliente"
        FORNECEDOR = "fornecedor", "Fornecedor"
        FILIAL = "filial", "Filial"
        AVULSO = "avulso", "Avulso"

    numero = models.PositiveIntegerField(db_index=True)
    data_solicitacao = models.DateField(default=timezone.localdate, db_index=True)
    data_coleta_prevista = models.DateField(null=True, blank=True, db_index=True)
    data_coleta_realizada = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    tipo_solicitante = models.CharField(
        max_length=20,
        choices=TipoSolicitante.choices,
        default=TipoSolicitante.CLIENTE,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_coleta",
    )
    cliente = models.ForeignKey(
        "cadastros.Cliente",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_coleta",
    )
    fornecedor = models.ForeignKey(
        "cadastros.Fornecedor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_coleta",
    )
    transportadora = models.ForeignKey(
        "cadastros.Transportadora",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_coleta",
    )
    romaneio = models.ForeignKey(
        RomaneioCarga,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_coleta",
    )
    solicitante_nome = models.CharField(max_length=180, blank=True)
    contato_nome = models.CharField(max_length=120, blank=True)
    contato_telefone = models.CharField(max_length=30, blank=True)
    endereco_coleta = models.JSONField(default=dict, blank=True)
    endereco_entrega = models.JSONField(default=dict, blank=True)
    motorista_nome = models.CharField(max_length=120, blank=True)
    veiculo_placa = models.CharField(max_length=10, blank=True)
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_total_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_estimado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_ordens_coleta"
        ordering = ["-data_solicitacao", "-numero"]
        unique_together = [("filial", "numero")]
        indexes = [
            models.Index(fields=["filial", "status", "-data_solicitacao"]),
            models.Index(fields=["filial", "data_coleta_prevista"]),
            models.Index(fields=["cliente"]),
            models.Index(fields=["fornecedor"]),
        ]
        verbose_name = "Ordem de Coleta"
        verbose_name_plural = "Ordens de Coleta"

    def __str__(self):
        return f"Ordem de Coleta #{self.numero:06d}"

    def recalcular_totais(self):
        totais = self.itens.aggregate(
            volumes=Sum("volumes"),
            peso_total=Sum("peso_kg"),
            valor_total=Sum("valor"),
        )
        self.volumes = totais["volumes"] or Decimal("0")
        self.peso_total_kg = totais["peso_total"] or Decimal("0")
        self.valor_estimado = totais["valor_total"] or Decimal("0")
        self.save(update_fields=["volumes", "peso_total_kg", "valor_estimado", "updated_at"])


class ItemOrdemColeta(TimestampedModel):
    ordem = models.ForeignKey(OrdemColeta, on_delete=models.CASCADE, related_name="itens")
    descricao = models.CharField(max_length=220)
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unidade = models.CharField(max_length=10, default="UN")
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_itens_ordem_coleta"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["ordem"]),
        ]
        verbose_name = "Item da Ordem de Coleta"
        verbose_name_plural = "Itens da Ordem de Coleta"

    def __str__(self):
        return f"{self.descricao} - {self.ordem}"


class ManifestoCarga(FilialScopedModel):
    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        EMITIDO = "emitido", "Emitido"
        EM_TRANSITO = "em_transito", "Em trânsito"
        ENCERRADO = "encerrado", "Encerrado"
        CANCELADO = "cancelado", "Cancelado"

    class Modal(models.TextChoices):
        RODOVIARIO = "rodoviario", "Rodoviário"
        AEREO = "aereo", "Aéreo"
        AQUAVIARIO = "aquaviario", "Aquaviário"
        FERROVIARIO = "ferroviario", "Ferroviário"

    numero = models.PositiveIntegerField(db_index=True)
    data_emissao = models.DateField(default=timezone.localdate, db_index=True)
    data_saida = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    modal = models.CharField(max_length=20, choices=Modal.choices, default=Modal.RODOVIARIO)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manifestos_carga",
    )
    romaneio = models.ForeignKey(
        RomaneioCarga,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manifestos_carga",
    )
    transportadora = models.ForeignKey(
        "cadastros.Transportadora",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manifestos_carga",
    )
    motorista_nome = models.CharField(max_length=120, blank=True)
    motorista_documento = models.CharField(max_length=30, blank=True)
    veiculo_placa = models.CharField(max_length=10, blank=True)
    veiculo_descricao = models.CharField(max_length=100, blank=True)
    cidade_origem = models.CharField(max_length=100, blank=True)
    uf_origem = models.CharField(max_length=2, blank=True)
    cidade_destino = models.CharField(max_length=100, blank=True)
    uf_destino = models.CharField(max_length=2, blank=True)
    percurso = models.TextField(blank=True)
    qtd_documentos = models.PositiveIntegerField(default=0)
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_total_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_manifestos_carga"
        ordering = ["-data_emissao", "-numero"]
        unique_together = [("filial", "numero")]
        indexes = [
            models.Index(fields=["filial", "status", "-data_emissao"]),
            models.Index(fields=["filial", "-numero"]),
            models.Index(fields=["romaneio"]),
        ]
        verbose_name = "Manifesto de Carga"
        verbose_name_plural = "Manifestos de Carga"

    def __str__(self):
        return f"Manifesto #{self.numero:06d}"

    def recalcular_totais(self):
        totais = self.documentos.aggregate(
            volumes=Sum("volumes"),
            peso_total=Sum("peso_kg"),
            valor_total=Sum("valor"),
        )
        self.qtd_documentos = self.documentos.count()
        self.volumes = totais["volumes"] or Decimal("0")
        self.peso_total_kg = totais["peso_total"] or Decimal("0")
        self.valor_total = totais["valor_total"] or Decimal("0")
        self.save(update_fields=["qtd_documentos", "volumes", "peso_total_kg", "valor_total", "updated_at"])


class CTe(FilialScopedModel):
    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        EM_DIGITACAO = "em_digitacao", "Em digitacao"
        AUTORIZADO = "autorizado", "Autorizado"
        CANCELADO = "cancelado", "Cancelado"
        DENEGADO = "denegado", "Denegado"

    class Modal(models.TextChoices):
        RODOVIARIO = "rodoviario", "Rodoviario"
        AEREO = "aereo", "Aereo"
        AQUAVIARIO = "aquaviario", "Aquaviario"
        FERROVIARIO = "ferroviario", "Ferroviario"
        DUTOVIARIO = "dutoviario", "Dutoviario"
        MULTIMODAL = "multimodal", "Multimodal"

    class TipoCTe(models.TextChoices):
        NORMAL = "normal", "Normal"
        COMPLEMENTO = "complemento", "Complemento de valores"
        ANULACAO = "anulacao", "Anulacao de valores"
        SUBSTITUTO = "substituto", "Substituto"

    class Tomador(models.TextChoices):
        REMETENTE = "remetente", "Remetente"
        DESTINATARIO = "destinatario", "Destinatario"
        EXPEDIDOR = "expedidor", "Expedidor"
        RECEBEDOR = "recebedor", "Recebedor"
        OUTROS = "outros", "Outros"

    numero = models.PositiveIntegerField(db_index=True)
    numero_cte = models.CharField(max_length=20, blank=True, db_index=True)
    serie = models.CharField(max_length=10, blank=True, default="1")
    chave_acesso = models.CharField(max_length=44, blank=True, db_index=True)
    data_emissao = models.DateField(default=timezone.localdate, db_index=True)
    data_saida = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    modal = models.CharField(max_length=20, choices=Modal.choices, default=Modal.RODOVIARIO)
    tipo_cte = models.CharField(max_length=20, choices=TipoCTe.choices, default=TipoCTe.NORMAL)
    cfop = models.CharField(max_length=10, blank=True)
    natureza_operacao = models.CharField(max_length=120, blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ctes",
    )
    transportadora = models.ForeignKey(
        "cadastros.Transportadora",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ctes",
    )
    tomador = models.CharField(max_length=20, choices=Tomador.choices, default=Tomador.REMETENTE)
    remetente_nome = models.CharField(max_length=180, blank=True)
    remetente_documento = models.CharField(max_length=20, blank=True)
    destinatario_nome = models.CharField(max_length=180, blank=True)
    destinatario_documento = models.CharField(max_length=20, blank=True)
    cidade_origem = models.CharField(max_length=100, blank=True)
    uf_origem = models.CharField(max_length=2, blank=True)
    cidade_destino = models.CharField(max_length=100, blank=True)
    uf_destino = models.CharField(max_length=2, blank=True)
    percurso = models.TextField(blank=True)
    motorista_nome = models.CharField(max_length=120, blank=True)
    motorista_documento = models.CharField(max_length=30, blank=True)
    veiculo_placa = models.CharField(max_length=10, blank=True)
    veiculo_descricao = models.CharField(max_length=100, blank=True)
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_total_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_carga = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_frete = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pedagio = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_outros = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    protocolo_autorizacao = models.CharField(max_length=60, blank=True)
    data_autorizacao = models.DateTimeField(null=True, blank=True)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_cte"
        ordering = ["-data_emissao", "-numero"]
        unique_together = [("filial", "numero")]
        indexes = [
            models.Index(fields=["filial", "status", "-data_emissao"]),
            models.Index(fields=["filial", "-numero"]),
            models.Index(fields=["chave_acesso"]),
        ]
        verbose_name = "CT-e"
        verbose_name_plural = "CT-es"

    def __str__(self):
        return f"CT-e #{self.numero:06d}"

    def recalcular_totais(self):
        totais = self.documentos.aggregate(
            volumes=Sum("volumes"),
            peso_total=Sum("peso_kg"),
            valor_docs=Sum("valor"),
        )
        self.volumes = totais["volumes"] or Decimal("0")
        self.peso_total_kg = totais["peso_total"] or Decimal("0")
        self.valor_carga = totais["valor_docs"] or Decimal("0")
        self.valor_total = (
            (self.valor_frete or Decimal("0"))
            + (self.valor_pedagio or Decimal("0"))
            + (self.valor_outros or Decimal("0"))
        )
        self.save(update_fields=["volumes", "peso_total_kg", "valor_carga", "valor_total", "updated_at"])


class DocumentoCTe(TimestampedModel):
    class TipoDocumento(models.TextChoices):
        NFE = "nfe", "NF-e"
        NFCE = "nfce", "NFC-e"
        CTE = "cte", "CT-e anterior"
        NFSE = "nfse", "NFS-e"
        OUTRO = "outro", "Outro"

    cte = models.ForeignKey(CTe, on_delete=models.CASCADE, related_name="documentos")
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices, default=TipoDocumento.NFE)
    numero_documento = models.CharField(max_length=60)
    serie = models.CharField(max_length=20, blank=True)
    chave_acesso = models.CharField(max_length=80, blank=True, db_index=True)
    emitente_nome = models.CharField(max_length=180, blank=True)
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_documentos_cte"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["cte"]),
            models.Index(fields=["tipo_documento", "numero_documento"]),
        ]
        verbose_name = "Documento do CT-e"
        verbose_name_plural = "Documentos do CT-e"

    def __str__(self):
        return f"{self.get_tipo_documento_display()} {self.numero_documento}"


class PedidoExpedicao(FilialScopedModel):
    """OMS — Pedido Gerado para Expedição."""

    class Status(models.TextChoices):
        ABERTO = "aberto", "Aberto"
        EM_SEPARACAO = "em_separacao", "Em Separação"
        SEPARADO = "separado", "Separado"
        EXPEDIDO = "expedido", "Expedido"
        ENTREGUE = "entregue", "Entregue"
        CANCELADO = "cancelado", "Cancelado"

    class Prioridade(models.TextChoices):
        NORMAL = "normal", "Normal"
        ALTA = "alta", "Alta"
        URGENTE = "urgente", "Urgente"

    numero = models.PositiveIntegerField(db_index=True)
    data_pedido = models.DateField(default=timezone.localdate, db_index=True)
    data_previsao_entrega = models.DateField(null=True, blank=True, db_index=True)
    data_expedicao = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTO, db_index=True)
    prioridade = models.CharField(max_length=10, choices=Prioridade.choices, default=Prioridade.NORMAL)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pedidos_expedicao",
    )
    cliente = models.ForeignKey(
        "cadastros.Cliente",
        on_delete=models.PROTECT,
        related_name="pedidos_expedicao",
    )
    # A VENDA QUE ORIGINOU ESTA EXPEDIÇÃO, quando existe.
    #
    # Sem ela, quem monta o pedido redigita cliente, endereço e itens que já
    # estão na venda — e cada redigitação é uma chance de entregar para o
    # cliente errado. Com ela, a pergunta "esta carga é de qual venda?" tem
    # resposta no sistema, e não na memória de quem carregou.
    #
    # OPCIONAL DE PROPÓSITO: carga avulsa, amostra e reposição existem e não
    # nascem de pedido nenhum. Exigir a venda faria essas cargas saírem por
    # fora do sistema, que é o oposto do que se quer.
    pedido_venda = models.ForeignKey(
        "vendas.PedidoVenda",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pedidos_expedicao",
        help_text="Pedido de venda que originou esta expedição, quando houver.",
    )
    transportadora = models.ForeignKey(
        "cadastros.Transportadora",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pedidos_expedicao",
    )
    romaneio = models.ForeignKey(
        RomaneioCarga,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pedidos_expedicao",
    )
    contato_nome = models.CharField(max_length=120, blank=True)
    contato_telefone = models.CharField(max_length=30, blank=True)
    endereco_entrega = models.JSONField(default=dict, blank=True)
    motorista_nome = models.CharField(max_length=120, blank=True)
    veiculo_placa = models.CharField(max_length=10, blank=True)
    # Totais calculados
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_total_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_pedidos_expedicao"
        ordering = ["-data_pedido", "-numero"]
        unique_together = [("filial", "numero")]
        indexes = [
            models.Index(fields=["filial", "status", "-data_pedido"]),
            models.Index(fields=["filial", "-numero"]),
            models.Index(fields=["cliente"]),
            models.Index(fields=["filial", "data_previsao_entrega"]),
        ]
        verbose_name = "Pedido de Expedição"
        verbose_name_plural = "Pedidos de Expedição"

    def __str__(self):
        return f"Pedido #{self.numero:06d}"

    def recalcular_totais(self):
        from decimal import Decimal as D
        totais = self.itens.aggregate(
            volumes=Sum("volumes"),
            peso=Sum("peso_kg"),
            valor=Sum("valor_total"),
        )
        self.volumes = totais["volumes"] or D("0")
        self.peso_total_kg = totais["peso"] or D("0")
        self.valor_total = totais["valor"] or D("0")
        self.save(update_fields=["volumes", "peso_total_kg", "valor_total", "updated_at"])


class ItemPedidoExpedicao(TimestampedModel):
    class StatusItem(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        SEPARADO = "separado", "Separado"
        EXPEDIDO = "expedido", "Expedido"
        CANCELADO = "cancelado", "Cancelado"

    pedido = models.ForeignKey(PedidoExpedicao, on_delete=models.CASCADE, related_name="itens")
    ordem = models.PositiveIntegerField(default=0)
    produto_codigo = models.CharField(max_length=60, blank=True)
    produto_nome = models.CharField(max_length=220)
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unidade = models.CharField(max_length=10, default="UN")
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status_item = models.CharField(
        max_length=20, choices=StatusItem.choices, default=StatusItem.PENDENTE, db_index=True
    )
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_itens_pedido_expedicao"
        ordering = ["ordem", "id"]
        indexes = [
            models.Index(fields=["pedido"]),
            models.Index(fields=["pedido", "status_item"]),
        ]
        verbose_name = "Item do Pedido de Expedição"
        verbose_name_plural = "Itens do Pedido de Expedição"

    def __str__(self):
        return f"{self.produto_nome} - {self.pedido}"

    def save(self, *args, **kwargs):
        # Recalcula valor_total do item automaticamente
        from decimal import Decimal as D
        self.valor_total = (self.valor_unitario or D("0")) * (self.quantidade or D("0"))
        super().save(*args, **kwargs)


class MDFe(FilialScopedModel):
    """MDF-e — Manifesto Eletrônico de Documentos Fiscais."""

    class Status(models.TextChoices):
        """
        O ciclo do manifesto, no vocabulário de quem opera.

        OS VALORES NÃO MUDAM — só os rótulos. Trocar o valor exigiria migrar
        dado e reescrever consulta em meia dúzia de lugares para ganhar
        nada: quem lê a tela lê o rótulo.

        "Aguardando emissão" é o manifesto que espera a autorização das
        NF-e que ele ampara — o MDF-e não pode ser transmitido antes delas.
        """

        RASCUNHO = "rascunho", "Não emitido"
        AGUARDANDO_NFE = "aguardando_nfe", "Aguardando emissão"
        PROCESSANDO = "processando", "Enviando"
        AUTORIZADO = "autorizado", "Autorizado"
        REJEITADO = "rejeitado", "Rejeitado"
        ENCERRADO = "encerrado", "Encerrado"
        CANCELADO = "cancelado", "Cancelado"

    class Modal(models.TextChoices):
        RODOVIARIO = "rodoviario", "Rodoviário"
        AEREO = "aereo", "Aéreo"
        AQUAVIARIO = "aquaviario", "Aquaviário"
        FERROVIARIO = "ferroviario", "Ferroviário"

    numero = models.PositiveIntegerField(db_index=True)
    serie = models.CharField(max_length=3, default="1", blank=True)
    chave_acesso = models.CharField(max_length=44, blank=True, db_index=True)
    data_emissao = models.DateField(default=timezone.localdate, db_index=True)
    data_encerramento = models.DateField(null=True, blank=True)
    data_hora_inicio_viagem = models.DateTimeField(null=True, blank=True)
    data_hora_previsao_fim = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    modal = models.CharField(max_length=20, choices=Modal.choices, default=Modal.RODOVIARIO)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mdfes",
    )
    transportadora = models.ForeignKey(
        "cadastros.Transportadora",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mdfes",
    )
    viagem = models.ForeignKey(
        'logistica.Viagem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mdfes',
        help_text='A viagem que este manifesto ampara. Um MDF-e por viagem.',
    )
    romaneio = models.ForeignKey(
        RomaneioCarga,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mdfes",
    )
    # Motorista e veículo
    motorista_nome = models.CharField(max_length=120, blank=True)
    motorista_cpf = models.CharField(max_length=14, blank=True)
    motorista_cnh = models.CharField(max_length=20, blank=True)
    veiculo_placa = models.CharField(max_length=10, blank=True)
    veiculo_rntrc = models.CharField(max_length=20, blank=True, help_text="RNTRC do veículo")
    veiculo_descricao = models.CharField(max_length=100, blank=True)
    # Origem e percurso
    uf_carregamento = models.CharField(max_length=2, blank=True)
    municipio_carregamento = models.CharField(max_length=100, blank=True)
    percurso_ufs = models.CharField(
        max_length=200,
        blank=True,
        help_text="UFs do percurso separadas por vírgula. Ex: SP,RJ,MG",
    )
    uf_descarregamento = models.CharField(max_length=2, blank=True)
    municipio_descarregamento = models.CharField(max_length=100, blank=True)
    # Totais
    qtd_ctes = models.PositiveIntegerField(default=0)
    qtd_nfes = models.PositiveIntegerField(default=0)
    peso_total_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Autorização
    protocolo_autorizacao = models.CharField(max_length=60, blank=True)
    data_autorizacao = models.DateTimeField(null=True, blank=True)
    data_cancelamento = models.DateTimeField(null=True, blank=True)
    mensagem_sefaz = models.TextField(blank=True)
    justificativa_cancelamento = models.TextField(blank=True)
    codigo_municipio_carregamento = models.CharField(max_length=7, blank=True)
    codigo_municipio_descarregamento = models.CharField(max_length=7, blank=True)
    transporte_metadados = models.JSONField(default=dict, blank=True)
    documento_fiscal = models.OneToOneField(
        "financeiro.DocumentoFiscal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mdfe_logistico",
    )
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_mdfe"
        ordering = ["-data_emissao", "-numero"]
        unique_together = [("filial", "numero")]
        indexes = [
            models.Index(fields=["filial", "status", "-data_emissao"]),
            models.Index(fields=["filial", "-numero"]),
            models.Index(fields=["chave_acesso"]),
        ]
        verbose_name = "MDF-e"
        verbose_name_plural = "MDF-es"

    def __str__(self):
        return f"MDF-e #{self.numero:06d}"

    def recalcular_totais(self):
        from decimal import Decimal as D
        totais = self.documentos.aggregate(
            peso=Sum("peso_kg"),
            valor=Sum("valor"),
        )
        self.qtd_ctes = self.documentos.filter(tipo_documento="cte").count()
        self.qtd_nfes = self.documentos.filter(tipo_documento="nfe").count()
        self.peso_total_kg = totais["peso"] or D("0")
        self.valor_total = totais["valor"] or D("0")
        self.save(update_fields=["qtd_ctes", "qtd_nfes", "peso_total_kg", "valor_total", "updated_at"])


class DocumentoMDFe(TimestampedModel):
    class TipoDocumento(models.TextChoices):
        CTE = "cte", "CT-e"
        NFE = "nfe", "NF-e"
        NFCE = "nfce", "NFC-e"
        OUTRO = "outro", "Outro"

    mdfe = models.ForeignKey(MDFe, on_delete=models.CASCADE, related_name="documentos")
    documento_fiscal = models.ForeignKey(
        "financeiro.DocumentoFiscal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vinculos_mdfe",
    )
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices, default=TipoDocumento.CTE)
    chave_acesso = models.CharField(max_length=44, blank=True, db_index=True)
    numero_documento = models.CharField(max_length=60, blank=True)
    serie = models.CharField(max_length=10, blank=True)
    emitente_nome = models.CharField(max_length=180, blank=True)
    emitente_documento = models.CharField(max_length=20, blank=True)
    municipio_descarga = models.CharField(max_length=100, blank=True)
    uf_descarga = models.CharField(max_length=2, blank=True)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_documentos_mdfe"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["mdfe"]),
            models.Index(fields=["tipo_documento", "numero_documento"]),
            models.Index(fields=["chave_acesso"]),
        ]
        verbose_name = "Documento do MDF-e"
        verbose_name_plural = "Documentos do MDF-e"

    def __str__(self):
        return f"{self.get_tipo_documento_display()} {self.numero_documento}"


class DocumentoManifestoCarga(TimestampedModel):
    class TipoDocumento(models.TextChoices):
        NFE = "nfe", "NF-e"
        NFCE = "nfce", "NFC-e"
        CTE = "cte", "CT-e"
        NFSE = "nfse", "NFS-e"
        OUTRO = "outro", "Outro"

    manifesto = models.ForeignKey(ManifestoCarga, on_delete=models.CASCADE, related_name="documentos")
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices, default=TipoDocumento.NFE)
    numero_documento = models.CharField(max_length=60)
    serie = models.CharField(max_length=20, blank=True)
    chave_acesso = models.CharField(max_length=80, blank=True, db_index=True)
    remetente_nome = models.CharField(max_length=180, blank=True)
    destinatario_nome = models.CharField(max_length=180, blank=True)
    cidade_origem = models.CharField(max_length=100, blank=True)
    uf_origem = models.CharField(max_length=2, blank=True)
    cidade_destino = models.CharField(max_length=100, blank=True)
    uf_destino = models.CharField(max_length=2, blank=True)
    volumes = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = "logistica_documentos_manifesto_carga"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["manifesto"]),
            models.Index(fields=["tipo_documento", "numero_documento"]),
        ]
        verbose_name = "Documento do Manifesto"
        verbose_name_plural = "Documentos do Manifesto"

    def __str__(self):
        return f"{self.get_tipo_documento_display()} {self.numero_documento}"


# Registrados aqui para o Django encontra-los; o codigo deles mora em
# `models_viagem.py`, que e' um assunto proprio e grande demais para este
# arquivo.
from apps.logistica.models_viagem import (  # noqa: E402,F401
    ItemCarga, ItemVendaViagem, SaldoCarga, Viagem, VendaViagem,
)
from apps.logistica.models_bonificacao import (  # noqa: E402,F401
    ComprovanteBonificacao, EntregaBonificacao,
)
