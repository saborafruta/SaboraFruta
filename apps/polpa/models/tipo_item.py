"""
O tipo do item do catálogo — que era enum e virou cadastro.

POR QUE SAIU DO CÓDIGO. Os trinta e cinco tipos cobriam polpa, açaí e sorvete,
mas cada fábrica tem o seu: xarope, cobertura, insumo de limpeza, pote de vidro
retornável. Enquanto a lista morava no `TextChoices`, acrescentar um item exigia
deploy — e o que acontece na prática é alguém cadastrar tudo como "Outro
ingrediente", que é onde a informação morre.

A CLASSE CONTINUA FECHADA, e é de propósito. São três, e não são gosto: matéria-
prima, embalagem e acabado decidem o que a ficha pergunta, o que a receita
separa, o que o custo soma e onde o item aparece no menu. Um tipo sem classe
seria um item que o sistema não sabe processar; por isso a classe é obrigatória
no cadastro do tipo, escolhida entre as três.

OS TRINTA E CINCO CONTINUAM EXISTINDO, agora como linhas marcadas `sistema`.
Eles nascem na primeira vez que a filial abre a lista — semeados a partir do
mesmo enum, que passou a ser A SEMENTE e não mais a lista em uso. Semear na
leitura, e não numa migração de dados, é o que faz filial criada amanhã ter os
tipos também.
"""
from django.db import models

from apps.core.models.base import FilialScopedModel


class TipoItem(FilialScopedModel):
    """Um tipo de item do catálogo, do sistema ou criado pela fábrica."""

    codigo = models.SlugField(
        max_length=20,
        help_text='Identificador gravado na ficha. Não muda depois de criado.',
    )
    nome = models.CharField(max_length=60)
    classe = models.CharField(
        max_length=20,
        help_text='Decide o que a ficha pergunta e como a produção trata o item.',
    )
    ativo = models.BooleanField(default=True, db_index=True)
    # Marca os que vieram do enum. Serve para não oferecer exclusão de um tipo
    # que o próprio código referencia pelo nome em regra de negócio.
    sistema = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'polpa_tipos_item'
        ordering = ['classe', 'nome']
        unique_together = [('filial', 'codigo')]
        indexes = [
            models.Index(fields=['filial', 'classe', 'ativo']),
        ]
        verbose_name = 'Tipo de item'
        verbose_name_plural = 'Tipos de item'

    def __str__(self):
        return self.nome

    # ── Semeadura ────────────────────────────────────────────────────────

    @classmethod
    def garantir_padroes(cls, filial) -> int:
        """
        Cria os tipos de sistema desta filial, se ainda não existirem.

        CHAMADO NA LEITURA, e não numa migração de dados. Migração cuidaria das
        filiais de hoje e deixaria a de amanhã com a lista vazia — e uma lista
        vazia aqui é a tela do catálogo inteira sem opção nenhuma. Além disso
        migração de dado que falha derruba o release; isto não pode derrubar.

        `bulk_create` com `ignore_conflicts` porque duas abas abrindo a tela ao
        mesmo tempo é o caso normal, não a exceção: a `unique_together` decide,
        e a segunda simplesmente não insere.
        """
        from apps.polpa.models.catalogo import FichaProduto

        if filial is None:
            return 0
        existentes = set(
            cls.objects.filter(filial=filial).values_list('codigo', flat=True)
        )
        novos = [
            cls(
                filial=filial, codigo=tipo.value, nome=tipo.label,
                classe=classe.value, sistema=True, ativo=True,
            )
            for classe, tipos in FichaProduto.TIPOS_POR_CLASSE.items()
            for tipo in tipos
            if tipo.value not in existentes
        ]
        if novos:
            cls.objects.bulk_create(novos, ignore_conflicts=True)
        return len(novos)

    @classmethod
    def da_filial(cls, filial):
        """A lista em uso, já semeada."""
        cls.garantir_padroes(filial)
        return cls.objects.for_filial(filial).filter(ativo=True)

    @classmethod
    def por_classe(cls, filial) -> dict[str, list['TipoItem']]:
        """Agrupado como a tela mostra: um `optgroup` por classe."""
        from apps.polpa.models.catalogo import FichaProduto

        agrupado: dict[str, list[TipoItem]] = {
            classe.value: [] for classe in FichaProduto.Classe
        }
        for tipo in cls.da_filial(filial):
            agrupado.setdefault(tipo.classe, []).append(tipo)
        return agrupado

    @classmethod
    def classe_do_codigo(cls, filial, codigo: str) -> str:
        """
        A classe de um código, olhando a tabela e caindo no enum.

        O FALLBACK NÃO É REDUNDÂNCIA. A classe é derivada dentro do `save` da
        ficha, que roda em teste e em script sem filial semeada; sem o enum por
        baixo, uma ficha gravada nesse caminho nasceria sem classe — e classe
        vazia é o item que a receita não separa e o custo não soma.
        """
        from apps.polpa.models.catalogo import FichaProduto

        if filial is not None and codigo:
            achado = (
                cls.objects
                .filter(filial=filial, codigo=codigo)
                .values_list('classe', flat=True)
                .first()
            )
            if achado:
                return achado
        return FichaProduto.CLASSE_DO_TIPO.get(codigo, '')

    @classmethod
    def nome_do_codigo(cls, filial, codigo: str) -> str:
        """O rótulo para exibir, com a mesma queda para o enum."""
        from apps.polpa.models.catalogo import FichaProduto

        if filial is not None and codigo:
            achado = (
                cls.objects
                .filter(filial=filial, codigo=codigo)
                .values_list('nome', flat=True)
                .first()
            )
            if achado:
                return achado
        try:
            return FichaProduto.Tipo(codigo).label
        except ValueError:
            return codigo
