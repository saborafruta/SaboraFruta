"""
A ficha do produto na fábrica de polpa — o que o catálogo do ERP não sabe.

NÃO É UM SEGUNDO CATÁLOGO. `produtos.Produto` já guarda descrição, código de
barras, NCM, CEST, o bloco fiscal inteiro, peso líquido e bruto, unidade,
quantidade por embalagem, condição de armazenamento, temperatura, controle
de lote e FEFO. Duplicar isso aqui daria dois cadastros do mesmo item — e,
no dia em que divergissem, a nota sairia com o NCM de um e o estoque com o
peso do outro.

O QUE FALTAVA É O VOCABULÁRIO DESTA INDÚSTRIA:

  · O QUE O ITEM É NA FÁBRICA. "Produto" não distingue polpa de manga,
    estabilizante e pote de 500 ml — e a formulação, a compra e a
    conferência tratam os três de formas diferentes. Sem essa classe, toda
    tela do vertical teria de adivinhar pelo nome do produto.

  · O SABOR. Numa confecção o produto é a peça; aqui "Polpa 100 g" existe em
    doze sabores que compartilham embalagem, NCM e processo, e diferem na
    fruta e na receita. Sabor como campo (e não no meio da descrição) é o
    que permite agrupar produção e ler o giro por fruta.

  · A VALIDADE EM DIAS. O ERP guarda a data de validade DO LOTE; ninguém
    guardava o prazo do PRODUTO. Sem ele, a data do lote é digitada à mão a
    cada produção — e digitação à mão em campo de validade é como sai lote
    com prazo errado para a rua.

  · A TEMPERATURA DE ARMAZENAMENTO já existe em `Produto`, e é de lá que
    esta ficha a lê. Repetir aqui criaria a segunda verdade que este arquivo
    inteiro existe para evitar.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class FichaProduto(FilialScopedModel):
    """A ficha de fábrica de um item do catálogo."""

    class Classe(models.TextChoices):
        # As três classes decidem o comportamento do item no vertical:
        # matéria-prima ENTRA por recebimento/compra, embalagem é consumida
        # no envase, acabado SAI com validade e lote.
        MATERIA_PRIMA = 'materia_prima', 'Matéria-prima'
        EMBALAGEM = 'embalagem', 'Material de embalagem'
        ACABADO = 'acabado', 'Produto acabado'

    class Tipo(models.TextChoices):
        # ── Matéria-prima ────────────────────────────────────────────────
        FRUTA = 'fruta', 'Fruta in natura'
        POLPA_BASE = 'polpa_base', 'Polpa-base'
        ACAI_BASE = 'acai_base', 'Açaí (base)'
        ACUCAR = 'acucar', 'Açúcar'
        AGUA = 'agua', 'Água'
        LEITE = 'leite', 'Leite'
        LEITE_PO = 'leite_po', 'Leite em pó'
        CREME_LEITE = 'creme_leite', 'Creme de leite'
        GORDURA = 'gordura', 'Gordura'
        ESTABILIZANTE = 'estabilizante', 'Estabilizante'
        EMULSIFICANTE = 'emulsificante', 'Emulsificante'
        AROMA = 'aroma', 'Aroma'
        CORANTE = 'corante', 'Corante'
        CONSERVANTE = 'conservante', 'Conservante'
        INGREDIENTE = 'ingrediente', 'Outro ingrediente'
        # ── Embalagem ────────────────────────────────────────────────────
        SACO = 'saco', 'Saco'
        POTE = 'pote', 'Pote'
        COPO = 'copo', 'Copo'
        TAMPA = 'tampa', 'Tampa'
        CAIXA = 'caixa', 'Caixa'
        FILME = 'filme', 'Filme plástico'
        ETIQUETA = 'etiqueta', 'Etiqueta'
        FARDO = 'fardo', 'Fardo'
        PALLET = 'pallet', 'Pallet'
        # ── Acabado ──────────────────────────────────────────────────────
        POLPA = 'polpa', 'Polpa de fruta'
        ACAI = 'acai', 'Açaí'
        SORVETE = 'sorvete', 'Sorvete'
        PICOLE = 'picole', 'Picolé'
        CREME = 'creme', 'Creme / base congelada'
        FRUTA_CONGELADA = 'fruta_congelada', 'Fruta congelada'
        MIX = 'mix', 'Mix de frutas'
        PERSONALIZADO = 'personalizado', 'Produto sob formulação'

    # Quais tipos pertencem a cada classe. A LISTA VIVE AQUI e não na tela:
    # o formulário filtra por ela e a validação cobra por ela, então as duas
    # nunca discordam sobre o que é embalagem.
    TIPOS_POR_CLASSE = {
        Classe.MATERIA_PRIMA: (
            Tipo.FRUTA, Tipo.POLPA_BASE, Tipo.ACAI_BASE, Tipo.ACUCAR, Tipo.AGUA,
            Tipo.LEITE, Tipo.LEITE_PO, Tipo.CREME_LEITE, Tipo.GORDURA,
            Tipo.ESTABILIZANTE, Tipo.EMULSIFICANTE, Tipo.AROMA, Tipo.CORANTE,
            Tipo.CONSERVANTE, Tipo.INGREDIENTE,
        ),
        Classe.EMBALAGEM: (
            Tipo.SACO, Tipo.POTE, Tipo.COPO, Tipo.TAMPA, Tipo.CAIXA,
            Tipo.FILME, Tipo.ETIQUETA, Tipo.FARDO, Tipo.PALLET,
        ),
        Classe.ACABADO: (
            Tipo.POLPA, Tipo.ACAI, Tipo.SORVETE, Tipo.PICOLE, Tipo.CREME,
            Tipo.FRUTA_CONGELADA, Tipo.MIX, Tipo.PERSONALIZADO,
        ),
    }

    CLASSE_DO_TIPO = {
        tipo: classe for classe, tipos in TIPOS_POR_CLASSE.items() for tipo in tipos
    }

    # UM PARA UM COM O CATÁLOGO. `OneToOne` e não FK: dois fichas para o
    # mesmo produto seriam duas respostas para "que sabor é este item".
    produto = models.OneToOneField(
        'produtos.Produto', on_delete=models.CASCADE, related_name='ficha_polpa',
    )

    classe = models.CharField(max_length=20, choices=Classe.choices, db_index=True)
    # SEM `choices` DE PROPOSITO. A lista em uso agora e' `TipoItem`, e o
    # servico chama `full_clean`: mantido o enum aqui, todo tipo criado pela
    # fabrica seria recusado como "opcao invalida". O enum continua vivo
    # como SEMENTE da tabela e como queda para leitura sem filial.
    tipo = models.CharField(max_length=20, db_index=True)

    # ── Identidade do acabado ────────────────────────────────────────────
    sabor = models.CharField(
        max_length=60, blank=True, db_index=True,
        help_text='Manga, açaí com guaraná, morango…',
    )
    fruta = models.ForeignKey(
        'polpa.Fruta', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos',
        help_text='A fruta deste item, quando ela tem ficha de recebimento.',
    )
    volume_ml = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Volume da embalagem em ml, quando o produto é líquido.',
    )

    # ── Validade ─────────────────────────────────────────────────────────
    # NULO É "NÃO SEI", e não zero: zero seria um produto que vence no dia em
    # que é feito, e a produção calcularia a data do lote como hoje.
    validade_dias = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Prazo de validade do produto, em dias a partir da fabricação.',
    )

    # ── Paletização ──────────────────────────────────────────────────────
    # `quantidade_por_embalagem` (caixa) já está no produto do ERP; o que
    # falta para fechar um pallet é o andar acima.
    caixas_por_pallet = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Quantas caixas fecham um pallet — a conta da expedição.',
    )

    # ── Registro sanitário ───────────────────────────────────────────────
    registro_mapa = models.CharField(
        max_length=40, blank=True,
        help_text='Registro no MAPA/SIF, quando o produto exige.',
    )

    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_fichas_produto'
        ordering = ['produto__descricao']
        indexes = [
            models.Index(fields=['filial', 'classe']),
            models.Index(fields=['filial', 'tipo']),
        ]
        verbose_name = 'Ficha de produto'
        verbose_name_plural = 'Fichas de produto'

    def __str__(self):
        return f'{self.produto} ({self.tipo_nome})'

    def clean(self):
        """O tipo tem de pertencer à classe — senão a tela filtra errado."""
        from django.core.exceptions import ValidationError

        esperada = self._classe_esperada()
        if esperada and self.classe and esperada != self.classe:
            raise ValidationError({
                'tipo': (
                    f'{self.tipo_nome} é '
                    f'{FichaProduto.Classe(esperada).label.lower()}, '
                    f'não {FichaProduto.Classe(self.classe).label.lower()}.'
                ),
            })

    def save(self, *args, **kwargs):
        # A CLASSE SAI DO TIPO quando não vier preenchida: são a mesma
        # informação em dois níveis, e deixar as duas soltas abriria a porta
        # para um "pote" cadastrado como matéria-prima.
        if not self.classe and self.tipo:
            self.classe = self._classe_esperada()
        super().save(*args, **kwargs)

    def _classe_esperada(self) -> str:
        """A classe que este tipo determina, pela tabela ou pelo enum."""
        from apps.polpa.models.tipo_item import TipoItem

        return TipoItem.classe_do_codigo(self.filial_id and self.filial, self.tipo)

    @property
    def tipo_nome(self) -> str:
        """
        O rotulo do tipo para exibir.

        Substitui `get_tipo_display()`, que morreu junto com o `choices`: sem
        ele o Django devolveria o codigo cru -- "polpa_base" no lugar de
        "Polpa-base" -- em toda tela que mostra o tipo.
        """
        from apps.polpa.models.tipo_item import TipoItem

        return TipoItem.nome_do_codigo(self.filial_id and self.filial, self.tipo)

    # ── Leituras ─────────────────────────────────────────────────────────

    @property
    def nome_completo(self) -> str:
        """Descrição com o sabor, do jeito que aparece na etiqueta."""
        base = self.produto.descricao
        if self.sabor and self.sabor.lower() not in base.lower():
            return f'{base} — {self.sabor}'
        return base

    @property
    def congelado(self) -> bool:
        from apps.produtos.models import Produto

        return self.produto.condicao_armazenamento == Produto.CondicaoArmazenamento.CONGELADO

    def validade_a_partir_de(self, fabricacao):
        """
        A data de validade de um lote fabricado nesta data.

        É AQUI QUE A CONTA MORA, e não na tela de produção: a validade do
        lote é a mesma pergunta feita pela produção, pela expedição e pela
        etiqueta, e três contas iguais espalhadas divergem na primeira
        mudança de prazo.
        """
        from datetime import timedelta

        if not self.validade_dias or fabricacao is None:
            return None
        return fabricacao + timedelta(days=self.validade_dias)

    def pendencias(self) -> list[str]:
        """
        O que falta nesta ficha para o item funcionar no processo.

        A LISTA É POR CLASSE porque a exigência é diferente: acabado sem
        validade gera lote sem data; matéria-prima sem unidade não entra no
        estoque; embalagem sem quantidade por caixa não fecha a paletização.
        Um "campo obrigatório" único faria a fábrica preencher zero para
        conseguir salvar.
        """
        produto = self.produto
        faltando = []

        if self.classe == self.Classe.ACABADO:
            if not self.validade_dias:
                faltando.append(
                    'Sem prazo de validade — o lote sairá sem data de vencimento.'
                )
            if not produto.controla_lote:
                faltando.append(
                    'Produto não controla lote — sem isso não há rastreabilidade.'
                )
            if not (produto.ncm or '').strip():
                faltando.append('Sem NCM — a nota fiscal deste item não sai.')
            if not (produto.codigo_barras or '').strip():
                faltando.append('Sem código de barras — o PDV e o cliente não leem.')

        if self.classe == self.Classe.EMBALAGEM:
            if not produto.quantidade_por_embalagem:
                faltando.append('Sem quantidade por caixa — a paletização não fecha.')

        if self.congelado and produto.temperatura_maxima is None:
            faltando.append(
                'Congelado sem temperatura máxima definida — não há desvio a apurar.'
            )
        return faltando


# Peso zero é ausência de informação, não um produto sem peso.
ZERO = Decimal('0')
