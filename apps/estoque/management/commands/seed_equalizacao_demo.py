"""Popula um cenário de demonstração pro módulo de equalização de estoque (Fase 33)."""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class Command(BaseCommand):
    help = (
        "Cria (ou completa) duas filiais de uma empresa com um produto em excesso numa "
        "e em risco de ruptura na outra, pra' ver o Equilíbrio de Estoque sugerir "
        "transferência assim que o comando termina."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa-cnpj", dest="empresa_cnpj", default=None,
            help="CNPJ da empresa a usar. Sem isso, usa a primeira empresa cadastrada.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        empresa = self._resolver_empresa(options["empresa_cnpj"])
        filial_a, filial_b = self._garantir_duas_filiais(empresa)
        usuario = self._garantir_usuario_demo(empresa, filial_a)
        unidade = self._garantir_unidade(empresa, filial_a, filial_b)
        produto = self._garantir_produto(filial_a, filial_b, unidade)
        deposito_a = Deposito.objects.filter(filial=filial_a, is_padrao=True).first() \
            or Deposito.objects.create(filial=filial_a, nome="Geral", is_padrao=True)
        deposito_b = Deposito.objects.filter(filial=filial_b, is_padrao=True).first() \
            or Deposito.objects.create(filial=filial_b, nome="Geral", is_padrao=True)

        Estoque.objects.update_or_create(
            produto=produto, filial=filial_a, deposito=deposito_a,
            defaults={"quantidade_atual": Decimal("500"), "quantidade_disponivel": Decimal("500")},
        )
        Estoque.objects.update_or_create(
            produto=produto, filial=filial_b, deposito=deposito_b,
            defaults={"quantidade_atual": Decimal("10"), "quantidade_disponivel": Decimal("10")},
        )

        if not ItemVendaPDV.objects.filter(produto=produto, venda_pdv__filial=filial_b).exists():
            venda = VendaPDV.objects.create(
                filial=filial_b, numero_venda=999001, usuario=usuario,
                data_venda=timezone.now(), status="finalizada", valor_total=Decimal("2400"),
            )
            ItemVendaPDV.objects.create(
                venda_pdv=venda, produto=produto, numero_item=1, quantidade=Decimal("240"),
                unidade_medida=unidade.sigla, valor_unitario=produto.preco_venda, valor_total=Decimal("2400"),
            )

        self.stdout.write(self.style.SUCCESS(
            f'Cenário pronto: "{produto.descricao}" tem 500 un. em {filial_a} e 10 un. em {filial_b} '
            f'(vendendo ~8/dia lá). Abra Equilíbrio de Estoque para ver a sugestão de transferência.'
        ))

    def _resolver_empresa(self, cnpj):
        if cnpj:
            empresa = Empresa.objects.filter(cnpj=cnpj).first()
            if not empresa:
                raise CommandError(f"Nenhuma empresa com CNPJ {cnpj}.")
            return empresa
        empresa = Empresa.objects.first()
        if not empresa:
            raise CommandError("Nenhuma empresa cadastrada -- crie uma empresa antes de rodar este seed.")
        return empresa

    def _garantir_duas_filiais(self, empresa):
        filiais = list(Filial.objects.filter(empresa=empresa, ativo=True).order_by("pk")[:2])
        if len(filiais) >= 2:
            return filiais[0], filiais[1]
        proximo_sufixo = Filial.objects.filter(empresa=empresa).count() + 1
        while len(filiais) < 2:
            filiais.append(Filial.objects.create(
                empresa=empresa, razao_social=f"Filial Demo {proximo_sufixo}",
                nome_fantasia=f"Filial Demo {proximo_sufixo}",
                cnpj=f"{empresa.cnpj[:8]}{proximo_sufixo:04d}00", uf="RN",
                is_matriz=not Filial.objects.filter(empresa=empresa, is_matriz=True).exists(),
            ))
            proximo_sufixo += 1
        return filiais[0], filiais[1]

    def _garantir_usuario_demo(self, empresa, filial):
        usuario = Usuario.objects.filter(empresa=empresa).first()
        if usuario:
            return usuario
        perfil, _ = PerfilAcesso.objects.get_or_create(empresa=empresa, nome="Admin", defaults={"is_admin": True})
        return Usuario.objects.create_user(
            email=f"demo@{empresa.pk}.inoovated.local", nome="Usuário Demo", password="demo12345",
            empresa=empresa, filial=filial, perfil=perfil,
        )

    def _garantir_unidade(self, empresa, filial_a, filial_b):
        unidade, _ = UnidadeMedida.objects.get_or_create(
            empresa=empresa, sigla="UN", defaults={"descricao": "Unidade", "tipo": UnidadeMedida.Tipo.UNIDADE},
        )
        UnidadeMedidaFilial.objects.get_or_create(unidade=unidade, filial=filial_a)
        UnidadeMedidaFilial.objects.get_or_create(unidade=unidade, filial=filial_b)
        return unidade

    def _garantir_produto(self, filial_a, filial_b, unidade):
        produto, criado = Produto.objects.get_or_create(
            filial=filial_a, descricao="Polpa de Morango 100g (demo equalização)",
            defaults={
                "unidade_medida": unidade, "ncm": "20089900", "preco_venda": Decimal("10"),
                "preco_custo": Decimal("4"), "estoque_minimo": Decimal("0"),
            },
        )
        ProdutoFilial.objects.get_or_create(produto=produto, filial=filial_a)
        ProdutoFilial.objects.get_or_create(produto=produto, filial=filial_b)
        return produto
