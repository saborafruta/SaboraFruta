from django.db import transaction

from apps.core.models import PerfilAcesso, Permissao


class PerfilPadraoService:
    """Garante os perfis operacionais minimos de cada empresa."""

    PERFIS = (
        {
            'nome': 'Administrador',
            'descricao': 'Acesso total ao sistema.',
            'is_admin': True,
            'permissoes': {},
        },
        {
            'nome': 'Gerente',
            'descricao': 'Gerencia a operacao da empresa, sem acesso administrativo global.',
            'is_admin': False,
            'permissoes': {
                modulo: {
                    'pode_ver': True,
                    'pode_criar': True,
                    'pode_editar': True,
                    'pode_cancelar': True,
                    'pode_aprovar': True,
                    'pode_exportar': True,
                }
                for modulo in (
                    'vendas',
                    'estoque',
                    'compras',
                    'producao',
                    'cadastros',
                    'produtos',
                    'relatorios',
                )
            },
        },
        {
            'nome': 'Operador',
            'descricao': 'Opera vendas, PDV e movimentacoes basicas de estoque.',
            'is_admin': False,
            'permissoes': {
                modulo: {'pode_ver': True, 'pode_criar': True}
                for modulo in ('vendas', 'estoque', 'pdv')
            },
        },
    )

    @classmethod
    def garantir_para_empresa(cls, empresa, using='default'):
        criados = []
        with transaction.atomic(using=using):
            for definicao in cls.PERFIS:
                perfil, criado = PerfilAcesso.objects.using(using).get_or_create(
                    empresa_id=empresa.pk,
                    nome=definicao['nome'],
                    defaults={
                        'descricao': definicao['descricao'],
                        'is_admin': definicao['is_admin'],
                        'ativo': True,
                    },
                )
                if criado:
                    criados.append(perfil.nome)
                elif definicao['is_admin'] and (not perfil.is_admin or not perfil.ativo):
                    perfil.is_admin = True
                    perfil.ativo = True
                    perfil.save(using=using, update_fields=['is_admin', 'ativo', 'updated_at'])

                for modulo, defaults in definicao['permissoes'].items():
                    Permissao.objects.using(using).get_or_create(
                        perfil=perfil,
                        modulo=modulo,
                        defaults=defaults,
                    )
        return criados
