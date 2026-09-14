"""Fase 20/30: aprovação de transferência por alçada de valor, com segregação de funções."""
import threading
from decimal import Decimal

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Deposito, Estoque, SolicitacaoTransferencia
from apps.estoque.services.aprovacao_transferencia import (
    aprovar_solicitacao, rejeitar_solicitacao, solicitar_transferencia,
    usuario_pode_executar_direto, valor_estimado_transferencia,
)
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class AprovacaoTransferenciaBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Aprovacao LTDA", nome_fantasia="Rede Aprovacao",
            cnpj="95645678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="95645678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="95645678000273", uf="RN",
        )
        cls.perfil_supervisor = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome="Supervisor", is_admin=False,
            alcada_transferencia=Decimal("1000"),
        )
        cls.perfil_gerente = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome="Gerente", is_admin=False,
            alcada_transferencia=Decimal("5000"),
        )
        cls.perfil_admin = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls._dar_permissao(cls.perfil_supervisor, "ver")
        cls._dar_permissao(cls.perfil_supervisor, "criar")
        cls._dar_permissao(cls.perfil_gerente, "ver")
        cls._dar_permissao(cls.perfil_gerente, "criar")
        cls._dar_permissao(cls.perfil_gerente, "aprovar")
        cls.supervisor = Usuario.objects.create_user(
            email="supervisor@inoovated.com", nome="Usuario Supervisor", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil_supervisor,
        )
        cls.gerente = Usuario.objects.create_user(
            email="gerente@inoovated.com", nome="Usuario Gerente", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil_gerente,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto Caro",
            ncm="20089900", preco_venda=Decimal("100"), preco_custo=Decimal("50"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=100, quantidade_disponivel=100,
        )

    @staticmethod
    def _dar_permissao(perfil, acao):
        from apps.core.models import Permissao

        permissao, _ = Permissao.objects.get_or_create(perfil=perfil, modulo="estoque")
        setattr(permissao, f"pode_{acao}", True)
        permissao.save()


class ValorEAlcadaTests(AprovacaoTransferenciaBase):
    def test_valor_estimado_usa_custo_do_produto(self):
        valor = valor_estimado_transferencia(produto=self.produto, quantidade=Decimal("10"))
        self.assertEqual(valor, Decimal("500.00"))

    def test_dentro_da_alcada_executa_direto(self):
        # Supervisor tem alcada de 1000; 10 un. a 50 de custo = 500 <= 1000.
        self.assertTrue(usuario_pode_executar_direto(self.supervisor, Decimal("500")))

    def test_acima_da_alcada_nao_executa_direto(self):
        # 30 un. a 50 de custo = 1500 > alcada do supervisor (1000).
        self.assertFalse(usuario_pode_executar_direto(self.supervisor, Decimal("1500")))

    def test_admin_sempre_executa_direto(self):
        admin = Usuario.objects.create_user(
            email="admin@inoovated.com", nome="Usuario Admin", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=self.perfil_admin,
        )
        self.assertTrue(usuario_pode_executar_direto(admin, Decimal("999999")))

    def test_perfil_sem_alcada_configurada_executa_direto(self):
        perfil_sem_limite = PerfilAcesso.objects.create(empresa=self.empresa, nome="Sem Limite")
        usuario = Usuario.objects.create_user(
            email="semlimite@inoovated.com", nome="Usuario Sem Limite", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=perfil_sem_limite,
        )
        self.assertTrue(usuario_pode_executar_direto(usuario, Decimal("999999")))


class FluxoAprovacaoTests(AprovacaoTransferenciaBase):
    def test_solicitar_cria_pendente_sem_mexer_no_estoque(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço de estoque", solicitante=self.supervisor,
        )

        self.assertEqual(solicitacao.status, SolicitacaoTransferencia.Status.PENDENTE)
        self.assertEqual(solicitacao.valor_estimado, Decimal("1500.00"))
        estoque_origem = Estoque.objects.get(produto=self.produto, filial=self.loja_a)
        self.assertEqual(estoque_origem.quantidade_atual, Decimal("100"))

    def test_solicitar_mesma_filial_como_origem_e_destino_falha(self):
        with self.assertRaises(DadosInvalidosError):
            solicitar_transferencia(
                produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_a,
                quantidade=Decimal("10"), motivo="", solicitante=self.supervisor,
            )

    def test_aprovar_executa_a_transferencia_de_verdade(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )

        aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente, observacao="Ok")

        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, SolicitacaoTransferencia.Status.APROVADA)
        self.assertTrue(solicitacao.documento_numero)
        origem_depois = Estoque.objects.get(produto=self.produto, filial=self.loja_a)
        destino_depois = Estoque.objects.get(produto=self.produto, filial=self.loja_b)
        self.assertEqual(origem_depois.quantidade_atual, Decimal("70"))
        self.assertEqual(destino_depois.quantidade_atual, Decimal("30"))

    def test_solicitante_nao_pode_aprovar_a_propria_solicitacao(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.gerente,
        )

        with self.assertRaises(DadosInvalidosError):
            aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente)

    def test_aprovador_sem_alcada_suficiente_nao_pode_aprovar(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )
        outro_supervisor = Usuario.objects.create_user(
            email="supervisor2@inoovated.com", nome="Outro Supervisor", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=self.perfil_supervisor,
        )

        with self.assertRaises(DadosInvalidosError):
            aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=outro_supervisor)

    def test_aprovar_quantidade_maior_que_saldo_atual_falha(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )
        Estoque.objects.filter(produto=self.produto, filial=self.loja_a).update(
            quantidade_atual=Decimal("5"), quantidade_disponivel=Decimal("5"),
        )

        with self.assertRaises(DadosInvalidosError):
            aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente)

    def test_rejeitar_exige_observacao_e_nao_mexe_no_estoque(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )

        with self.assertRaises(DadosInvalidosError):
            rejeitar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente, observacao="")

        rejeitar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente, observacao="Sem necessidade agora")
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, SolicitacaoTransferencia.Status.REJEITADA)
        estoque_origem = Estoque.objects.get(produto=self.produto, filial=self.loja_a)
        self.assertEqual(estoque_origem.quantidade_atual, Decimal("100"))

    def test_aprovar_produto_com_lote_resolve_fefo_automaticamente(self):
        from apps.estoque.models import LoteProduto

        produto_lote = Produto.objects.create(
            filial=self.loja_a, unidade_medida=self.unidade, descricao="Produto Com Lote",
            ncm="20089900", preco_venda=Decimal("100"), preco_custo=Decimal("50"),
            controla_lote=True,
        )
        ProdutoFilial.objects.create(produto=produto_lote, filial=self.loja_a)
        ProdutoFilial.objects.create(produto=produto_lote, filial=self.loja_b)
        Estoque.objects.create(
            produto=produto_lote, filial=self.loja_a, deposito=Deposito.objects.get(filial=self.loja_a),
            quantidade_atual=100, quantidade_disponivel=100,
        )
        LoteProduto.objects.create(
            produto=produto_lote, filial=self.loja_a, numero_lote="L1",
            quantidade_inicial=100, quantidade_atual=100, status=LoteProduto.Status.ATIVO,
        )
        solicitacao = solicitar_transferencia(
            produto=produto_lote, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )

        aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente)

        lote = LoteProduto.objects.get(produto=produto_lote, numero_lote="L1", filial=self.loja_a)
        self.assertEqual(lote.quantidade_atual, Decimal("70"))

    def test_aprovar_produto_com_lote_sem_lote_disponivel_falha(self):
        produto_lote = Produto.objects.create(
            filial=self.loja_a, unidade_medida=self.unidade, descricao="Produto Sem Lote Disponivel",
            ncm="20089900", preco_venda=Decimal("100"), preco_custo=Decimal("50"),
            controla_lote=True,
        )
        ProdutoFilial.objects.create(produto=produto_lote, filial=self.loja_a)
        ProdutoFilial.objects.create(produto=produto_lote, filial=self.loja_b)
        Estoque.objects.create(
            produto=produto_lote, filial=self.loja_a, deposito=Deposito.objects.get(filial=self.loja_a),
            quantidade_atual=100, quantidade_disponivel=100,
        )
        solicitacao = solicitar_transferencia(
            produto=produto_lote, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )

        with self.assertRaises(DadosInvalidosError):
            aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente)

    def test_solicitar_aprovar_e_rejeitar_geram_trilha_de_auditoria(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço de estoque", solicitante=self.supervisor,
        )
        registro_criacao = RegistroAuditoria.objects.get(
            objeto_tipo="estoque.solicitacaotransferencia", objeto_id=str(solicitacao.pk), acao="criar",
        )
        self.assertEqual(registro_criacao.usuario_id, self.supervisor.pk)
        self.assertEqual(registro_criacao.justificativa, "Reforço de estoque")

        aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente, observacao="Ok, pode mandar")
        registro_aprovacao = RegistroAuditoria.objects.get(
            objeto_tipo="estoque.solicitacaotransferencia", objeto_id=str(solicitacao.pk), acao="aprovar",
        )
        self.assertEqual(registro_aprovacao.usuario_id, self.gerente.pk)
        self.assertEqual(registro_aprovacao.justificativa, "Ok, pode mandar")
        self.assertEqual(registro_aprovacao.dados_anteriores["status"], "pendente")
        self.assertEqual(registro_aprovacao.dados_novos["status"], "aprovada")

    def test_rejeitar_gera_trilha_de_auditoria(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )

        rejeitar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente, observacao="Sem necessidade agora")

        registro = RegistroAuditoria.objects.get(
            objeto_tipo="estoque.solicitacaotransferencia", objeto_id=str(solicitacao.pk), acao="cancelar",
        )
        self.assertEqual(registro.usuario_id, self.gerente.pk)
        self.assertEqual(registro.justificativa, "Sem necessidade agora")
        self.assertEqual(registro.dados_novos["status"], "rejeitada")

    def test_nao_decide_duas_vezes(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )
        aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente)

        with self.assertRaises(DadosInvalidosError):
            aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=self.gerente)


class TransferenciaGateViewTests(AprovacaoTransferenciaBase):
    def setUp(self):
        self.client = Client()

    def test_dentro_da_alcada_redireciona_direto_para_criacao(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("estoque:transferencia-gate"), {
            "produto": self.produto.pk, "destino": self.loja_b.pk, "quantidade": "10",
        })
        self.assertRedirects(response, expected_url=(
            reverse("estoque:transferencia-lojas-create")
            + f"?destino={self.loja_b.pk}&produto={self.produto.pk}&quantidade=10&origem=equilibrio"
        ), fetch_redirect_response=False)

    def test_acima_da_alcada_pede_confirmacao(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("estoque:transferencia-gate"), {
            "produto": self.produto.pk, "destino": self.loja_b.pk, "quantidade": "30",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "excede sua alçada")

    def test_post_acima_da_alcada_cria_solicitacao(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse("estoque:transferencia-gate"), {
            "produto": self.produto.pk, "destino": self.loja_b.pk, "quantidade": "30",
            "motivo": "Precisamos reforçar a loja B",
        })
        self.assertRedirects(response, reverse("estoque:solicitacao-transferencia-list"))
        self.assertEqual(SolicitacaoTransferencia.objects.filter(solicitante=self.supervisor).count(), 1)


class SolicitacaoTransferenciaViewTests(AprovacaoTransferenciaBase):
    def setUp(self):
        self.client = Client()

    def test_lista_e_decisao_via_view(self):
        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Reforço", solicitante=self.supervisor,
        )

        self.client.force_login(self.gerente)
        response = self.client.get(reverse("estoque:solicitacao-transferencia-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto Caro")

        response = self.client.post(
            reverse("estoque:solicitacao-transferencia-decidir", args=[solicitacao.pk]),
            {"acao": "aprovar", "observacao": "Ok"},
        )
        self.assertRedirects(response, reverse("estoque:solicitacao-transferencia-list"))
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, SolicitacaoTransferencia.Status.APROVADA)


class ConcorrenciaAprovacaoTests(TransactionTestCase):
    """
    Fase 34: duas aprovações "ao mesmo tempo" pra mesma solicitação --
    so' uma pode vencer. `select_for_update()` em `aprovar_solicitacao`
    é o que garante isso; `TransactionTestCase` (não `TestCase`) é
    obrigatório aqui porque threads reais precisam de conexões e
    transações de verdade, não a transação única que `TestCase` nunca
    comita.

    So' roda de verdade no Postgres: SQLite em `:memory:` (usado nos
    testes rápidos) dá uma conexão por thread, cada uma com seu próprio
    banco isolado -- não ha' disputa nenhuma pra' testar.
    """

    def setUp(self):
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
        from apps.estoque.models import Deposito, Estoque
        from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial

        self.empresa = Empresa.objects.create(
            razao_social="Rede Concorrencia LTDA", nome_fantasia="Rede Concorrencia",
            cnpj="97845678000455", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.loja_a = Filial.objects.create(
            empresa=self.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="97845678000536", uf="RN", is_matriz=True,
        )
        self.loja_b = Filial.objects.create(
            empresa=self.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="97845678000617", uf="RN",
        )
        perfil = PerfilAcesso.objects.create(empresa=self.empresa, nome="Gerente", is_admin=True)
        self.solicitante = Usuario.objects.create_user(
            email="concorrencia-solicitante@inoovated.com", nome="Solicitante", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=perfil,
        )
        self.aprovador_1 = Usuario.objects.create_user(
            email="concorrencia-aprovador1@inoovated.com", nome="Aprovador Um", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=perfil,
        )
        self.aprovador_2 = Usuario.objects.create_user(
            email="concorrencia-aprovador2@inoovated.com", nome="Aprovador Dois", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=perfil,
        )
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=unidade, filial=self.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=unidade, filial=self.loja_b)
        self.produto = Produto.objects.create(
            filial=self.loja_a, unidade_medida=unidade, descricao="Produto Concorrencia",
            ncm="20089900", preco_venda=Decimal("10"), preco_custo=Decimal("5"),
        )
        ProdutoFilial.objects.create(produto=self.produto, filial=self.loja_a)
        ProdutoFilial.objects.create(produto=self.produto, filial=self.loja_b)
        deposito_a = Deposito.objects.create(filial=self.loja_a, nome="Geral A", is_padrao=True)
        Estoque.objects.create(
            produto=self.produto, filial=self.loja_a, deposito=deposito_a,
            quantidade_atual=100, quantidade_disponivel=100,
        )

    def test_duas_aprovacoes_simultaneas_so_uma_vence(self):
        if connection.vendor != "postgresql":
            self.skipTest("Concorrência real de select_for_update só é observável com Postgres (não em SQLite :memory:).")

        from apps.estoque.services.aprovacao_transferencia import aprovar_solicitacao, solicitar_transferencia

        solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("30"), motivo="Teste de concorrência", solicitante=self.solicitante,
        )

        resultados = {}
        barreira = threading.Barrier(2)

        def _aprovar(chave, aprovador):
            barreira.wait()
            try:
                aprovar_solicitacao(solicitacao_id=solicitacao.pk, aprovador=aprovador)
                resultados[chave] = "ok"
            except Exception as exc:  # noqa: BLE001
                resultados[chave] = f"erro: {exc}"
            finally:
                connection.close()

        t1 = threading.Thread(target=_aprovar, args=("t1", self.aprovador_1))
        t2 = threading.Thread(target=_aprovar, args=("t2", self.aprovador_2))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        sucessos = [chave for chave, resultado in resultados.items() if resultado == "ok"]
        self.assertEqual(len(sucessos), 1, f"Esperava exatamente 1 aprovação vencedora, veio: {resultados}")
