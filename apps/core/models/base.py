"""
Modelos base: timestamps, manager com escopo de filial e abstract model para isolamento multi-filial.
"""
import hashlib

from django.db import models


class TimestampedModel(models.Model):
    """Adiciona campos de auditoria temporal em todos os registros."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CoordenadaMixin(models.Model):
    """
    Coordenadas geográficas + rastro da geocodificação.

    Vive aqui (e não em `apps.mapas`) porque é herdado por models de
    `cadastros`/`core` e o app de mapas consulta esses models — colocar o
    mixin lá criaria import circular.

    Pressupõe que a entidade tenha os campos de endereço padrão do
    sistema (`endereco`, `numero`, `bairro`, `cidade`, `uf`, `cep`), o que
    vale para Cliente, Fornecedor, Filial, Motorista e Transportadora.

    Usa float (não Decimal) porque as funções da extensão `earthdistance`
    operam em float8 — casar os tipos mantém o índice GIST por expressão
    `gist(ll_to_earth(latitude, longitude))` utilizável nas consultas.
    """

    class Precisao(models.TextChoices):
        EXATA = 'exata', 'Exata (número)'
        APROXIMADA = 'aproximada', 'Aproximada (rua/bairro)'
        CIDADE = 'cidade', 'Centro da cidade'
        MANUAL = 'manual', 'Ajustada manualmente'

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geo_precisao = models.CharField(max_length=12, choices=Precisao.choices, blank=True)
    geo_atualizado_em = models.DateTimeField(null=True, blank=True)
    # Hash do endereço que gerou a coordenada atual. Se o endereço muda, o
    # hash deixa de casar e o registro volta para a fila de geocodificação.
    geo_endereco_hash = models.CharField(max_length=32, blank=True, db_index=True)
    # Coordenada arrastada/ajustada à mão: o lote automático não sobrescreve.
    geo_fixado = models.BooleanField(default=False)
    geo_erro = models.CharField(max_length=160, blank=True)

    class Meta:
        abstract = True

    @property
    def tem_coordenada(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def endereco_para_geocodificar(self) -> str:
        """Endereço em uma linha, no formato que os geocoders esperam."""
        rua = (getattr(self, 'endereco', '') or '').strip()
        numero = (getattr(self, 'numero', '') or '').strip()
        bairro = (getattr(self, 'bairro', '') or '').strip()
        cidade = (getattr(self, 'cidade', '') or '').strip()
        uf = (getattr(self, 'uf', '') or '').strip()
        cep = ''.join(filter(str.isdigit, str(getattr(self, 'cep', '') or '')))

        logradouro = f'{rua}, {numero}' if rua and numero else rua
        partes = [p for p in (logradouro, bairro, cidade, uf) if p]
        if cep and len(cep) == 8:
            partes.append(f'{cep[:5]}-{cep[5:]}')
        if partes:
            partes.append('Brasil')
        return ', '.join(partes)

    def hash_endereco_atual(self) -> str:
        """Impressão digital do endereço, para detectar alteração."""
        base = self.endereco_para_geocodificar().lower()
        return hashlib.md5(base.encode('utf-8')).hexdigest() if base else ''

    @property
    def geo_desatualizado(self) -> bool:
        """True quando falta coordenada ou o endereço mudou desde a última."""
        if self.geo_fixado:
            return False
        atual = self.hash_endereco_atual()
        if not atual:
            return False
        return (not self.tem_coordenada) or self.geo_endereco_hash != atual


class FilialManager(models.Manager):
    """
    Manager que filtra automaticamente registros pela filial ativa da request.

    Uso:
        class Produto(FilialScopedModel):
            objects = FilialManager()
            all_objects = models.Manager()  # Sem filtro (para admin/matriz)
    """

    def for_filial(self, filial):
        """Retorna apenas registros da filial informada."""
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filial=filial)

    def for_empresa(self, empresa):
        """Para perfil matriz: registros de todas as filiais da empresa."""
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filial__empresa=empresa)


class FilialScopedModel(TimestampedModel):
    """
    Model base para TODAS as tabelas operacionais (produtos, estoque, vendas, etc).

    Garante:
    - Campo filial (FK obrigatório)
    - Manager customizado com métodos de filtro
    - Índice em filial para performance
    """

    filial = models.ForeignKey(
        'core.Filial',
        on_delete=models.PROTECT,
        db_index=True,
        related_name='+',
        help_text='Filial proprietária do registro',
    )

    objects = FilialManager()

    class Meta:
        abstract = True

class ActiveModel(models.Model):
    """Mixin que adiciona campo ativo para soft-delete."""
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        abstract = True
