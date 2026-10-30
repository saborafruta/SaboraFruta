"""Regras de autorização e atribuição da carteira comercial."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet

from apps.cadastros.models import Cliente
from apps.core.tenant_context import tenant_atomic
from apps.crm.models import CarteiraCliente


class CarteiraService:
    @staticmethod
    def pode_supervisionar(usuario) -> bool:
        perfil = getattr(usuario, 'perfil', None)
        return bool(
            usuario.is_superuser
            or (perfil and perfil.is_admin)
            or usuario.tem_permissao('crm', 'aprovar')
        )

    @classmethod
    def filtrar_clientes(cls, queryset: QuerySet, *, usuario, empresa):
        queryset = queryset.filter(filial__empresa=empresa)
        if cls.pode_supervisionar(usuario):
            return queryset
        grupos = CarteiraCliente.objects.filter(
            empresa=empresa, vendedor=usuario,
        ).values('grupo_cliente')
        return queryset.filter(grupo_replicacao__in=grupos)

    @classmethod
    def filtrar_oportunidades(cls, queryset: QuerySet, *, usuario, empresa):
        if cls.pode_supervisionar(usuario):
            return queryset
        grupos = CarteiraCliente.objects.filter(
            empresa=empresa, vendedor=usuario,
        ).values('grupo_cliente')
        return queryset.filter(cliente__grupo_replicacao__in=grupos)

    @classmethod
    def usuario_pode_acessar_cliente(cls, *, usuario, cliente) -> bool:
        if usuario.empresa_id != cliente.filial.empresa_id:
            return False
        if cls.pode_supervisionar(usuario):
            return True
        return CarteiraCliente.objects.filter(
            empresa_id=usuario.empresa_id,
            grupo_cliente=cliente.grupo_replicacao,
            vendedor=usuario,
        ).exists()

    @classmethod
    @tenant_atomic
    def atribuir(cls, *, cliente, vendedor, atribuido_por):
        empresa = cliente.filial.empresa
        if not cls.pode_supervisionar(atribuido_por):
            raise PermissionDenied('Somente um gestor pode alterar a carteira de clientes.')
        if vendedor.empresa_id != empresa.pk:
            raise ValidationError('O vendedor pertence a outra empresa.')
        carteira = CarteiraCliente.objects.filter(
            empresa=empresa, grupo_cliente=cliente.grupo_replicacao,
        ).first()
        if carteira:
            carteira.cliente = cliente
            carteira.vendedor = vendedor
            carteira.atribuido_por = atribuido_por
            carteira.save()
            return carteira, False
        return CarteiraCliente.objects.create(
            empresa=empresa,
            cliente=cliente,
            vendedor=vendedor,
            atribuido_por=atribuido_por,
        ), True

    @classmethod
    def validar_responsavel(cls, *, cliente, responsavel, usuario):
        if not cls.usuario_pode_acessar_cliente(usuario=usuario, cliente=cliente):
            raise ValidationError('O cliente não pertence à sua carteira.')
        carteira = CarteiraCliente.objects.filter(
            empresa_id=cliente.filial.empresa_id,
            grupo_cliente=cliente.grupo_replicacao,
        ).first()
        if not carteira:
            raise ValidationError('Defina o vendedor responsável pelo cliente antes de continuar.')
        if responsavel and responsavel.pk != carteira.vendedor_id:
            raise ValidationError('O responsável da oportunidade deve ser o vendedor da carteira.')
        return carteira.vendedor

    @classmethod
    def clientes_disponiveis_na_filial(cls, *, filial, usuario):
        queryset = Cliente.objects.for_filial(filial).filter(ativo=True)
        return cls.filtrar_clientes(
            queryset, usuario=usuario, empresa=filial.empresa,
        )
