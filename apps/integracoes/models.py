import hashlib
import ipaddress
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


ESCOPOS_DISPONIVEIS = {
    'filiais:ler': 'Consultar filiais',
    'clientes:ler': 'Consultar clientes e contatos',
    'produtos:ler': 'Consultar produtos do ERP',
    'estoque:ler': 'Consultar saldos de estoque',
    'moda:ler': 'Consultar produtos, cores, tamanhos e variantes de confecção',
    'ops:ler': 'Consultar pedidos, ordens e peças de produção',
}


class CredencialIntegracao(models.Model):
    """Chave de máquina, vinculada a uma empresa e armazenada somente como hash."""

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.PROTECT, related_name='credenciais_integracao',
    )
    nome = models.CharField(max_length=120)
    prefixo = models.CharField(max_length=20, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    escopos = models.JSONField(default=list)
    filiais = models.ManyToManyField(
        'core.Filial', blank=True, related_name='credenciais_integracao',
        help_text='Em branco, permite todas as filiais ativas da empresa.',
    )
    ips_permitidos = models.JSONField(
        default=list, blank=True,
        help_text='IPs ou redes CIDR. Em branco, permite qualquer origem.',
    )
    ativo = models.BooleanField(default=True, db_index=True)
    expira_em = models.DateTimeField(null=True, blank=True)
    ultimo_uso_em = models.DateTimeField(null=True, blank=True)
    ultimo_ip = models.GenericIPAddressField(null=True, blank=True)
    criado_por = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='credenciais_integracao_criadas',
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'integracoes_credenciais_api'
        ordering = ['empresa__razao_social', 'nome']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'nome'], name='uq_integracao_nome_empresa',
            ),
        ]
        verbose_name = 'Credencial de integração'
        verbose_name_plural = 'Credenciais de integração'

    def __str__(self):
        return f'{self.empresa} — {self.nome} ({self.prefixo})'

    @property
    def is_authenticated(self):
        return True

    @property
    def escopos_set(self):
        return set(self.escopos or [])

    def possui_escopo(self, escopo):
        return '*' in self.escopos_set or escopo in self.escopos_set

    def esta_expirada(self):
        return bool(self.expira_em and self.expira_em <= timezone.now())

    def permite_ip(self, ip):
        redes = self.ips_permitidos or []
        if not redes:
            return True
        try:
            endereco = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for rede in redes:
            try:
                if endereco in ipaddress.ip_network(rede, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def clean(self):
        super().clean()
        invalidos = sorted(set(self.escopos or []) - set(ESCOPOS_DISPONIVEIS) - {'*'})
        if invalidos:
            raise ValidationError({'escopos': f'Escopos desconhecidos: {", ".join(invalidos)}.'})
        for rede in self.ips_permitidos or []:
            try:
                ipaddress.ip_network(rede, strict=False)
            except ValueError as exc:
                raise ValidationError({'ips_permitidos': f'IP ou CIDR inválido: {rede}.'}) from exc

    @classmethod
    def criar(cls, *, empresa, nome, escopos=None, criado_por=None, expira_em=None):
        segredo = secrets.token_urlsafe(32)
        prefixo = secrets.token_hex(5)
        token = f'ited_{prefixo}.{segredo}'
        credencial = cls.objects.create(
            empresa=empresa,
            nome=nome,
            prefixo=prefixo,
            token_hash=cls.hash_token(token),
            escopos=list(escopos or ESCOPOS_DISPONIVEIS),
            criado_por=criado_por,
            expira_em=expira_em,
        )
        return credencial, token

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()
