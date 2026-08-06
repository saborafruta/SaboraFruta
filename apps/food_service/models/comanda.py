from decimal import Decimal

from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class Comanda(FilialScopedModel):
    """
    Uma comanda aberta acumula itens até ser fechada.

    `mesas` é M2M (não uma FK única) porque isso é o que permite "unir
    mesas" (uma comanda cobrindo várias) e "transferir mesa" (editar o M2M)
    sem precisar de um modelo de histórico/junção extra. Uma comanda sem
    mesas é uma comanda avulsa (balcão/individual/por cliente).

    Ao fechar, converge para uma `VendaPDV` real via
    `ComandaService.fechar()` — reaproveitando baixa de estoque, fiscal e
    cashback já existentes em vez de reimplementá-los aqui.
    """

    class Status(models.TextChoices):
        ABERTA = 'aberta', 'Aberta'
        FECHANDO = 'fechando', 'Fechando'
        FECHADA = 'fechada', 'Fechada'
        CANCELADA = 'cancelada', 'Cancelada'

    class Tipo(models.TextChoices):
        MESA = 'mesa', 'Por mesa'
        CLIENTE = 'cliente', 'Por cliente'
        INDIVIDUAL = 'individual', 'Individual'
        COMPARTILHADA = 'compartilhada', 'Compartilhada'
        QR_CODE = 'qrcode', 'QR Code'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.MESA)
    mesas = models.ManyToManyField('food_service.Mesa', related_name='comandas', blank=True)
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.PROTECT, null=True, blank=True,
        related_name='comandas',
    )
    nome_ocupante = models.CharField(max_length=100, blank=True)
    garcom = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='comandas_atendidas',
    )
    quantidade_pessoas = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTA)
    aberta_em = models.DateTimeField(auto_now_add=True)
    fechada_em = models.DateTimeField(null=True, blank=True)
    observacoes = models.TextField(blank=True)
    venda_pdv = models.OneToOneField(
        'pdv.VendaPDV', on_delete=models.PROTECT, null=True, blank=True,
        related_name='comanda',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'food_service_comandas'
        verbose_name = 'Comanda'
        verbose_name_plural = 'Comandas'
        ordering = ['-aberta_em']

    def __str__(self):
        return self.nome_ocupante or f'Comanda #{self.pk}'

    @property
    def valor_total(self):
        return sum(
            (item.valor_total_com_complementos for item in self.itens.all()),
            Decimal('0'),
        )
