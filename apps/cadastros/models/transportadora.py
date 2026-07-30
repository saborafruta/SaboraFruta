"""Transportadora, Veículo e Representante."""
from django.db import models

from apps.core.constants.choices import UF
from apps.core.models.base import CoordenadaMixin, FilialManager, FilialScopedModel, TimestampedModel


class CadastroFilialManager(FilialManager):
    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filiais_vinculo__filial=filial, filiais_vinculo__ativo=True).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filiais_vinculo__filial__empresa=empresa, filiais_vinculo__ativo=True).distinct()


class Transportadora(CoordenadaMixin, FilialScopedModel):
    razao_social = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=100, blank=True)
    cnpj = models.CharField(max_length=14, blank=True, db_index=True)
    inscricao_estadual = models.CharField(max_length=20, blank=True)
    rntrc = models.CharField(
        max_length=20, blank=True,
        help_text='Registro ANTT obrigatório para transporte de carga',
    )

    endereco = models.CharField(max_length=255, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, choices=UF.choices, blank=True)
    cep = models.CharField(max_length=8, blank=True)
    codigo_municipio_ibge = models.CharField(max_length=7, blank=True)

    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = CadastroFilialManager()

    class Meta:
        db_table = 'transportadoras'
        ordering = ['razao_social']
        verbose_name = 'Transportadora'

    def __str__(self):
        return self.nome_fantasia or self.razao_social


class TransportadoraFilial(TimestampedModel):
    transportadora = models.ForeignKey(Transportadora, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='transportadoras_vinculadas')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'transportadoras_filiais'
        ordering = ['transportadora', 'filial']
        unique_together = [('transportadora', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['transportadora', 'ativo']),
        ]

    def __str__(self):
        return f'{self.transportadora} - {self.filial}'


class VeiculoTransportadora(TimestampedModel):
    class TipoRodado(models.TextChoices):
        TRUCK = 'Truck', 'Truck'
        TOCO = 'Toco', 'Toco'
        CARRETA = 'Carreta', 'Carreta'
        VUC = 'VUC', 'VUC'
        FURGAO = 'Furgão', 'Furgão'

    class TipoCarroceria(models.TextChoices):
        ABERTA = 'Aberta', 'Aberta'
        FECHADA = 'Fechada', 'Fechada'
        GRANELEIRA = 'Graneleira', 'Graneleira'
        PORTA_CONTAINER = 'Porta-container', 'Porta-container'
        SIDER = 'Sider', 'Sider'

    transportadora = models.ForeignKey(
        Transportadora, on_delete=models.CASCADE, related_name='veiculos',
    )
    descricao = models.CharField(max_length=100, blank=True)
    placa = models.CharField(max_length=8)
    uf_placa = models.CharField(max_length=2, choices=UF.choices, blank=True)
    rntrc = models.CharField(max_length=20, blank=True)
    tara = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    capacidade_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    capacidade_m3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    tipo_rodado = models.CharField(max_length=20, choices=TipoRodado.choices, blank=True)
    tipo_carroceria = models.CharField(max_length=20, choices=TipoCarroceria.choices, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'veiculos_transportadora'
        ordering = ['transportadora', 'placa']

    def __str__(self):
        return f'{self.placa} ({self.transportadora})'


class Motorista(CoordenadaMixin, FilialScopedModel):
    class CategoriaCNH(models.TextChoices):
        A = 'A', 'A'
        B = 'B', 'B'
        AB = 'AB', 'AB'
        C = 'C', 'C'
        D = 'D', 'D'
        E = 'E', 'E'
        ACC = 'ACC', 'ACC'

    nome = models.CharField(max_length=120)
    cpf = models.CharField(max_length=14, blank=True)
    rg = models.CharField(max_length=20, blank=True)
    cnh = models.CharField(max_length=20, blank=True, verbose_name='CNH')
    categoria_cnh = models.CharField(
        max_length=10, choices=CategoriaCNH.choices, blank=True,
        verbose_name='Categoria CNH',
    )
    validade_cnh = models.DateField(null=True, blank=True, verbose_name='Validade CNH')
    transportadora = models.ForeignKey(
        Transportadora, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='motoristas',
    )
    telefone = models.CharField(max_length=20, blank=True)
    celular = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    endereco = models.CharField(max_length=255, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, choices=UF.choices, blank=True)
    cep = models.CharField(max_length=8, blank=True)
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'motoristas'
        ordering = ['nome']
        verbose_name = 'Motorista'
        verbose_name_plural = 'Motoristas'
        indexes = [
            models.Index(fields=['filial', 'ativo']),
        ]

    def __str__(self):
        return self.nome


class Veiculo(FilialScopedModel):
    class TipoRodado(models.TextChoices):
        TRUCK = 'Truck', 'Truck'
        TOCO = 'Toco', 'Toco'
        CARRETA = 'Carreta', 'Carreta'
        VUC = 'VUC', 'VUC'
        FURGAO = 'Furgão', 'Furgão'
        VAN = 'Van', 'Van'
        MOTO = 'Moto', 'Moto'
        CARRO = 'Carro', 'Carro'

    class TipoCarroceria(models.TextChoices):
        ABERTA = 'Aberta', 'Aberta'
        FECHADA = 'Fechada', 'Fechada'
        GRANELEIRA = 'Graneleira', 'Graneleira'
        PORTA_CONTAINER = 'Porta-container', 'Porta-container'
        SIDER = 'Sider', 'Sider'
        BAU = 'Baú', 'Baú'
        CEGONHA = 'Cegonha', 'Cegonha'

    placa = models.CharField(max_length=8, db_index=True)
    descricao = models.CharField(max_length=120, blank=True)
    marca = models.CharField(max_length=60, blank=True)
    modelo = models.CharField(max_length=80, blank=True)
    ano_fabricacao = models.PositiveSmallIntegerField(null=True, blank=True)
    cor = models.CharField(max_length=40, blank=True)
    renavam = models.CharField(max_length=15, blank=True, verbose_name='RENAVAM')
    chassi = models.CharField(max_length=17, blank=True)
    uf_placa = models.CharField(max_length=2, choices=UF.choices, blank=True, verbose_name='UF da placa')
    tipo_rodado = models.CharField(max_length=20, choices=TipoRodado.choices, blank=True)
    tipo_carroceria = models.CharField(max_length=20, choices=TipoCarroceria.choices, blank=True)
    tara = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, help_text='Peso tara em kg')
    capacidade_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, verbose_name='Capacidade (kg)')
    capacidade_m3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, verbose_name='Capacidade (m³)')
    transportadora = models.ForeignKey(
        Transportadora, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='veiculos_filial',
    )
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'veiculos'
        ordering = ['placa']
        verbose_name = 'Veículo'
        verbose_name_plural = 'Veículos'
        indexes = [
            models.Index(fields=['filial', 'ativo']),
        ]

    def __str__(self):
        partes = [self.placa]
        if self.descricao:
            partes.append(self.descricao)
        elif self.marca or self.modelo:
            partes.append(f'{self.marca} {self.modelo}'.strip())
        return ' — '.join(partes)


class Representante(FilialScopedModel):
    nome = models.CharField(max_length=120)
    cpf = models.CharField(max_length=11, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    celular = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    comissao_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    regiao_atuacao = models.CharField(max_length=100, blank=True)
    meta_mensal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', help_text='Se o representante também é usuário do sistema',
    )
    ativo = models.BooleanField(default=True, db_index=True)

    objects = CadastroFilialManager()

    class Meta:
        db_table = 'representantes'
        ordering = ['nome']
        verbose_name = 'Representante'

    def __str__(self):
        return self.nome


class RepresentanteFilial(TimestampedModel):
    representante = models.ForeignKey(Representante, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='representantes_vinculados')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'representantes_filiais'
        ordering = ['representante', 'filial']
        unique_together = [('representante', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['representante', 'ativo']),
        ]

    def __str__(self):
        return f'{self.representante} - {self.filial}'
