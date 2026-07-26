from django.db import models


class TipoFormaPagamento(models.TextChoices):
    DINHEIRO = "dinheiro", "Dinheiro"
    PIX = "pix", "PIX"
    BOLETO = "boleto", "Boleto"
    CARTAO_DEBITO = "cartao_debito", "Cartão de Débito"
    CARTAO_CREDITO = "cartao_credito", "Cartão de Crédito"
    CHEQUE = "cheque", "Cheque"
    TED = "ted", "TED"
    DOC = "doc", "DOC"
    VALE = "vale", "Vale"
    CONVENIO = "convenio", "Convênio"
    CREDIARIO = "crediario", "Crediário"
    CASHBACK = "cashback", "Cashback"


class StatusContaReceber(models.TextChoices):
    ABERTO = "aberto", "Aberto"
    PAGO = "pago", "Pago"
    VENCIDO = "vencido", "Vencido"
    CANCELADO = "cancelado", "Cancelado"
    NEGOCIADO = "negociado", "Negociado"
    DEVOLVIDO = "devolvido", "Devolvido"


class StatusContaPagar(models.TextChoices):
    ABERTO = "aberto", "Aberto"
    PAGO = "pago", "Pago"
    VENCIDO = "vencido", "Vencido"
    CANCELADO = "cancelado", "Cancelado"
    AGENDADO = "agendado", "Agendado"


class TipoDocumentoFiscal(models.TextChoices):
    NFE = "nfe", "NF-e"
    NFCE = "nfce", "NFC-e"
    NFSE = "nfse", "NFS-e"
    CTE = "cte", "CT-e"
    MDFE = "mdfe", "MDF-e"


class StatusDocumentoFiscal(models.TextChoices):
    PENDENTE = "pendente", "Pendente"
    PROCESSANDO = "processando", "Processando"
    AUTORIZADA = "autorizada", "Autorizada"
    REJEITADA = "rejeitada", "Rejeitada"
    CANCELADA = "cancelada", "Cancelada"
    DENEGADA = "denegada", "Denegada"
    INUTILIZADA = "inutilizada", "Inutilizada"


class StatusPIX(models.TextChoices):
    PENDENTE = "pendente", "Pendente"
    PAGO = "pago", "Pago"
    EXPIRADO = "expirado", "Expirado"
    CANCELADO = "cancelado", "Cancelado"


class TipoMovimentacaoCaixa(models.TextChoices):
    ABERTURA = "abertura", "Abertura"
    FECHAMENTO = "fechamento", "Fechamento"
    SANGRIA = "sangria", "Sangria"
    SUPRIMENTO = "suprimento", "Suprimento"
    VENDA = "venda", "Venda"
    CANCELAMENTO_VENDA = "cancelamento_venda", "Cancelamento de venda"
    DEVOLUCAO = "devolucao", "Devolução"
    TROCO = "troco", "Troco"
    TEF_ENTRADA = "tef_entrada", "TEF entrada"
    TEF_SAIDA = "tef_saida", "TEF saída"
