"""Aprovação por valor: transferência acima da alçada do solicitante fica pendente."""
from django.db import models

from apps.core.models.base import TimestampedModel


class NivelAprovacaoTransferencia(TimestampedModel):
    """
    Faixa de valor -> nome do nível que a aprova (ex.: "até R$1.000:
    Supervisor"). Puramente informativo/documental -- quem de fato pode
    aprovar é decidido por `PerfilAcesso.alcada_transferencia`; esta
    tabela só existe pra mostrar ao usuário quais faixas foram
    combinadas com qual nível de aprovador.
    """

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='niveis_aprovacao_transferencia',
    )
    nivel_nome = models.CharField(max_length=60, help_text='Ex.: Supervisor, Gerente, Diretor')
    valor_minimo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_maximo = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Vazio = sem teto (última faixa).',
    )

    class Meta:
        db_table = 'estoque_niveis_aprovacao_transferencia'
        ordering = ['empresa_id', 'valor_minimo']
        verbose_name = 'Nível de aprovação de transferência'
        verbose_name_plural = 'Níveis de aprovação de transferência'

    def __str__(self):
        return f'{self.nivel_nome} ({self.valor_minimo}+)'


class SolicitacaoTransferencia(TimestampedModel):
    """
    Transferência que excedeu a alçada de quem pediu -- fica parada aqui
    sem mexer em estoque nenhum ate' alguem com alçada suficiente
    aprovar (o que ai' sim executa a transferencia real) ou rejeitar.
    """

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        APROVADA = 'aprovada', 'Aprovada'
        REJEITADA = 'rejeitada', 'Rejeitada'

    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='solicitacoes_transferencia')
    filial_origem = models.ForeignKey('core.Filial', on_delete=models.PROTECT, related_name='solicitacoes_transferencia_origem')
    filial_destino = models.ForeignKey('core.Filial', on_delete=models.PROTECT, related_name='solicitacoes_transferencia_destino')
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    valor_estimado = models.DecimalField(max_digits=14, decimal_places=2)
    motivo = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    solicitante = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT, related_name='solicitacoes_transferencia_criadas',
    )
    aprovador = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solicitacoes_transferencia_decididas',
    )
    decidida_em = models.DateTimeField(null=True, blank=True)
    observacao_decisao = models.TextField(blank=True)
    documento_numero = models.CharField(
        max_length=20, blank=True,
        help_text='Preenchido quando aprovada -- número da transferência real gerada.',
    )

    class Meta:
        db_table = 'estoque_solicitacoes_transferencia'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['filial_origem', 'status'])]
        verbose_name = 'Solicitação de transferência'
        verbose_name_plural = 'Solicitações de transferência'

    def __str__(self):
        return f'{self.produto} {self.filial_origem} -> {self.filial_destino} ({self.get_status_display()})'
