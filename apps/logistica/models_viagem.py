"""
Viagem e carga.

O QUE A VIAGEM RESOLVE
======================

Um caminhão sai com três coisas ao mesmo tempo: mercadoria já vendida,
mercadoria sem comprador para vender na rua, e mercadoria de bonificação.
Fisicamente é uma carga só, e o MDF-e é um só. Fiscalmente cada uma tem
documento, CFOP e destino próprios.

O `RomaneioCarga` que já existia responde por ENTREGA — cada item dele é um
cliente com endereço e status. Ele não tem onde pendurar "dez caixas sem
comprador", porque não existe comprador. A Viagem é a camada de cima: ela
guarda o veículo, o motorista, a rota e o MDF-e, e a carga dela é por PRODUTO
e QUANTIDADE, com a natureza fiscal em cada linha. O romaneio continua vivo
como o roteiro de entregas dentro da viagem.

O SALDO EM PODER DE QUEM VIAJA
==============================

Na venda fora do estabelecimento a mercadoria deixa o estabelecimento — o
estoque da filial baixa de verdade, amparado pela nota de remessa. Mas ela
continua sendo da empresa: está com o vendedor, não vendida.

Tratar isso como baixa e pronto perde o rastro: durante a rota o sistema não
sabe onde a mercadoria está, e o que volta entra como uma devolução solta que
não conversa com a saída. Por isso existe `SaldoCarga`: um livro por viagem e
produto, onde o que saiu na remessa precisa fechar contra o que vendeu, o que
foi bonificado e o que voltou.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models.base import FilialScopedModel, TimestampedModel

ZERO = Decimal('0')


class Viagem(FilialScopedModel):
    """A saída de um veículo, com tudo que ele leva."""

    class Status(models.TextChoices):
        """
        O ciclo de uma viagem, do papel à prestação de contas.

        SÃO ETAPAS DE VERDADE, e não rótulos. Entre fechar a carga e o veículo
        sair há um intervalo em que a mercadoria já baixou do estoque mas o
        documento ainda não foi autorizado — e é justamente aí que as coisas
        dão errado. Um status só para "em rota" esconderia esse intervalo, e
        ninguém saberia dizer se o caminhão pode ou não sair.
        """

        RASCUNHO = 'rascunho', 'Rascunho'
        EM_PREPARACAO = 'em_preparacao', 'Em preparação'
        AGUARDANDO_DOCUMENTOS = 'aguardando_documentos', 'Aguardando documentos fiscais'
        DOCUMENTOS_EMITIDOS = 'documentos_emitidos', 'Documentos emitidos'
        MDFE_AUTORIZADO = 'mdfe_autorizado', 'MDF-e autorizado'
        EM_TRANSITO = 'em_transito', 'Em trânsito'
        EM_VENDAS = 'em_vendas', 'Em vendas'
        RETORNANDO = 'retornando', 'Retornando'
        AGUARDANDO_CONFERENCIA = 'aguardando_conferencia', 'Aguardando conferência'
        FINALIZADA = 'finalizada', 'Finalizada'
        CANCELADA = 'cancelada', 'Cancelada'

    # Enquanto nada saiu do estoque e nenhum documento existe, a carga é livre.
    STATUS_EDITAVEIS = (Status.RASCUNHO, Status.EM_PREPARACAO)
    # Depois que a carga fechou, mexer nela é reescrever o que o documento já
    # disse -- a correção passa a ser por baixa, retorno ou cancelamento.
    STATUS_ENCERRADOS = (Status.FINALIZADA, Status.CANCELADA)

    # AS TRANSIÇÕES SÃO EXPLÍCITAS. Sem elas o status vira enfeite: alguém
    # marca "Finalizada" numa viagem que nunca saiu, e a prestação de contas
    # deixa de significar coisa alguma.
    TRANSICOES = {
        Status.RASCUNHO: (Status.EM_PREPARACAO, Status.CANCELADA),
        Status.EM_PREPARACAO: (Status.RASCUNHO, Status.AGUARDANDO_DOCUMENTOS, Status.CANCELADA),
        Status.AGUARDANDO_DOCUMENTOS: (Status.DOCUMENTOS_EMITIDOS, Status.CANCELADA),
        # Nem toda carga precisa de MDF-e: quando não precisa, sai direto.
        Status.DOCUMENTOS_EMITIDOS: (
            Status.MDFE_AUTORIZADO, Status.EM_TRANSITO, Status.CANCELADA,
        ),
        Status.MDFE_AUTORIZADO: (Status.EM_TRANSITO, Status.CANCELADA),
        # Em trânsito e em vendas alternam: o veículo roda, para, vende, roda.
        Status.EM_TRANSITO: (Status.EM_VENDAS, Status.RETORNANDO),
        Status.EM_VENDAS: (Status.EM_TRANSITO, Status.RETORNANDO),
        Status.RETORNANDO: (Status.AGUARDANDO_CONFERENCIA,),
        Status.AGUARDANDO_CONFERENCIA: (Status.FINALIZADA,),
        Status.FINALIZADA: (),
        Status.CANCELADA: (),
    }

    numero = models.PositiveIntegerField(db_index=True)
    data_saida = models.DateField(default=timezone.localdate, db_index=True)
    hora_saida = models.TimeField(
        null=True, blank=True,
        help_text='A hora em que o veículo deixa a empresa.',
    )
    # PREVISAO E RETORNO SAO CAMPOS DIFERENTES. Guardar so' um faz a viagem
    # atrasada parecer no prazo: a data muda quando ela volta, e some a
    # informacao de que deveria ter voltado antes.
    previsao_retorno = models.DateField(null=True, blank=True)
    data_retorno = models.DateField(
        null=True, blank=True, help_text='Quando o veículo efetivamente voltou.',
    )
    status = models.CharField(
        max_length=30, choices=Status.choices,
        default=Status.RASCUNHO, db_index=True,
    )

    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='viagens_responsavel',
    )
    # Quem vende na rua. Numa viagem de venda fora, o saldo de carga responde
    # a esta pessoa -- e' dela a prestacao de contas no retorno.
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='viagens_vendedor',
        help_text='Quem responde pela mercadoria que sai sem comprador.',
    )

    # O CADASTRO PREENCHE, O TEXTO PERMANECE. Escolher do cadastro evita
    # digitar nome e placa errados; guardar o texto junto e' o que faz a viagem
    # de dois anos atras continuar dizendo quem levou, mesmo que o motorista
    # tenha saido da empresa e o cadastro dele mude.
    motorista = models.ForeignKey(
        'cadastros.Motorista', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='viagens',
    )
    motorista_nome = models.CharField(max_length=120, blank=True)
    motorista_documento = models.CharField(max_length=30, blank=True)
    veiculo = models.ForeignKey(
        'cadastros.Veiculo', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='viagens',
    )
    veiculo_placa = models.CharField(max_length=10, blank=True)
    veiculo_descricao = models.CharField(max_length=100, blank=True)
    transportadora = models.ForeignKey(
        'cadastros.Transportadora', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='viagens',
    )

    rota = models.CharField(max_length=160, blank=True)
    uf_origem = models.CharField(max_length=2, blank=True)
    uf_destino = models.CharField(max_length=2, blank=True)
    percurso_ufs = models.CharField(
        max_length=120, blank=True,
        help_text='UFs por onde o veículo passa, na ordem, separadas por vírgula.',
    )

    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'logistica_viagens'
        ordering = ['-data_saida', '-numero']
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'numero'], name='viagem_numero_por_filial',
            ),
        ]
        indexes = [
            models.Index(fields=['filial', 'status', 'data_saida']),
            models.Index(fields=['filial', 'vendedor', 'status']),
        ]
        verbose_name = 'Viagem'
        verbose_name_plural = 'Viagens'

    def __str__(self):
        return f'Viagem #{self.numero:06d}'

    @property
    def editavel(self) -> bool:
        return self.status in self.STATUS_EDITAVEIS

    @property
    def aberta(self) -> bool:
        return self.status not in self.STATUS_ENCERRADOS

    @property
    def saiu(self) -> bool:
        """A mercadoria já deixou o estabelecimento."""
        return self.status not in (
            self.Status.RASCUNHO, self.Status.EM_PREPARACAO, self.Status.CANCELADA,
        )

    def proximos_status(self) -> list[tuple[str, str]]:
        """Para onde esta viagem pode ir a partir de onde está."""
        rotulos = dict(self.Status.choices)
        return [(valor, rotulos[valor]) for valor in self.TRANSICOES.get(self.status, ())]

    def pode_ir_para(self, destino: str) -> bool:
        return destino in self.TRANSICOES.get(self.status, ())


class ItemCarga(TimestampedModel):
    """
    Um produto, uma quantidade e a natureza fiscal com que ele viaja.

    A NATUREZA FICA NA LINHA, e não na viagem: é o que permite o mesmo
    caminhão levar venda, venda fora e bonificação sem misturar o fiscal. Duas
    linhas do mesmo produto com naturezas diferentes são duas operações
    diferentes, e vão em documentos diferentes.
    """

    viagem = models.ForeignKey(Viagem, on_delete=models.CASCADE, related_name='itens')
    natureza = models.ForeignKey(
        'fiscal.NaturezaOperacao', on_delete=models.PROTECT, related_name='itens_carga',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='itens_carga',
    )
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_carga',
    )

    # O destinatário só existe quando a operação tem um. A remessa para venda
    # fora sai sem: a nota é contra a própria empresa.
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.PROTECT,
        null=True, blank=True, related_name='itens_carga',
    )
    pedido_venda = models.ForeignKey(
        'vendas.PedidoVenda', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_carga',
        help_text='O pedido que esta linha atende, quando a venda já existia.',
    )

    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'logistica_itens_carga'
        ordering = ['viagem', 'natureza', 'produto', 'pk']
        indexes = [
            models.Index(fields=['viagem', 'natureza']),
            models.Index(fields=['viagem', 'produto']),
        ]
        verbose_name = 'Item da carga'
        verbose_name_plural = 'Itens da carga'

    def __str__(self):
        return f'{self.produto} × {self.quantidade} ({self.natureza.codigo})'

    def clean(self):
        if self.quantidade is not None and self.quantidade <= ZERO:
            raise ValidationError({'quantidade': 'A quantidade precisa ser maior que zero.'})
        # Bonificação e venda sabem para quem vão; remessa para venda fora,
        # não. Deixar isso passar produz nota sem destinatário na hora de
        # transmitir, quando já não dá para voltar atrás.
        if self.natureza_id and self.natureza.exige_destinatario and not self.cliente_id:
            raise ValidationError({
                'cliente': f'A operação "{self.natureza.descricao}" precisa de destinatário.',
            })

    def save(self, *args, **kwargs):
        self.valor_total = (self.quantidade or ZERO) * (self.valor_unitario or ZERO)
        super().save(*args, **kwargs)


class SaldoCarga(TimestampedModel):
    """
    O que saiu na remessa e ainda não voltou nem foi vendido.

    É o livro que fecha a venda fora do estabelecimento: remetido tem que ser
    igual a vendido + bonificado + retornado. Enquanto não fecha, há mercadoria
    da empresa na rua sem destino registrado — e é exatamente isso que a
    fiscalização pede para ver.
    """

    viagem = models.ForeignKey(Viagem, on_delete=models.CASCADE, related_name='saldos')
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='saldos_carga',
    )
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='saldos_carga',
    )

    quantidade_remetida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantidade_vendida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantidade_bonificada = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantidade_retornada = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    # O que sumiu: quebra, perda, furto. Sai do saldo por baixa declarada, e
    # nunca por diferenca silenciosa -- saldo que "some" sozinho e' o mesmo que
    # nao ter livro nenhum.
    quantidade_baixada = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    custo_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        db_table = 'logistica_saldos_carga'
        ordering = ['viagem', 'produto']
        constraints = [
            models.UniqueConstraint(
                fields=['viagem', 'produto', 'lote'],
                name='saldo_carga_unico_por_produto_lote',
            ),
        ]
        indexes = [
            models.Index(fields=['viagem', 'produto']),
        ]
        verbose_name = 'Saldo em poder da viagem'
        verbose_name_plural = 'Saldos em poder da viagem'

    def __str__(self):
        return f'{self.produto} · {self.quantidade_em_poder} em poder'

    @property
    def quantidade_em_poder(self) -> Decimal:
        """O que ainda está no caminhão, pela conta do sistema."""
        return (
            (self.quantidade_remetida or ZERO)
            - (self.quantidade_vendida or ZERO)
            - (self.quantidade_bonificada or ZERO)
            - (self.quantidade_retornada or ZERO)
            - (self.quantidade_baixada or ZERO)
        )

    @property
    def fechado(self) -> bool:
        return self.quantidade_em_poder == ZERO
