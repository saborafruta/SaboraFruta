"""
Naturezas de operação e as regras fiscais de cada uma.

POR QUE ISTO É TABELA E NÃO CÓDIGO
==================================

O CFOP de uma mesma operação muda com a UF de origem e destino, com o regime
tributário do emitente e, em vários casos, com o produto. Uma remessa para
venda fora do estabelecimento é 5904 dentro do estado e 6904 para fora; a
bonificação é 5910/6910; o retorno é 5905/6905 — e a contabilidade de cada
cliente ajusta CST, CSOSN e natureza conforme entende.

Cravar isso no código transforma cada orientação da contabilidade num deploy,
e cada exceção de UF numa ramificação a mais dentro de um `if`. Aqui a regra é
dado: quem responde pelo fiscal cadastra, versiona por vigência e a emissão só
consulta.

O QUE O SISTEMA DECIDE E O QUE A TABELA DECIDE
==============================================

O sistema precisa saber o COMPORTAMENTO de cada natureza — se baixa estoque,
se gera contas a receber, se exige destinatário, se é o retorno de uma remessa
anterior. Isso é `especie`, e é um conjunto fechado: são os ganchos que o
código realmente executa.

Tudo que é NÚMERO FISCAL — CFOP, CST, CSOSN, alíquotas, finalidade — vem de
`RegraNaturezaOperacao`, que pode ter uma linha por UF, por regime e por
produto, com vigência.
"""
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


class NaturezaOperacao(FilialScopedModel):
    """Uma operação que o sistema sabe executar, com o nome que ela tem na nota."""

    class Especie(models.TextChoices):
        """
        O comportamento que o código executa. Fechado de propósito: cada valor
        aqui corresponde a um caminho no sistema (baixa de estoque,
        conciliação, financeiro). Acrescentar espécie é mudar comportamento,
        não configuração.
        """

        VENDA = 'venda', 'Venda'
        # Sai da empresa sem comprador. A mercadoria continua sendo da empresa,
        # em poder de quem viaja, e precisa voltar ou ser vendida.
        REMESSA_VENDA_FORA = 'remessa_venda_fora', 'Remessa para venda fora do estabelecimento'
        # A venda que acontece na rua, contra o saldo que saiu na remessa.
        VENDA_FORA = 'venda_fora', 'Venda fora do estabelecimento'
        # O que não vendeu e voltou.
        RETORNO_VENDA_FORA = 'retorno_venda_fora', 'Retorno de venda fora do estabelecimento'
        BONIFICACAO = 'bonificacao', 'Bonificação'
        REMESSA_SIMPLES = 'remessa_simples', 'Outra remessa'
        RETORNO_SIMPLES = 'retorno_simples', 'Outro retorno'

    # As espécies que tiram mercadoria do estabelecimento.
    ESPECIES_DE_SAIDA = (
        Especie.VENDA,
        Especie.REMESSA_VENDA_FORA,
        Especie.BONIFICACAO,
        Especie.REMESSA_SIMPLES,
    )
    # As que consomem o saldo que saiu numa remessa, em vez do estoque da filial.
    ESPECIES_CONTRA_REMESSA = (
        Especie.VENDA_FORA,
        Especie.RETORNO_VENDA_FORA,
    )

    codigo = models.SlugField(
        max_length=40,
        help_text='Identificador curto usado nas telas e nos relatórios.',
    )
    descricao = models.CharField(
        max_length=120,
        help_text='O texto que vai no campo "natureza da operação" da nota.',
    )
    especie = models.CharField(
        max_length=30,
        choices=Especie.choices,
        db_index=True,
        help_text='O comportamento que o sistema executa nesta operação.',
    )

    # ── O que a operação faz, além da nota ────────────────────────────────
    movimenta_estoque = models.BooleanField(
        default=True,
        help_text='Desmarque para operações que só geram documento.',
    )
    tipo_operacao_estoque = models.CharField(
        max_length=30,
        blank=True,
        help_text=(
            'Tipo da movimentação de estoque (ver MovimentacaoEstoque). '
            'Em branco, o sistema usa o padrão da espécie.'
        ),
    )
    gera_financeiro = models.BooleanField(
        default=True,
        help_text=(
            'Bonificação e remessa normalmente não geram contas a receber. '
            'Desmarque quando a operação não cobra do destinatário.'
        ),
    )
    exige_destinatario = models.BooleanField(
        default=True,
        help_text=(
            'A remessa para venda fora sai sem comprador — a nota é contra a '
            'própria empresa. Desmarque nesses casos.'
        ),
    )
    entra_no_mdfe = models.BooleanField(
        default=True,
        help_text='Se a nota desta operação deve constar no MDF-e da viagem.',
    )

    ativo = models.BooleanField(default=True, db_index=True)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'fiscal_naturezas_operacao'
        ordering = ['descricao']
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'codigo'],
                name='natureza_operacao_codigo_por_filial',
            ),
        ]
        indexes = [
            models.Index(fields=['filial', 'especie', 'ativo']),
        ]
        verbose_name = 'Natureza de operação'
        verbose_name_plural = 'Naturezas de operação'

    def __str__(self):
        return f'{self.descricao} ({self.codigo})'

    @property
    def e_saida(self) -> bool:
        return self.especie in self.ESPECIES_DE_SAIDA

    @property
    def consome_saldo_de_remessa(self) -> bool:
        return self.especie in self.ESPECIES_CONTRA_REMESSA


class RegraNaturezaOperacao(TimestampedModel):
    """
    O CFOP e a tributação de uma natureza, para um contexto.

    A REGRA MAIS ESPECÍFICA GANHA. Uma linha sem UF, sem regime e sem produto é
    o padrão da natureza; acrescentar UF de destino, regime ou produto estreita
    o alvo. Assim a contabilidade cadastra o geral uma vez e trata só as
    exceções — em vez de repetir tudo para cada combinação.

    VIGÊNCIA EM VEZ DE EDIÇÃO. Mudança de regra vale a partir de uma data, e a
    nota emitida ontem precisa continuar explicável pela regra de ontem.
    Corrigir a linha antiga apagaria essa história.
    """

    natureza = models.ForeignKey(
        NaturezaOperacao,
        on_delete=models.CASCADE,
        related_name='regras',
    )

    # ── O alvo da regra. Vazio significa "vale para qualquer" ────────────
    uf_origem = models.CharField(max_length=2, blank=True, db_index=True)
    uf_destino = models.CharField(
        max_length=2,
        blank=True,
        db_index=True,
        help_text='Deixe vazio e marque "somente interestadual" para a regra de fora do estado.',
    )
    somente_interestadual = models.BooleanField(
        default=False,
        help_text='Vale quando origem e destino são UFs diferentes, qualquer que seja o destino.',
    )
    regime_tributario = models.CharField(max_length=30, blank=True, db_index=True)
    ncm = models.CharField(max_length=8, blank=True, db_index=True)
    produto = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='regras_natureza',
    )

    # ── O que a regra devolve ────────────────────────────────────────────
    cfop = models.CharField(max_length=5)
    cst_icms = models.CharField(max_length=3, blank=True)
    csosn = models.CharField(max_length=3, blank=True)
    cst_pis = models.CharField(max_length=2, blank=True)
    cst_cofins = models.CharField(max_length=2, blank=True)
    cst_ipi = models.CharField(max_length=2, blank=True)
    aliquota_icms = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    reducao_base_icms = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    finalidade_nfe = models.PositiveSmallIntegerField(
        default=1,
        help_text='1 normal, 2 complementar, 3 ajuste, 4 devolução.',
    )
    natureza_operacao_texto = models.CharField(
        max_length=120,
        blank=True,
        help_text='Sobrescreve o texto da natureza, quando esta regra pede outro.',
    )
    informacoes_complementares = models.TextField(
        blank=True,
        help_text='Vai para o campo de informações adicionais da nota.',
    )

    vigencia_inicio = models.DateField(null=True, blank=True, db_index=True)
    vigencia_fim = models.DateField(null=True, blank=True, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'fiscal_regras_natureza_operacao'
        ordering = ['natureza', '-vigencia_inicio', 'uf_destino']
        indexes = [
            models.Index(fields=['natureza', 'ativo']),
            models.Index(fields=['natureza', 'uf_destino', 'ativo']),
        ]
        verbose_name = 'Regra de natureza de operação'
        verbose_name_plural = 'Regras de natureza de operação'

    def __str__(self):
        alvo = self.uf_destino or ('interestadual' if self.somente_interestadual else 'padrão')
        return f'{self.natureza.codigo} · {alvo} · CFOP {self.cfop}'

    def clean(self):
        if self.uf_destino and self.somente_interestadual:
            raise ValidationError({
                'somente_interestadual': (
                    'Escolha um destino OU marque interestadual — as duas '
                    'coisas juntas descrevem alvos diferentes.'
                ),
            })
        if self.vigencia_inicio and self.vigencia_fim and self.vigencia_fim < self.vigencia_inicio:
            raise ValidationError({
                'vigencia_fim': 'A vigência termina antes de começar.',
            })

    @property
    def especificidade(self) -> int:
        """
        Quanto mais estreito o alvo, maior o peso — é o critério de desempate
        quando mais de uma regra serve.
        """
        peso = 0
        if self.produto_id:
            peso += 8
        if self.ncm:
            peso += 4
        if self.uf_destino:
            peso += 2
        if self.somente_interestadual:
            peso += 1
        if self.regime_tributario:
            peso += 1
        if self.uf_origem:
            peso += 1
        return peso
