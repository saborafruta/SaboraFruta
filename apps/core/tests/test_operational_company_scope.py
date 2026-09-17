from pathlib import Path

from django.test import SimpleTestCase


class OperationalCompanyScopeTests(SimpleTestCase):
    def test_codigo_operacional_nao_usa_empresa_direta_do_usuario(self):
        apps_dir = Path(__file__).resolve().parents[2]
        permitidos = {
            Path('core/middleware/tenant.py'),
            Path('core/services/request_scope.py'),
        }
        infratores = []

        for arquivo in apps_dir.rglob('*.py'):
            relativo = arquivo.relative_to(apps_dir)
            if 'tests' in relativo.parts or relativo in permitidos:
                continue
            if 'request.user.empresa' in arquivo.read_text(encoding='utf-8'):
                infratores.append(str(relativo))

        self.assertEqual(
            infratores,
            [],
            'Use empresa_operacional(request) nas rotas operacionais: '
            + ', '.join(infratores),
        )
