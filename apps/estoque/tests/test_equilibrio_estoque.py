from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.compras.models import PedidoCompra
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


class EquilibrioEstoqueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Equilibrio LTDA", nome_fantasia="Rede Equilibrio",
            cnpj="82345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="82345678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="82345678000193", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="equilibrio@inoovated.com", nome="Usuario Equilibrio", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto de alto giro",
            ncm="20089900", estoque_minimo=Decimal("5"), preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=2, quantidade_disponivel=2,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=40, quantidade_disponivel=40,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_a, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=300,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=30,
            unidade_medida="UN", valor_unitario=10, valor_total=300,
        )

    def test_sugere_excedente_da_loja_parada_para_loja_de_alto_giro(self):
        resultado = calcular_equilibrio(
            empresa=self.empresa, dias_analise=30, dias_cobertura=14,
        )

        self.assertEqual(len(resultado["sugestoes"]), 1)
        sugestao = resultado["sugestoes"][0]
        self.assertEqual(sugestao["origem"], self.loja_b)
        self.assertEqual(sugestao["destino"], self.loja_a)
        self.assertEqual(sugestao["quantidade"], Decimal("35"))
        self.assertTrue(sugestao["produto_parado_origem"])
        self.assertEqual(Estoque.objects.get(filial=self.loja_b).quantidade_disponivel, Decimal("40"))

    def test_tela_exibe_sugestao_e_link_para_transferencia_quando_origem_ativa(self):
        from apps.estoque.views.equilibrio_estoque import EquilibrioEstoqueView

        request = RequestFactory().get(reverse("estoque:equilibrio-estoque"))
        request.user = self.usuario
        request.filial_ativa = self.loja_b
        request.session = {"filial_ativa_id": self.loja_b.pk}
        response = EquilibrioEstoqueView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto de alto giro")
        self.assertContains(response, "Criar transferência")
        self.assertContains(response, f"destino={self.loja_a.pk}")

    def test_produto_vinculado_a_uma_unica_filial_nao_e_comparado(self):
        ProdutoFilial.objects.filter(produto=self.produto, filial=self.loja_b).update(ativo=False)

        resultado = calcular_equilibrio(empresa=self.empresa)

        self.assertEqual(resultado["produtos_analisados"], 0)
        self.assertEqual(resultado["sugestoes"], [])

    def test_link_do_equilibrio_preenche_transferencia_com_validacao(self):
        from apps.estoque.views.outras_movimentacoes import TransferenciaLojaView

        request = RequestFactory().get(reverse("estoque:transferencia-lojas-create"), {
            "origem": "equilibrio",
            "destino": self.loja_a.pk,
            "produto": self.produto.pk,
            "quantidade": "12",
        })
        request.user = self.usuario
        request.filial_ativa = self.loja_b
        request.session = {"filial_ativa_id": self.loja_b.pk}
        response = TransferenciaLojaView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto de alto giro")
        self.assertContains(response, "Transferencia sugerida pelo equilibrio de estoque")


class EquilibrioComVendaB2BECompraEmAbertoTests(TestCase):
    """
    Fase 2 do equilibrio: a demanda tambem conta pedido de venda B2B (nao
    so' balcao do PDV), e uma compra em aberto com o fornecedor reduz o
    deficit sugerido -- sem isso o equilibrio mandaria de outra loja algo
    que ja esta chegando por outro canal.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede B2B LTDA", nome_fantasia="Rede B2B",
            cnpj="83345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="83345678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="83345678000193", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="b2b@inoovated.com", nome="Usuario B2B", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto B2B",
            ncm="20089900", estoque_minimo=Decimal("5"), preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=2, quantidade_disponivel=2,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=40, quantidade_disponivel=40,
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.loja_a, tipo_pessoa='J', razao_social='Fornecedor B2B',
            cpf_cnpj='84345678000194', uf='RN',
        )

    def _pedido_venda(self, quantidade, status=PedidoVenda.Status.FATURADO, numero='PV-1'):
        from apps.cadastros.models import Cliente

        cliente, _ = Cliente.objects.get_or_create(
            filial=self.loja_a, cpf_cnpj='11122233344',
            defaults={'razao_social': 'Cliente Atacado', 'cidade': 'Natal', 'uf': 'RN'},
        )
        pedido = PedidoVenda.objects.create(
            filial=self.loja_a, numero_pedido=numero, cliente=cliente,
            usuario=self.usuario, status=status, data_emissao=timezone.now(),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=self.produto, quantidade=Decimal(quantidade),
            valor_unitario=Decimal('10'), valor_bruto=Decimal(quantidade) * 10,
            valor_total=Decimal(quantidade) * 10,
        )
        return pedido

    def test_venda_b2b_faturada_conta_como_demanda(self):
        self._pedido_venda(30, status=PedidoVenda.Status.FATURADO)

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        self.assertEqual(len(resultado["sugestoes"]), 1)
        sugestao = resultado["sugestoes"][0]
        self.assertEqual(sugestao["destino"], self.loja_a)
        self.assertEqual(sugestao["destino_vendido"], Decimal("30"))

    def test_pedido_venda_ainda_nao_faturado_nao_conta(self):
        # Zera o minimo pra isolar o efeito da demanda: com ele em 5, a
        # loja A (saldo 2) geraria um deficit de baseline mesmo sem
        # nenhuma venda, e o teste deixaria de provar o que importa aqui.
        self.produto.estoque_minimo = Decimal('0')
        self.produto.save(update_fields=['estoque_minimo', 'updated_at'])
        self._pedido_venda(30, status=PedidoVenda.Status.EM_SEPARACAO)

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        self.assertEqual(resultado["sugestoes"], [])

    def test_compra_em_aberto_reduz_o_deficit_sugerido(self):
        self._pedido_venda(30, status=PedidoVenda.Status.FATURADO)
        pedido_compra = PedidoCompra.objects.create(
            filial=self.loja_a, fornecedor=self.fornecedor, usuario=self.usuario,
            numero_pedido='PC-1', status=PedidoCompra.Status.APROVADO,
            data_emissao=timezone.now(),
        )
        pedido_compra.itens.create(
            produto=self.produto, numero_item=1, quantidade=Decimal('40'),
            valor_unitario=Decimal('2'), valor_bruto=Decimal('80'), valor_total=Decimal('80'),
        )

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        # Sem a compra em aberto, o deficit de 30+ dava sugestao (ver teste
        # acima); com 40 un. a caminho, o deficit inteiro ja esta coberto.
        self.assertEqual(resultado["sugestoes"], [])

    def test_compra_recebida_nao_conta_como_a_caminho(self):
        self._pedido_venda(30, status=PedidoVenda.Status.FATURADO)
        pedido_compra = PedidoCompra.objects.create(
            filial=self.loja_a, fornecedor=self.fornecedor, usuario=self.usuario,
            numero_pedido='PC-2', status=PedidoCompra.Status.RECEBIDO,
            data_emissao=timezone.now(),
        )
        pedido_compra.itens.create(
            produto=self.produto, numero_item=1, quantidade=Decimal('40'),
            quantidade_recebida=Decimal('40'),
            valor_unitario=Decimal('2'), valor_bruto=Decimal('80'), valor_total=Decimal('80'),
        )

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        self.assertEqual(len(resultado["sugestoes"]), 1)

    def test_compra_parcialmente_recebida_conta_so_o_que_falta(self):
        self._pedido_venda(30, status=PedidoVenda.Status.FATURADO)
        pedido_compra = PedidoCompra.objects.create(
            filial=self.loja_a, fornecedor=self.fornecedor, usuario=self.usuario,
            numero_pedido='PC-3', status=PedidoCompra.Status.PARCIALMENTE_RECEBIDO,
            data_emissao=timezone.now(),
        )
        pedido_compra.itens.create(
            produto=self.produto, numero_item=1, quantidade=Decimal('40'),
            quantidade_recebida=Decimal('30'),
            valor_unitario=Decimal('2'), valor_bruto=Decimal('80'), valor_total=Decimal('80'),
        )

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        # So' 10 un. ainda a caminho (40 - 30 recebidas); nao cobre o
        # deficit inteiro, entao ainda sobra sugestao de transferencia.
        self.assertEqual(len(resultado["sugestoes"]), 1)


class EquilibrioComLeadTimeTests(TestCase):
    """
    Fase 4: a meta de cobertura nunca fica menor que o lead time de
    reposição do produto -- escolher "14 dias" no filtro não pode deixar
    uma loja descoberta se o fornecedor demora mais que isso pra entregar.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Lead Time LTDA", nome_fantasia="Rede Lead Time",
            cnpj="86345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="86345678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="86345678000193", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="leadtime@inoovated.com", nome="Usuario Lead Time", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        # 1 un/dia de demanda (30 vendidas em 30 dias de analise).
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto fornecedor lento",
            ncm="20089900", estoque_minimo=Decimal("0"), preco_venda=Decimal("10"),
            lead_time_reposicao_dias=30,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=Decimal("10"), quantidade_disponivel=Decimal("10"),
        )
        # Saldo de rede propositalmente escasso: com 10 (loja A) + 20
        # (loja B) = 30, uma meta de 30 dias (reserva 30) ja consome todo
        # o excedente da rede -- e' o que faz a meta de 45 dias (reserva
        # 45) aparecer diferente no resultado, em vez de ser absorvida
        # pela redistribuicao do excedente (ver docstring da classe).
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=Decimal("20"), quantidade_disponivel=Decimal("20"),
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_a, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=300,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=30,
            unidade_medida="UN", valor_unitario=10, valor_total=300,
        )

    def test_meta_usa_o_lead_time_quando_maior_que_a_cobertura_escolhida(self):
        # Cobertura escolhida: 14 dias. Lead time do produto: 30 dias ->
        # a meta de verdade usa 30 dias (1 un./dia x 30 = 30 un.), nao 14.
        resultado = calcular_equilibrio(
            empresa=self.empresa, dias_analise=30, dias_cobertura=14,
        )

        self.assertEqual(len(resultado["sugestoes"]), 1)
        sugestao = resultado["sugestoes"][0]
        self.assertEqual(sugestao["destino_meta"], Decimal("30.000"))
        self.assertEqual(sugestao["dias_meta"], 30)
        self.assertTrue(sugestao["lead_time_maior_que_cobertura"])

    def test_cobertura_do_filtro_vence_quando_maior_que_o_lead_time(self):
        # Filtro pede 45 dias de cobertura, acima do lead time (30) -- a
        # meta segue o filtro, nao o lead time, e por isso fica maior que
        # no teste acima.
        resultado = calcular_equilibrio(
            empresa=self.empresa, dias_analise=30, dias_cobertura=45,
        )

        sugestao = resultado["sugestoes"][0]
        self.assertEqual(sugestao["destino_meta"], Decimal("45.000"))
        self.assertEqual(sugestao["dias_meta"], 45)
        self.assertFalse(sugestao["lead_time_maior_que_cobertura"])


class ExemploQuatroFiliaisTests(TestCase):
    """
    O exemplo de 4 filiais que o usuário deu pra validar o motor:
    "Polpa de Morango 100g" numa rede com uma filial parada (A, 50 dias
    de cobertura) e duas quase em ruptura (D com 1,25 dia, B com 1,3 dia).

    O PONTO QUE ESTE TESTE TRAVA: destino é escolhido por URGÊNCIA (menor
    cobertura em dias), não pelo maior déficit em unidades. Com déficit
    bruto, B (déficit de 280 un.) venceria D (déficit de 150 un.) mesmo
    D estando mais perto de faltar -- e a origem (300 un. de excedente)
    acabaria consumida quase toda em B, sobrando pouco pra D. Por
    urgência, D é atendida primeiro e o excedente de A (300 un.) fecha
    exatamente 150/150 pros dois destinos, do jeito que o usuário esperava.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Polpa LTDA", nome_fantasia="Rede Polpa",
            cnpj="87345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial A", nome_fantasia="Filial A",
            cnpj="87345678000192", uf="RN", is_matriz=True,
        )
        cls.filial_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial B", nome_fantasia="Filial B",
            cnpj="87345678000193", uf="RN",
        )
        cls.filial_c = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial C", nome_fantasia="Filial C",
            cnpj="87345678000194", uf="RN",
        )
        cls.filial_d = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial D", nome_fantasia="Filial D",
            cnpj="87345678000195", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="polpa@inoovated.com", nome="Usuario Polpa", password="teste1234",
            empresa=cls.empresa, filial=cls.filial_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        for filial in (cls.filial_a, cls.filial_b, cls.filial_c, cls.filial_d):
            UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=filial)

        cls.produto = Produto.objects.create(
            filial=cls.filial_a, unidade_medida=cls.unidade, descricao="Polpa de Morango 100g",
            ncm="20089900", preco_venda=Decimal("10"),
        )
        for filial in (cls.filial_a, cls.filial_b, cls.filial_c, cls.filial_d):
            ProdutoFilial.objects.create(produto=cls.produto, filial=filial)

        # Filial B declarou estoque mínimo de 30 -- entra no cadastro
        # mesmo não sendo o fator que decide a meta neste caso (a meta
        # por dias de cobertura, 15 un./dia x 20 dias = 300, já é maior).
        Produto.objects.filter(pk=cls.produto.pk).update(estoque_minimo=Decimal("30"))
        cls.produto.refresh_from_db()

        depositos = {
            filial: Deposito.objects.create(filial=filial, nome=f"Geral {filial.nome_fantasia}", is_padrao=True)
            for filial in (cls.filial_a, cls.filial_b, cls.filial_c, cls.filial_d)
        }
        saldos = {cls.filial_a: 500, cls.filial_b: 20, cls.filial_c: 100, cls.filial_d: 10}
        for filial, saldo in saldos.items():
            Estoque.objects.create(
                produto=cls.produto, filial=filial, deposito=depositos[filial],
                quantidade_atual=saldo, quantidade_disponivel=saldo,
            )

        # Venda média diária via total vendido em 30 dias de análise:
        # A=10/dia, B=15/dia, C=5/dia, D=8/dia.
        vendas = {cls.filial_a: 300, cls.filial_b: 450, cls.filial_c: 150, cls.filial_d: 240}
        numero = 0
        for filial, quantidade in vendas.items():
            numero += 1
            venda = VendaPDV.objects.create(
                filial=filial, numero_venda=numero, usuario=cls.usuario,
                data_venda=timezone.now(), status="finalizada", valor_total=quantidade * 10,
            )
            ItemVendaPDV.objects.create(
                venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=quantidade,
                unidade_medida="UN", valor_unitario=10, valor_total=quantidade * 10,
            )

    def test_origem_e_a_neediest_filiais_atendidas_por_urgencia_nao_por_deficit_bruto(self):
        # estoque_maximo_recomendado da Filial A = 20 dias -> mesma
        # cobertura desejada pra toda a rede neste teste.
        resultado = calcular_equilibrio(
            empresa=self.empresa, dias_analise=30, dias_cobertura=20,
        )

        sugestoes = resultado["sugestoes"]
        self.assertEqual(len(sugestoes), 2)

        # D e' mais urgente (1,25 dia de cobertura) que B (1,33 dia) --
        # tem que ser atendida primeiro, mesmo com deficit bruto menor.
        self.assertEqual(sugestoes[0]["origem"], self.filial_a)
        self.assertEqual(sugestoes[0]["destino"], self.filial_d)
        self.assertEqual(sugestoes[0]["quantidade"], Decimal("150"))

        self.assertEqual(sugestoes[1]["origem"], self.filial_a)
        self.assertEqual(sugestoes[1]["destino"], self.filial_b)
        self.assertEqual(sugestoes[1]["quantidade"], Decimal("150"))

        # Nunca sugerir mais que o excedente real da origem: 500 - meta
        # (200, por 10 un./dia x 20 dias) = 300, e 150+150 fecha exato.
        total_sugerido = sum((s["quantidade"] for s in sugestoes), Decimal("0"))
        self.assertEqual(total_sugerido, Decimal("300"))

        # Filial C nao aparece: saldo (100) bate exatamente a meta dela
        # (5 un./dia x 20 dias = 100) -- sem deficit, sem excedente.
        destinos_da_rede = {s["destino"] for s in sugestoes}
        self.assertNotIn(self.filial_c, destinos_da_rede)
