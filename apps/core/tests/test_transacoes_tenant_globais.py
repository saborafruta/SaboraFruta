import ast
from pathlib import Path

from django.test import SimpleTestCase


class TransacoesTenantGlobaisTests(SimpleTestCase):
    """Impede transacoes ambiguas nos fluxos executados por telas e servicos."""

    def test_codigo_operacional_nao_usa_atomic_sem_alias(self):
        raiz_apps = Path(__file__).resolve().parents[2]
        erros = []

        for caminho in raiz_apps.rglob("*.py"):
            partes = set(caminho.parts)
            if partes.intersection({"migrations", "tests", "management"}):
                continue

            arvore = ast.parse(caminho.read_text(encoding="utf-8-sig"))
            pais = {
                filho: pai
                for pai in ast.walk(arvore)
                for filho in ast.iter_child_nodes(pai)
            }
            for no in ast.walk(arvore):
                if not (
                    isinstance(no, ast.Attribute)
                    and isinstance(no.value, ast.Name)
                    and no.value.id == "transaction"
                    and no.attr == "atomic"
                ):
                    continue

                pai = pais.get(no)
                if isinstance(pai, ast.Call) and pai.func is no:
                    if any(chave.arg == "using" for chave in pai.keywords):
                        continue
                erros.append(f"{caminho.relative_to(raiz_apps)}:{no.lineno}")

        self.assertEqual(
            erros,
            [],
            "Use tenant_atomic para dados operacionais ou informe using= explicitamente: "
            + ", ".join(erros),
        )
