"""
Personalização / arte aplicada a um item do pedido.

É lista, e não um bloco único, porque a mesma peça costuma levar técnicas
diferentes em locais diferentes. A ficha do Grupo Eureka diz exatamente
isso: "ESCUDO PATCH APLICADO DEMAIS IMPRESSÕES SUBLIMADAS" — patch no
escudo, sublimação no resto. Um campo só não expressaria as duas.

Fica no item, e não no pedido, porque "Local" (peito, costas, manga) só faz
sentido em relação a uma peça: num pedido de camisa + calção, cada uma tem
a sua.
"""
from django.core.validators import FileExtensionValidator
from django.db import models

# Extensões aceitas. CDR e AI entram porque é nelas que a arte costuma
# chegar do designer, mesmo que o navegador não saiba abrir -- ver
# `pode_pre_visualizar`.
EXTENSOES_ARTE = ['png', 'jpg', 'jpeg', 'pdf', 'cdr', 'svg', 'ai']

# Só estas o navegador desenha numa <img>. PDF abre em visualizador
# próprio; CDR e AI não abrem de jeito nenhum -- para essas, a tela
# oferece download em vez de fingir uma prévia quebrada.
EXTENSOES_COM_PREVIA = {'png', 'jpg', 'jpeg', 'svg'}


class Personalizacao(models.Model):
    """Uma aplicação de arte num item: técnica + local + arquivo."""

    class Tecnica(models.TextChoices):
        SUBLIMACAO = 'sublimacao', 'Sublimação'
        SILK = 'silk', 'Silk'
        BORDADO = 'bordado', 'Bordado'
        DTF = 'dtf', 'DTF'
        DTG = 'dtg', 'DTG'
        TRANSFER = 'transfer', 'Transfer'
        PATCH = 'patch', 'Patch'
        SEM_IMPRESSAO = 'sem_impressao', 'Sem impressão'
        OUTRO = 'outro', 'Outro'

    class Tipo(models.TextChoices):
        """O QUE é aplicado — a técnica diz COMO."""

        ARTE = 'arte', 'Arte / estampa'
        ESCUDO = 'escudo', 'Escudo / logo'
        NOME = 'nome', 'Nome'
        NUMERO = 'numero', 'Número'
        PATROCINIO = 'patrocinio', 'Patrocínio'
        OUTRO = 'outro', 'Outro'

    item = models.ForeignKey(
        'moda.ItemPedidoProducao', on_delete=models.CASCADE,
        related_name='personalizacoes',
    )

    tipo = models.CharField(
        max_length=20, choices=Tipo.choices, default=Tipo.ARTE,
        verbose_name='Tipo de impressão',
    )
    tecnica = models.CharField(
        max_length=120, choices=Tecnica.choices, default=Tecnica.SUBLIMACAO,
    )
    local = models.CharField(
        max_length=80, blank=True,
        help_text='Ex.: peito esquerdo, costas, manga direita.',
    )

    nome_personalizado = models.CharField(
        max_length=80, blank=True,
        help_text='Nome que vai na peça, quando houver.',
    )
    numero_personalizado = models.CharField(
        max_length=10, blank=True,
        help_text='Número que vai na peça (ex.: 25).',
    )

    patrocinios = models.TextField(
        blank=True, help_text='Quais marcas e onde entram.',
    )
    quantidade_patrocinadores = models.PositiveIntegerField(default=0)

    arquivo = models.FileField(
        upload_to='moda/artes/', blank=True, null=True,
        validators=[FileExtensionValidator(allowed_extensions=EXTENSOES_ARTE)],
        help_text='PNG, JPG, PDF, CDR, SVG ou AI.',
    )

    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_personalizacoes'
        ordering = ['id']
        verbose_name = 'Personalização'
        verbose_name_plural = 'Personalizações'

    def __str__(self):
        partes = [self.get_tecnica_display()]
        if self.local:
            partes.append(f'em {self.local}')
        return ' '.join(partes)

    # ── Arquivo ──────────────────────────────────────────────────────────

    @property
    def extensao(self) -> str:
        if not self.arquivo:
            return ''
        return self.arquivo.name.rsplit('.', 1)[-1].lower() if '.' in self.arquivo.name else ''

    @property
    def pode_pre_visualizar(self) -> bool:
        """
        Só PNG/JPG/SVG viram prévia na tela.

        CDR e AI o navegador não abre, e PDF precisa de visualizador. Tentar
        desenhar esses num <img> daria um quadrado quebrado, que é pior que
        assumir que não há prévia e oferecer o download.
        """
        return self.extensao in EXTENSOES_COM_PREVIA

    @property
    def e_pdf(self) -> bool:
        return self.extensao == 'pdf'

    @property
    def nome_arquivo(self) -> str:
        return self.arquivo.name.rsplit('/', 1)[-1] if self.arquivo else ''
