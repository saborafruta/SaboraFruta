"""
Uma linha do checklist dentro de uma análise — o veredito item a item.

`AnaliseQualidade.parametros` é um JSON `{"brix": 12.5}`. Ele guarda o que foi
MEDIDO e nada mais, e três coisas que a qualidade precisa não cabem lá:

  1. O VEREDITO POR ITEM. A análise inteira tem "aprovado" ou "reprovado"; a
     linha não tem nada. Não dá para perguntar "quantas vezes a selagem
     reprovou este mês" — a resposta está presa dentro de um blob por laudo,
     e é justamente ela que diz onde o processo está falhando.

  2. O QUE ERA EXIGIDO. O JSON só tem o que alguém digitou. Se o pH for
     esquecido, a chave simplesmente não existe, e o laudo fecha "aprovado"
     sem ele — a falta some em vez de aparecer. Apontando para o parâmetro
     cadastrado, o item existe mesmo vazio, e vazio obrigatório é pendência.

  3. A AÇÃO CORRETIVA. `AnaliseQualidade.acao_reprovacao` é a CATEGORIA do
     destino do lote (bloqueio, descarte, reprocessamento). Não é o que foi
     feito, nem quem fez, nem quando. Numa reprovação de embalagem, "bloqueio"
     não diz "trocamos a bobina e refizemos a selagem do lote 12" — e é essa
     frase que a auditoria pede um ano depois.

O NOME É COPIADO NA GRAVAÇÃO. O parâmetro cadastrado pode ser renomeado ou
desativado depois; o laudo tem de continuar dizendo o que dizia no dia. Pelo
mesmo motivo os limites vêm junto: um laudo que aprovou com Brix entre 10 e 14
não pode virar reprovado porque alguém apertou a faixa no mês seguinte.
"""
from django.conf import settings
from django.db import models


class ItemAnalise(models.Model):
    """Um parâmetro conferido numa análise, com o veredito e o que se fez."""

    class Situacao(models.TextChoices):
        # PENDENTE é o estado de nascimento: o checklist é montado inteiro e
        # preenchido depois. Sem ele, item não medido seria indistinguível de
        # item conforme -- que é exatamente como um laudo sai sem o pH.
        PENDENTE = 'pendente', 'Não conferido'
        CONFORME = 'conforme', 'Conforme'
        NAO_CONFORME = 'nao_conforme', 'Não conforme'
        NAO_APLICA = 'nao_aplica', 'Não se aplica'

    analise = models.ForeignKey(
        'qualidade.AnaliseQualidade', on_delete=models.CASCADE,
        related_name='itens',
    )
    parametro = models.ForeignKey(
        'qualidade.ParametroQualidadeProduto', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='itens_analise',
        help_text='O cadastro que originou esta linha, quando ainda existe.',
    )

    # ── Copiados na gravação ─────────────────────────────────────────────
    nome_parametro = models.CharField(max_length=60)
    unidade_medida = models.CharField(max_length=10, blank=True)
    valor_minimo = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
    )
    valor_maximo = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
    )
    obrigatorio = models.BooleanField(default=True)
    ordem = models.PositiveSmallIntegerField(default=0)

    # ── O que foi medido ─────────────────────────────────────────────────
    valor_numero = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
    )
    valor_texto = models.CharField(max_length=160, blank=True)

    situacao = models.CharField(
        max_length=14, choices=Situacao.choices, default=Situacao.PENDENTE,
        db_index=True,
    )
    observacao = models.CharField(max_length=300, blank=True)

    # ── A ação corretiva, quando não conforme ────────────────────────────
    acao_corretiva = models.TextField(
        blank=True,
        help_text='O que foi feito — não a categoria, a ação.',
    )
    acao_responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='acoes_corretivas_qualidade',
    )
    acao_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'analises_qualidade_itens'
        ordering = ['analise', 'ordem', 'nome_parametro']
        indexes = [
            models.Index(fields=['analise', 'situacao']),
            models.Index(fields=['situacao']),
        ]
        verbose_name = 'Item da análise'
        verbose_name_plural = 'Itens da análise'

    def __str__(self):
        return f'{self.nome_parametro}: {self.get_situacao_display()}'

    # ── Leitura ──────────────────────────────────────────────────────────

    @property
    def valor(self):
        """O que foi medido, seja número ou texto."""
        if self.valor_numero is not None:
            return self.valor_numero
        return self.valor_texto

    @property
    def preenchido(self) -> bool:
        return self.valor_numero is not None or bool(self.valor_texto.strip())

    @property
    def pendente_obrigatorio(self) -> bool:
        """
        Obrigatório e ainda sem resposta. É o que impede um laudo de fechar —
        e o que a tela precisa apontar antes de alguém assinar.
        """
        return self.obrigatorio and self.situacao == self.Situacao.PENDENTE

    @staticmethod
    def _numero(valor) -> str:
        """
        Decimal sem os zeros da casa decimal do banco.

        `:g` corta zeros em float, mas NÃO em Decimal: `Decimal("11.000")`
        sai "11.000" e a faixa vira "11.000 a 16.000" na tela. E `.normalize()`
        seria pior -- devolve "1.1E+1".
        """
        return f'{valor:f}'.rstrip('0').rstrip('.') or '0'

    @property
    def faixa(self) -> str:
        """A faixa exigida, em texto, para a tela e para o laudo."""
        if self.valor_minimo is None and self.valor_maximo is None:
            return ''
        unidade = f' {self.unidade_medida}' if self.unidade_medida else ''
        minimo = self._numero(self.valor_minimo) if self.valor_minimo is not None else ''
        maximo = self._numero(self.valor_maximo) if self.valor_maximo is not None else ''
        if minimo and maximo:
            return f'{minimo} a {maximo}{unidade}'
        if minimo:
            return f'mín. {minimo}{unidade}'
        return f'máx. {maximo}{unidade}'

    def avaliar(self) -> str:
        """
        O veredito que o NÚMERO permite dar sozinho.

        Só decide quando há faixa numérica e valor numérico: fora disso, quem
        julga é a pessoa. "Aparência", "odor" e "textura" não têm mínimo e
        máximo, e inventar um veredito para eles seria pior que não ter — um
        conforme automático em campo subjetivo é um carimbo que ninguém deu.
        """
        if self.valor_numero is None:
            return self.situacao
        if self.valor_minimo is None and self.valor_maximo is None:
            return self.situacao
        if self.valor_minimo is not None and self.valor_numero < self.valor_minimo:
            return self.Situacao.NAO_CONFORME
        if self.valor_maximo is not None and self.valor_numero > self.valor_maximo:
            return self.Situacao.NAO_CONFORME
        return self.Situacao.CONFORME
