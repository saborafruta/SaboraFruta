import ast
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from apps.core.models import Usuario
from apps.core.tenant_context import tenant_db
from apps.pdv.models import VendaPDV


class TransacoesTenantPDVTests(SimpleTestCase):
    """Evita que fluxos do PDV voltem a abrir transacao no banco central."""

    ARQUIVOS_TRANSACIONAIS = {
        "services/autorizacao_cancelamento_service.py": 1,
        "services/cancelamento_fiscal_service.py": 1,
        "services/edicao_venda_service.py": 1,
        "services/nfce_payload_builder.py": 2,
        "services/venda_pdv_service.py": 1,
        "views/comprovante_publico.py": 1,
        "views/pdv.py": 8,
    }

    def test_fluxos_transacionais_usam_conexao_do_tenant(self):
        raiz_pdv = Path(__file__).resolve().parents[1]

        for caminho_relativo, quantidade_minima in self.ARQUIVOS_TRANSACIONAIS.items():
            with self.subTest(arquivo=caminho_relativo):
                caminho = raiz_pdv / caminho_relativo
                arvore = ast.parse(caminho.read_text(encoding="utf-8"))
                atomicos_padrao = [
                    no
                    for no in ast.walk(arvore)
                    if isinstance(no, ast.Attribute)
                    and isinstance(no.value, ast.Name)
                    and no.value.id == "transaction"
                    and no.attr == "atomic"
                ]
                atomicos_tenant = [
                    no
                    for no in ast.walk(arvore)
                    if isinstance(no, ast.Name) and no.id == "tenant_atomic"
                ]
                importa_tenant_atomic = any(
                    isinstance(no, ast.ImportFrom)
                    and no.module == "apps.core.tenant_context"
                    and any(nome.name == "tenant_atomic" for nome in no.names)
                    for no in ast.walk(arvore)
                )

                self.assertFalse(
                    atomicos_padrao,
                    f"{caminho_relativo} abriu transacao no banco central.",
                )
                self.assertTrue(importa_tenant_atomic)
                self.assertGreaterEqual(
                    len(atomicos_tenant),
                    quantidade_minima,
                    f"{caminho_relativo} deixou de proteger um fluxo no banco do tenant.",
                )

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_DATABASE_ALIASES=["tenant_pdv"],
    )
    def test_finalizacao_nao_atribui_instancia_de_usuario_de_outro_banco(self):
        usuario_central = Usuario(pk=123, email="superadmin@example.com")
        usuario_central._state.db = "default"

        with tenant_db("tenant_pdv"):
            venda = VendaPDV(usuario_id=usuario_central.pk)
            with self.assertRaisesMessage(ValueError, "prevents this relation"):
                VendaPDV(usuario=usuario_central)

        self.assertEqual(venda.usuario_id, usuario_central.pk)

        caminho = Path(__file__).resolve().parents[1] / "services/venda_pdv_service.py"
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        criacoes_venda = [
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr == "create"
            and isinstance(no.func.value, ast.Attribute)
            and no.func.value.attr == "objects"
            and isinstance(no.func.value.value, ast.Name)
            and no.func.value.value.id == "VendaPDV"
        ]
        self.assertEqual(len(criacoes_venda), 1)
        argumentos = {keyword.arg for keyword in criacoes_venda[0].keywords}
        self.assertIn("usuario_id", argumentos)
        self.assertNotIn("usuario", argumentos)
