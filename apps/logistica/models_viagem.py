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
    # O ELO ATE' O DOCUMENTO. Viagem → carga → documento fiscal → cliente: e' a
    # corrente que responde "que nota amparava esta mercadoria neste caminhao?"
    # -- pergunta de fiscalizacao, e que sem este campo so' se responde
    # cruzando planilha com o portal da SEFAZ.
    documento_fiscal = models.ForeignKey(
        'financeiro.DocumentoFiscal', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_carga',
        help_text='A nota que ampara esta linha, quando já emitida.',
    )

    # O MOVIMENTO DE ESTOQUE DESTA LINHA, e não o da viagem inteira.
    #
    # A CARGA É UMA SÓ FISICAMENTE, E VÁRIAS FISCALMENTE. Quando o mesmo
    # produto e lote sobem no caminhão em duas linhas de naturezas
    # diferentes — parte vendida, parte em remessa —, o razão registra dois
    # movimentos idênticos em tudo menos na natureza. Sem este ponteiro não
    # há como dizer qual movimento pertence a qual operação, e o vínculo com
    # a nota tinha de ser deixado vazio para não amparar uma venda com nota
    # de remessa.
    movimentacao = models.ForeignKey(
        'estoque.MovimentacaoEstoque', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_carga',
        help_text='A baixa de estoque que esta linha gerou.',
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
            models.Index(fields=['pedido_venda']),
            models.Index(fields=['documento_fiscal']),
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


class VendaViagem(TimestampedModel):
    """
    Uma venda feita na rua, contra o saldo que a viagem carrega.

    A VENDA PRECISA EXISTIR COMO REGISTRO, e não apenas baixar o saldo. Sem
    ela o sistema sabe que 50 unidades saíram do caminhão, mas não para quem,
    por quanto nem em que condição — e a prestação de contas do retorno vira
    a palavra do vendedor contra a diferença de estoque.

    O CLIENTE PODE NÃO ESTAR CADASTRADO. Venda de rua acontece com quem
    aparece, e exigir cadastro prévio pararia a venda na calçada. Por isso há
    FK opcional e, junto, os dados copiados: é o que faz a venda de dois anos
    atrás continuar dizendo para quem foi, mesmo que o cadastro mude depois.
    """

    class Status(models.TextChoices):
        REGISTRADA = 'registrada', 'Registrada'
        CANCELADA = 'cancelada', 'Cancelada'

    class Tipo(models.TextChoices):
        """
        O que saiu do caminhão para o cliente.

        A BONIFICAÇÃO É A MESMA ENTREGA COM OUTRA NATUREZA. Ela tem cliente,
        itens, lote e nota — muda que ninguém paga e que o CFOP é outro.
        Um modelo próprio ao lado repetiria tudo isso para trocar duas
        coisas, e as duas listas de entregas feitas na rua acabariam
        discordando sobre o que saiu do caminhão.
        """

        VENDA = 'venda', 'Venda'
        BONIFICACAO = 'bonificacao', 'Bonificação'

    class Motivo(models.TextChoices):
        """
        Por que a mercadoria saiu sem cobrança.

        LISTA FECHADA, E NÃO TEXTO LIVRE. "Por que demos 20 caixas?" é a
        pergunta que a auditoria faz e que o comercial precisa responder por
        cliente e por período — e isso não se faz agrupando frases digitadas
        à mão. `OUTRO` existe para o caso que a lista não previu, e é ele que
        evita que a lista vire mentira: sem essa saída, quem não se encaixa
        escolhe qualquer uma.
        """

        COMERCIAL = 'comercial', 'Bonificação comercial'
        BRINDE = 'brinde', 'Brinde'
        CAMPANHA = 'campanha', 'Campanha promocional'
        ACAO = 'acao', 'Ação comercial'
        RELACIONAMENTO = 'relacionamento', 'Relacionamento'
        COMPENSACAO = 'compensacao', 'Compensação'
        OUTRO = 'outro', 'Outro'

    viagem = models.ForeignKey(Viagem, on_delete=models.CASCADE, related_name='vendas')
    tipo = models.CharField(
        max_length=15, choices=Tipo.choices, default=Tipo.VENDA, db_index=True,
        help_text='Venda cobra; bonificação entrega sem cobrar.',
    )
    motivo = models.CharField(
        max_length=20, choices=Motivo.choices, blank=True, db_index=True,
        help_text='Por que a bonificação foi dada. Vazio em venda.',
    )
    # O PEDIDO QUE ORIGINOU A CORTESIA, quando existe. Bonificação de
    # compensação e de campanha quase sempre respondem a uma venda anterior,
    # e sem o vínculo a pergunta "esta cortesia foi por causa de quê?" não
    # tem resposta no sistema.
    pedido_venda = models.ForeignKey(
        'vendas.PedidoVenda', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='entregas_viagem',
        help_text='Pedido relacionado, quando a entrega responde a um.',
    )
    numero = models.PositiveIntegerField(db_index=True)
    data = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices,
        default=Status.REGISTRADA, db_index=True,
    )

    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vendas_viagem',
    )
    cliente_nome = models.CharField(max_length=180)
    cliente_documento = models.CharField(max_length=20, blank=True)
    # Copiado, e nao apontado: e' para onde a mercadoria foi naquele dia.
    endereco = models.JSONField(default=dict, blank=True)

    condicao_pagamento = models.ForeignKey(
        'financeiro.CondicaoPagamento', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vendas_viagem',
    )
    forma_pagamento = models.ForeignKey(
        'financeiro.FormaPagamento', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vendas_viagem',
    )

    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vendas_viagem',
    )
    documento_fiscal = models.ForeignKey(
        'financeiro.DocumentoFiscal', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vendas_viagem',
    )

    class Meta:
        db_table = 'logistica_vendas_viagem'
        ordering = ['-data', '-numero']
        constraints = [
            models.UniqueConstraint(
                fields=['viagem', 'numero'], name='venda_viagem_numero_por_viagem',
            ),
        ]
        indexes = [
            models.Index(fields=['viagem', 'status']),
            models.Index(fields=['cliente']),
        ]
        verbose_name = 'Venda durante a viagem'
        verbose_name_plural = 'Vendas durante a viagem'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero} — {self.cliente_nome}'

    @property
    def campo_do_saldo(self) -> str:
        """
        Em qual coluna do saldo da carga esta entrega baixa.

        A CONCILIAÇÃO SEPARA OS DOIS de propósito: remetido = vendido +
        bonificado + retornado + baixado. Somar bonificação em "vendido"
        faria a viagem parecer ter faturado o que foi dado.
        """
        return (
            'quantidade_bonificada' if self.tipo == self.Tipo.BONIFICACAO
            else 'quantidade_vendida'
        )

    @property
    def bonificacao(self) -> bool:
        return self.tipo == self.Tipo.BONIFICACAO

    def recalcular_total(self):
        total = sum(
            (item.valor_total or ZERO for item in self.itens.all()), ZERO,
        )
        self.valor_total = total
        self.save(update_fields=['valor_total', 'updated_at'])
        return total


class ItemVendaViagem(TimestampedModel):
    """Um produto vendido na rua, com o lote de onde ele saiu."""

    venda = models.ForeignKey(VendaViagem, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='itens_venda_viagem',
    )
    # O LOTE VEM DO SALDO, e nao de escolha livre: e' o que saiu no caminhao.
    # Vender de um lote que nao viajou quebraria a rastreabilidade justamente
    # no ponto em que ela mais importa.
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_venda_viagem',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # A REMESSA QUE AMPAROU ESTA MERCADORIA, gravada no momento da entrega.
    #
    # Ela é descobrível pela viagem — mas descobrir não é a mesma coisa que
    # registrar. Se a remessa for cancelada e reemitida depois de vendas
    # feitas, a busca passaria a apontar a nota NOVA para vendas que saíram
    # sob a ANTIGA, e o vínculo mudaria sozinho no dia em que ele mais
    # importa. Guardado aqui, ele responde pelo que era verdade quando a
    # mercadoria saiu do caminhão.
    remessa = models.ForeignKey(
        'financeiro.DocumentoFiscal', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_venda_viagem',
        help_text='NF-e de remessa sob a qual esta mercadoria saiu.',
    )

    class Meta:
        db_table = 'logistica_itens_venda_viagem'
        ordering = ['venda', 'pk']
        indexes = [models.Index(fields=['venda', 'produto'])]
        verbose_name = 'Item da venda em viagem'
        verbose_name_plural = 'Itens da venda em viagem'

    def __str__(self):
        return f'{self.produto} × {self.quantidade}'

    def save(self, *args, **kwargs):
        self.valor_total = (self.quantidade or ZERO) * (self.valor_unitario or ZERO)
        super().save(*args, **kwargs)
