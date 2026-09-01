"""
Usuário (customizado) + PerfilAcesso + Permissões por módulo + Sessão JWT.
"""
from io import BytesIO
from pathlib import Path

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone
from PIL import Image, ImageOps

from .base import TimestampedModel


class UsuarioManager(BaseUserManager):
    def create_user(self, email, nome, password=None, **extra_fields):
        if not email:
            raise ValueError('Usuário precisa de e-mail.')
        email = self.normalize_email(email)
        usuario = self.model(email=email, nome=nome, **extra_fields)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, email, nome, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('ativo', True)
        return self.create_user(email, nome, password, **extra_fields)


class PerfilAcesso(TimestampedModel):
    """Agrupa permissões. Ex: Administrador, Vendedor, Gerente de Estoque, Operador PDV."""

    empresa = models.ForeignKey('core.Empresa', on_delete=models.CASCADE, related_name='perfis')
    nome = models.CharField(max_length=80)
    descricao = models.TextField(blank=True)
    is_admin = models.BooleanField(default=False)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'perfis_acesso'
        unique_together = [('empresa', 'nome')]
        ordering = ['nome']
        verbose_name = 'Perfil de Acesso'
        verbose_name_plural = 'Perfis de Acesso'

    def __str__(self):
        return self.nome


class Permissao(models.Model):
    """
    Permissão granular por módulo para um perfil.
    Módulos: vendas, estoque, financeiro, fiscal, config, relatorios, pdv, producao, compras, cadastros.
    """

    class Modulo(models.TextChoices):
        VENDAS = 'vendas', 'Vendas'
        ESTOQUE = 'estoque', 'Estoque'
        FINANCEIRO = 'financeiro', 'Financeiro'
        FISCAL = 'fiscal', 'Fiscal'
        CONFIG = 'config', 'Configurações'
        RELATORIOS = 'relatorios', 'Relatórios'
        PDV = 'pdv', 'PDV'
        PRODUCAO = 'producao', 'Produção'
        QUALIDADE = 'qualidade', 'Qualidade'
        COMPRAS = 'compras', 'Compras'
        CADASTROS = 'cadastros', 'Cadastros'
        PRODUTOS = 'produtos', 'Produtos'
        LOGISTICA = 'logistica', 'Logistica'
        CASHBACK = 'cashback', 'Cashback'
        CRM = 'crm', 'CRM'
        MAPAS = 'mapas', 'Mapas e Geolocalização'
        FOOD_SERVICE = 'food_service', 'Food Service'
        MODA = 'moda', 'Moda e Confecção'

        # Áreas do vertical Moda. `moda` continua valendo como guarda-
        # chuva para os perfis antigos; estas estreitam por posto de
        # trabalho. Ver `apps.moda.permissoes`.
        MODA_COMERCIAL = 'moda_comercial', 'Moda: Comercial'
        MODA_PCP = 'moda_pcp', 'Moda: PCP'
        MODA_CORTE = 'moda_corte', 'Moda: Corte'
        MODA_PRODUCAO = 'moda_producao', 'Moda: Produção'
        MODA_QUALIDADE = 'moda_qualidade', 'Moda: Qualidade'
        MODA_EXPEDICAO = 'moda_expedicao', 'Moda: Expedição'
        MODA_INDICADORES = 'moda_indicadores', 'Moda: Indicadores'

    perfil = models.ForeignKey(
        PerfilAcesso, on_delete=models.CASCADE, related_name='permissoes',
    )
    modulo = models.CharField(max_length=60, choices=Modulo.choices)
    pode_ver = models.BooleanField(default=False)
    pode_criar = models.BooleanField(default=False)
    pode_editar = models.BooleanField(default=False)
    pode_excluir = models.BooleanField(default=False)
    pode_cancelar = models.BooleanField(default=False)
    pode_aprovar = models.BooleanField(default=False)
    pode_exportar = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'permissoes'
        unique_together = [('perfil', 'modulo')]
        ordering = ['perfil', 'modulo']

    def __str__(self):
        return f'{self.perfil.nome} - {self.modulo}'


class Usuario(AbstractBaseUser, PermissionsMixin):
    """
    Usuário do sistema. Vinculado a uma empresa e (opcionalmente) a uma filial padrão.
    Pode trocar de filial durante a sessão se o perfil permitir.
    """

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.PROTECT, related_name='usuarios',
    )
    filial = models.ForeignKey(
        'core.Filial', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='usuarios', help_text='Filial padrão do usuário',
    )
    perfil = models.ForeignKey(
        PerfilAcesso, on_delete=models.PROTECT, related_name='usuarios',
    )

    nome = models.CharField(max_length=120)
    cpf = models.CharField(max_length=11, blank=True)
    email = models.EmailField(max_length=120, unique=True)
    telefone = models.CharField(max_length=20, blank=True)
    foto = models.ImageField(upload_to='usuarios/fotos/', blank=True, null=True)
    menu_favoritos = models.JSONField(default=list, blank=True)
    preferencias_tabelas = models.JSONField(default=dict, blank=True)

    comissao_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # PIN para PDV (operações rápidas sem login completo)
    pin_code = models.CharField(max_length=6, blank=True)
    pin_exige_supervisor = models.BooleanField(default=False)

    ultimo_acesso = models.DateTimeField(null=True, blank=True)
    ip_ultimo_acesso = models.GenericIPAddressField(null=True, blank=True)
    tentativas_login_falhas = models.SmallIntegerField(default=0)
    bloqueado_ate = models.DateTimeField(null=True, blank=True)

    ativo = models.BooleanField(default=True, db_index=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome']

    objects = UsuarioManager()

    class Meta:
        db_table = 'usuarios'
        ordering = ['nome']
        verbose_name = 'Usuário'
        verbose_name_plural = 'Usuários'

    def __str__(self):
        return f'{self.nome} ({self.email})'

    def _otimizar_foto(self):
        if not self.foto:
            return
        if getattr(self.foto, '_committed', True):
            return
        try:
            arquivo = getattr(self.foto, 'file', None)
        except (FileNotFoundError, ValueError):
            return
        if not arquivo:
            return
        try:
            arquivo.seek(0)
            imagem = Image.open(arquivo)
            imagem = ImageOps.exif_transpose(imagem)
            imagem = imagem.convert('RGB')

            largura, altura = imagem.size
            lado = min(largura, altura)
            esquerda = (largura - lado) // 2
            topo = (altura - lado) // 2
            imagem = imagem.crop((esquerda, topo, esquerda + lado, topo + lado))
            imagem = imagem.resize((512, 512), Image.Resampling.LANCZOS)

            buffer = BytesIO()
            imagem.save(buffer, format='JPEG', quality=92, optimize=True, progressive=True)
            nome_base = Path(self.foto.name).stem or 'foto'
            self.foto.save(f'{nome_base}.jpg', ContentFile(buffer.getvalue()), save=False)
        except Exception:
            try:
                arquivo.seek(0)
            except Exception:
                pass

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or 'foto' in update_fields:
            self._otimizar_foto()
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.ativo and (self.bloqueado_ate is None or self.bloqueado_ate < timezone.now())

    def tem_permissao(self, modulo: str, acao: str = 'ver') -> bool:
        """
        Verifica se o usuário tem permissão em um módulo.
        acao: 'ver', 'criar', 'editar', 'excluir', 'cancelar', 'aprovar', 'exportar'.
        """
        perfil = getattr(self, '_perfil_ativo', None) or self.perfil
        if self.is_superuser or perfil.is_admin:
            return True
        try:
            perm = perfil.permissoes.get(modulo=modulo)
        except Permissao.DoesNotExist:
            umbrela = self._guarda_chuva_vertical(perfil, modulo)
            if umbrela:
                return self.tem_permissao(umbrela, acao)
            return False
        return getattr(perm, f'pode_{acao}', False)

    # Verticais que quebram o módulo em ÁREAS (`moda_corte`, `polpa_frio`).
    # Está aqui, e não em cada vertical, porque quem responde a pergunta é o
    # usuário: espalhar a lista faria o vertical seguinte esquecer de se
    # registrar e trancar para fora quem só tem o módulo guarda-chuva.
    VERTICAIS_COM_AREA = ('moda', 'polpa')

    @classmethod
    def _guarda_chuva_vertical(cls, perfil, modulo: str) -> str | None:
        """
        Perfil que tem o VERTICAL mas não a área — devolve o módulo pai.

        As áreas (`moda_corte`, `polpa_recebimento`...) nascem depois do
        módulo. Sem esta regra, o dia em que elas entram tranca para fora
        todo perfil já existente: nenhum tem linha de área, e todos passam a
        receber 'não'. Vale igual para o vertical novo -- conceder `polpa`
        na tela de perfis precisa bastar para entrar, senão a pessoa recebe
        um menu que não abre nada e conclui que o módulo está quebrado.

        A condição separa os dois mundos: só cai no guarda-chuva quem NÃO
        tem nenhuma área daquele vertical cadastrada. Perfil montado com as
        áreas responde pela área e ponto -- senão o Comercial, que tem
        `moda` com tudo por causa da própria área, herdaria o corte inteiro
        por não ter linha de corte.
        """
        pai = modulo.split('_', 1)[0]
        if pai == modulo or pai not in cls.VERTICAIS_COM_AREA:
            return None
        if perfil.permissoes.filter(modulo__startswith=f'{pai}_').exists():
            return None
        return pai

    def filiais_permitidas(self):
        """
        As unidades que este login pode abrir. UMA REGRA SO', e todo mundo
        pergunta aqui.

        Ela existia em tres lugares -- a tela de escolha, o middleware e o
        seletor do cabecalho -- e os tres discordavam. Discordancia em regra
        de acesso nao aparece como erro: aparece como unidade a mais na
        lista, que a pessoa clica e entra.

        A ORDEM IMPORTA. Vinculo explicito (`UsuarioFilialAcesso`) vem antes
        do perfil: se alguem se deu ao trabalho de dizer em quais unidades
        este login trabalha, essa e' a fronteira -- inclusive para perfil
        administrador, que responde pelo QUE a pessoa faz, nao por ONDE.
        Era exatamente aqui que o login de uma filial via as tres.

        Sem nenhum vinculo, o admin da empresa continua enxergando tudo --
        senao a mudanca trancaria para fora quem foi cadastrado antes de o
        vinculo existir. E `filial` sozinha e' a unidade padrao de quem nao
        e' admin.
        """
        from apps.core.models.empresa import Filial

        qs = Filial.objects.filter(ativo=True)
        if self.is_superuser:
            return qs
        qs = qs.filter(empresa_id=self.empresa_id)

        acessos = self.acessos_filiais.filter(ativo=True).values_list(
            'filial_id', flat=True,
        )
        if acessos:
            return qs.filter(pk__in=list(acessos))

        perfil = getattr(self, 'perfil', None)
        if perfil is not None and perfil.is_admin:
            return qs
        if self.filial_id:
            return qs.filter(pk=self.filial_id)
        return qs.none()

    def pode_acessar_filial(self, filial) -> bool:
        """A mesma lista de `filiais_permitidas`, respondida para uma unidade."""
        if self.is_superuser:
            return True
        return self.filiais_permitidas().filter(pk=filial.pk).exists()

    def perfil_para_filial(self, filial):
        if not filial:
            return self.perfil
        acesso = self.acessos_filiais.select_related('perfil').filter(
            filial_id=filial.id,
            ativo=True,
        ).first()
        return acesso.perfil if acesso else self.perfil


class UsuarioFilialAcesso(TimestampedModel):
    """Vincula um login a uma filial com um perfil especifico para aquela loja."""

    usuario = models.ForeignKey(
        Usuario, on_delete=models.CASCADE, related_name='acessos_filiais',
    )
    filial = models.ForeignKey(
        'core.Filial', on_delete=models.CASCADE, related_name='acessos_usuarios',
    )
    perfil = models.ForeignKey(
        PerfilAcesso, on_delete=models.PROTECT, related_name='acessos_usuarios',
    )
    ativo = models.BooleanField(default=True)
    is_padrao = models.BooleanField(default=False)

    class Meta:
        db_table = 'usuarios_filiais_acessos'
        unique_together = [('usuario', 'filial')]
        ordering = ['usuario__nome', 'filial__razao_social']
        verbose_name = 'Acesso do Usuario por Filial'
        verbose_name_plural = 'Acessos dos Usuarios por Filial'

    def __str__(self):
        return f'{self.usuario.email} -> {self.filial}'

    def save(self, *args, **kwargs):
        if self.perfil_id and self.filial_id and self.perfil.empresa_id != self.filial.empresa_id:
            raise ValueError('Perfil e filial devem pertencer a mesma empresa.')
        super().save(*args, **kwargs)


class SessaoUsuario(models.Model):
    """Rastreio de sessões JWT ativas por dispositivo/filial."""

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='sessoes')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=255)
    refresh_token_hash = models.CharField(max_length=255, blank=True)
    ip_acesso = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_serial = models.CharField(max_length=255, blank=True)
    data_expiracao = models.DateTimeField()
    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sessoes_usuario'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['usuario', 'ativo']),
            models.Index(fields=['token_hash']),
        ]
