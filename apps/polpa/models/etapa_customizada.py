"""
Etapas que a indústria cria — o que as trinta e quatro do vocabulário não têm.

AS TRINTA E QUATRO NÃO SAEM. Elas são o vocabulário comum: é por elas que o
rendimento por etapa soma entre produtos diferentes, que o painel compara
despolpamento de manga com despolpamento de acerola, e que a tela sabe pedir
temperatura na pasteurização. Trocá-las por uma tabela vazia obrigaria cada
fábrica a inventar o próprio vocabulário e acabaria com a comparação.

O QUE FALTAVA ERA O RESTO. Uma fábrica de congelados tempera; uma de defumados
defuma; uma de doces apura ponto. Nenhuma dessas está — nem deveria estar — na
lista de uma fábrica de polpa. Sem onde cadastrá-las, a etapa vira INSTRUÇÃO
solta na receita: aparece na tela, mas não recebe apontamento, e portanto não
entra no rendimento nem na perda. A etapa mais cara do processo fica invisível
justamente para os números que ela move.

O CÓDIGO É O QUE LIGA TUDO. `ApontamentoEtapa.etapa` guarda um código, e o
`EtapaReceita.etapa` também. Um código cadastrado aqui atravessa o sistema
igual aos outros — o que esta tabela acrescenta é o NOME que a tela mostra, a
POSIÇÃO onde ele entra na sequência, e o que ele exige medir.

POR FILIAL, e não global: a mesma empresa pode ter uma unidade que defuma e
outra que não, e uma lista global encheria a tela da segunda com etapa que ela
nunca vai apontar.
"""
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class EtapaProcesso(FilialScopedModel):
    """Uma etapa de processo criada pela indústria."""

    codigo = models.SlugField(
        max_length=20,
        help_text='Sem espaço nem acento: "fermentacao", "defumacao".',
    )
    nome = models.CharField(
        max_length=60, help_text='Como aparece na tela: "Fermentação".',
    )
    # ONDE ELA ENTRA NA FILA. As canônicas ocupam de 0 a 33; um número entre
    # elas encaixa a etapa nova no meio do processo. Sem isso ela cairia no
    # fim da lista — e uma fermentação depois do congelamento não descreve
    # fábrica nenhuma.
    sequencia = models.PositiveSmallIntegerField(
        default=99,
        help_text='Posição na sequência. As etapas do vocabulário vão de 0 a 33.',
    )

    # O QUE ELA EXIGE MEDIR. É o que faz a etapa entrar no rendimento: sem
    # peso de entrada e saída não há perda a calcular, e a etapa vira só um
    # carimbo de "passou por aqui".
    exige_peso = models.BooleanField(
        default=True,
        help_text='Pede entrada e saída — é o que alimenta perda e rendimento.',
    )
    exige_temperatura = models.BooleanField(default=False)

    instrucao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_etapas_processo'
        ordering = ['sequencia', 'nome']
        unique_together = [('filial', 'codigo')]
        indexes = [models.Index(fields=['filial', 'ativo'])]
        verbose_name = 'Etapa de processo'
        verbose_name_plural = 'Etapas de processo'

    def __str__(self):
        return self.nome

    def clean(self):
        """
        O código não pode colidir com o vocabulário comum.

        Uma "pasteurizacao" da casa sobrescreveria o rótulo e as regras da
        canônica em uma filial só — e o mesmo código passaria a significar
        duas coisas no mesmo banco, que é como um indicador deixa de somar.
        """
        from django.core.exceptions import ValidationError

        from apps.polpa.models.processo import Etapa

        if self.codigo in Etapa.values:
            raise ValidationError({
                'codigo': (
                    f'"{self.codigo}" já é uma etapa do vocabulário comum — '
                    f'use-a na receita em vez de recriá-la.'
                ),
            })
