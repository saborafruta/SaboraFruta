"""
Cache do padrão de recompra de cada cliente.

A tabela `RecompraCliente` é resultado pré-calculado, nunca fonte da
verdade: tudo aqui é derivado do histórico de PedidoVenda + VendaPDV
pelo `RecompraService`. Ela existe porque a tela precisa filtrar e
ordenar milhares de clientes por dias de atraso e score — o que seria
inviável recalculando a cada request.
"""
from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


class RecompraCliente(FilialScopedModel):
    """Padrão de compra de um cliente numa filial, recalculado periodicamente."""

    class Frequencia(models.TextChoices):
        SEMANAL = 'semanal', 'Compra semanal'
        QUINZENAL = 'quinzenal', 'Compra quinzenal'
        MENSAL = 'mensal', 'Compra mensal'
        PERSONALIZADA = 'personalizada', 'Compra personalizada'
        SEM_PADRAO = 'sem_padrao', 'Padrão insuficiente'

    class Status(models.TextChoices):
        VERDE = 'verde', 'Em dia'
        AMARELO = 'amarelo', 'Próximo da recompra'
        VERMELHO = 'vermelho', 'Em atraso'
        CINZA = 'cinza', 'Sem histórico suficiente'

    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.CASCADE, related_name='recompras',
    )
    representante = models.ForeignKey(
        'cadastros.Representante', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recompras_clientes',
        help_text='Representante do pedido mais recente do cliente.',
    )

    # Padrão detectado
    media_intervalo_dias = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    desvio_padrao_dias = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    qtd_compras = models.PositiveIntegerField(default=0)
    frequencia = models.CharField(
        max_length=20, choices=Frequencia.choices, default=Frequencia.SEM_PADRAO, db_index=True,
    )

    # Previsão
    primeira_compra = models.DateField(null=True, blank=True)
    ultima_compra = models.DateField(null=True, blank=True)
    proxima_compra_prevista = models.DateField(null=True, blank=True)
    dias_restantes = models.IntegerField(
        null=True, blank=True,
        help_text='Negativo = já passou da data prevista.',
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.CINZA, db_index=True,
    )

    # Valores
    valor_medio = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total_periodo = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Priorização
    score = models.PositiveSmallIntegerField(
        default=0, help_text='0-100. Quanto maior, mais vale a pena contatar agora.',
    )
    nivel_confianca = models.DecimalField(
        max_digits=4, decimal_places=3, default=0,
        help_text='0-1. Quão regular é o cliente (baixo desvio = alta confiança).',
    )

    ultima_atualizacao = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = 'crm_recompra_cliente'
        verbose_name = 'Recompra do Cliente'
        verbose_name_plural = 'Recompras dos Clientes'
        ordering = ['-score']
        constraints = [
            models.UniqueConstraint(
                fields=['cliente', 'filial'], name='uniq_recompra_cliente_filial',
            ),
        ]
        indexes = [
            models.Index(fields=['filial', 'status', '-score']),
            models.Index(fields=['filial', 'proxima_compra_prevista']),
            models.Index(fields=['filial', 'frequencia']),
        ]

    def __str__(self):
        return f'{self.cliente} — {self.get_frequencia_display()}'

    @property
    def tem_padrao(self) -> bool:
        return self.frequencia != self.Frequencia.SEM_PADRAO

    @property
    def dias_desde_ultima_compra(self):
        if not self.ultima_compra:
            return None
        from django.utils import timezone
        return (timezone.localdate() - self.ultima_compra).days

    @property
    def dias_atraso(self) -> int:
        """Quantos dias passaram da data prevista (0 se ainda não venceu)."""
        if self.dias_restantes is None or self.dias_restantes >= 0:
            return 0
        return abs(self.dias_restantes)

    @property
    def telefone_whatsapp(self) -> str:
        """Telefone só com dígitos, com DDI 55, pronto para link wa.me."""
        bruto = (self.cliente.celular or self.cliente.telefone or '') if self.cliente_id else ''
        digitos = ''.join(c for c in bruto if c.isdigit())
        if not digitos:
            return ''
        if not digitos.startswith('55'):
            digitos = '55' + digitos
        return digitos


class RecompraControle(TimestampedModel):
    """
    Controle de quando a empresa teve o último recálculo em lote.

    Serve de lock: o recálculo "preguiçoso" (disparado ao abrir a tela)
    trava esta linha com select_for_update(skip_locked=True) para que dois
    workers do gunicorn não recalculem a mesma empresa ao mesmo tempo.
    Não dá para usar o cache do Django nisso porque o backend configurado
    é LocMemCache, que é por processo.
    """

    empresa = models.OneToOneField(
        'core.Empresa', on_delete=models.CASCADE, related_name='recompra_controle',
    )
    ultima_execucao = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'crm_recompra_controle'
        verbose_name = 'Controle de Recálculo de Recompra'
        verbose_name_plural = 'Controles de Recálculo de Recompra'

    def __str__(self):
        return f'{self.empresa} — {self.ultima_execucao or "nunca"}'
