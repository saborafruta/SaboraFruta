"""Modelos de Praça e Rota para logística."""
from django.db import models

from apps.core.constants.choices import UF
from apps.core.models.base import FilialScopedModel


class Praca(FilialScopedModel):
    """
    Praça de atendimento — território comercial.

    Nasceu textual (uma lista de cidades separadas por vírgula) e foi
    **evoluída** para território geográfico em vez de ganhar uma entidade
    paralela: quem já usa praça para precificação e roteamento continua
    funcionando, e quem desenhar o polígono no mapa passa a ter delimitação
    real. Os dois modos convivem:

    - `cidades` (texto): critério grosso, por município. Continua valendo.
    - `poligono`: delimitação precisa, usada pelo mapa e pela atribuição
      automática de clientes (ver `apps.mapas.services.territorio`).

    Sem PostGIS o polígono é um JSON de pontos e o teste de pertencimento
    roda em Python. Para isso não virar varredura da base inteira, a caixa
    envolvente fica materializada em `bbox_*`: ela recorta os candidatos
    usando o índice B-tree de (latitude, longitude) e só o resto passa pelo
    ponto-em-polígono.
    """
    nome = models.CharField(max_length=100)
    codigo = models.CharField(max_length=20, blank=True, help_text='Código interno da praça')
    uf = models.CharField(max_length=2, choices=UF.choices, blank=True)
    cidades = models.TextField(
        blank=True,
        help_text='Lista de cidades separadas por vírgula (ex: São Paulo, Guarulhos, Osasco)',
    )
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    # ── Território geográfico ────────────────────────────────────────────
    #: Anel externo do polígono: [[lat, lng], [lat, lng], ...]. Guardamos
    #: [lat, lng] (e não [lng, lat] do GeoJSON) para casar com a ordem que o
    #: Leaflet usa, que é quem lê e escreve isso.
    poligono = models.JSONField(null=True, blank=True)
    bbox_sul = models.FloatField(null=True, blank=True)
    bbox_norte = models.FloatField(null=True, blank=True)
    bbox_oeste = models.FloatField(null=True, blank=True)
    bbox_leste = models.FloatField(null=True, blank=True)

    representante = models.ForeignKey(
        'cadastros.Representante', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pracas', help_text='Responsável comercial pelo território',
    )
    supervisor = models.CharField(max_length=120, blank=True)
    cor = models.CharField(
        max_length=7, default='#3b82f6',
        help_text='Cor do território no mapa (hex).',
    )
    meta_mensal = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='Meta de faturamento do território, para o comparativo meta x realizado.',
    )

    class Meta:
        db_table = 'cadastros_pracas'
        ordering = ['nome']
        verbose_name = 'Praça'
        verbose_name_plural = 'Praças'

    def __str__(self):
        if self.codigo:
            return f'{self.codigo} — {self.nome}'
        return self.nome

    @property
    def lista_cidades(self):
        """Retorna a lista de cidades como uma lista Python."""
        if not self.cidades:
            return []
        return [c.strip() for c in self.cidades.split(',') if c.strip()]

    # ── Território ───────────────────────────────────────────────────────
    @property
    def tem_poligono(self) -> bool:
        return bool(self.poligono) and len(self.poligono) >= 3

    def definir_poligono(self, pontos) -> None:
        """
        Grava o polígono e materializa a caixa envolvente.

        A bbox é derivada aqui (e não no save) para que exista um único ponto
        onde polígono e bbox mudam juntos — se elas divergirem, a atribuição
        de clientes passa a ignorar silenciosamente parte do território.
        """
        limpos = []
        for ponto in (pontos or []):
            try:
                lat, lng = float(ponto[0]), float(ponto[1])
            except (TypeError, ValueError, IndexError):
                continue
            limpos.append([lat, lng])

        if len(limpos) < 3:
            self.poligono = None
            self.bbox_sul = self.bbox_norte = self.bbox_oeste = self.bbox_leste = None
            return

        self.poligono = limpos
        lats = [p[0] for p in limpos]
        lngs = [p[1] for p in limpos]
        self.bbox_sul, self.bbox_norte = min(lats), max(lats)
        self.bbox_oeste, self.bbox_leste = min(lngs), max(lngs)

    def contem_ponto(self, lat, lng) -> bool:
        """
        Ponto-em-polígono por ray casting.

        A bbox é testada primeiro porque descarta a maioria dos casos com 4
        comparações, antes de percorrer os vértices.
        """
        if not self.tem_poligono or lat is None or lng is None:
            return False
        if self.bbox_sul is not None and not (
            self.bbox_sul <= lat <= self.bbox_norte
            and self.bbox_oeste <= lng <= self.bbox_leste
        ):
            return False

        dentro = False
        pontos = self.poligono
        n = len(pontos)
        j = n - 1
        for i in range(n):
            lat_i, lng_i = pontos[i][0], pontos[i][1]
            lat_j, lng_j = pontos[j][0], pontos[j][1]
            # Conta os cruzamentos do raio horizontal que sai do ponto.
            if (lng_i > lng) != (lng_j > lng):
                lat_corte = lat_i + (lng - lng_i) / (lng_j - lng_i) * (lat_j - lat_i)
                if lat < lat_corte:
                    dentro = not dentro
            j = i
        return dentro


class Rota(FilialScopedModel):
    """
    Rota de entrega — agrupa praças e define o circuito de coletas/entregas.
    """
    nome = models.CharField(max_length=100)
    codigo = models.CharField(max_length=20, blank=True, help_text='Código interno da rota')
    descricao = models.TextField(blank=True)
    pracas = models.ManyToManyField(
        Praca,
        blank=True,
        related_name='rotas',
        verbose_name='Praças da rota',
    )
    motorista = models.ForeignKey(
        'cadastros.Motorista', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rotas', verbose_name='Motorista padrão',
    )
    veiculo = models.ForeignKey(
        'cadastros.Veiculo', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rotas', verbose_name='Veículo padrão',
    )
    # Campos textuais originais. Uma migration de dados casa o texto com os
    # cadastros reais e preenche as FKs acima; o que não casar (motorista
    # terceirizado, placa digitada errada) fica só aqui. São mantidos como
    # fallback de exibição para não perder essa informação — ver as
    # properties `motorista_nome`/`veiculo_placa`. Não usar em código novo.
    motorista_padrao = models.CharField(
        max_length=100, blank=True,
        help_text='LEGADO: preencha o campo Motorista. Mantido para registros sem cadastro.',
    )
    veiculo_padrao = models.CharField(
        max_length=20, blank=True,
        help_text='LEGADO: preencha o campo Veículo. Mantido para registros sem cadastro.',
    )
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'cadastros_rotas'
        ordering = ['nome']
        verbose_name = 'Rota'
        verbose_name_plural = 'Rotas'

    def __str__(self):
        if self.codigo:
            return f'{self.codigo} — {self.nome}'
        return self.nome

    @property
    def motorista_nome(self) -> str:
        """Nome do motorista: o cadastrado, senão o texto legado."""
        if self.motorista_id:
            return self.motorista.nome
        return self.motorista_padrao

    @property
    def veiculo_placa(self) -> str:
        """Placa do veículo: a cadastrada, senão o texto legado."""
        if self.veiculo_id:
            return self.veiculo.placa
        return self.veiculo_padrao
