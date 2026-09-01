import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.core.models import Usuario
from apps.core.views.preferencias_tabelas import (
    TabelaPreferenciasView,
    normalizar_chave_tabela,
    normalizar_preferencia_tabela,
)


class TabelaPreferenciasViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.usuario = SimpleNamespace(pk=7, preferencias_tabelas={}, save=Mock())

    def _post(self, dados, *, autenticado=True):
        request = self.factory.post(
            reverse('core:preferencias-tabelas'),
            data=json.dumps(dados),
            content_type='application/json',
        )
        request.user = (
            SimpleNamespace(is_authenticated=True, pk=self.usuario.pk)
            if autenticado else AnonymousUser()
        )
        selecionado = Mock()
        selecionado.get.return_value = self.usuario
        with patch(
            'apps.core.views.preferencias_tabelas.Usuario.objects.select_for_update',
            return_value=selecionado,
        ), patch(
            'apps.core.views.preferencias_tabelas.transaction.atomic',
            return_value=nullcontext(),
        ):
            return TabelaPreferenciasView.as_view()(request)

    def test_salva_preferencia_somente_no_usuario_autenticado(self):
        outro = SimpleNamespace(preferencias_tabelas={})

        response = self._post({
            'table': '/financeiro/receber/.contas-receber',
            'preferences': {'hidden': ['parcela'], 'widths': {'cliente': 240.4}},
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.preferencias_tabelas, {
            '/financeiro/receber/.contas-receber': {
                'hidden': ['parcela'], 'widths': {'cliente': 240},
            },
        })
        self.assertEqual(outro.preferencias_tabelas, {})
        self.usuario.save.assert_called_once_with(
            update_fields=['preferencias_tabelas', 'updated_at'],
        )

    def test_rejeita_preferencia_invalida_e_exige_autenticacao(self):
        invalida = self._post({
            'table': 'financeiro/receber',
            'preferences': {'hidden': [], 'widths': {}},
        })
        anonima = self._post({
            'table': '/financeiro/receber/',
            'preferences': {'hidden': [], 'widths': {}},
        }, autenticado=False)

        self.assertEqual(invalida.status_code, 400)
        self.assertEqual(anonima.status_code, 401)
        self.usuario.save.assert_not_called()

    def test_normalizacao_limita_largura_e_remove_ocultas_repetidas(self):
        self.assertEqual(
            normalizar_preferencia_tabela({
                'hidden': ['status', 'status'],
                'widths': {'cliente': 9999, 'valor': 10},
            }),
            {'hidden': ['status'], 'widths': {'cliente': 2400, 'valor': 48}},
        )
        self.assertIsNone(normalizar_chave_tabela('https://externo.test/tabela'))
        self.assertIsNone(normalizar_preferencia_tabela({'hidden': 'status'}))
        self.assertIsNone(normalizar_preferencia_tabela({
            'hidden': [], 'widths': {'cliente': float('nan')},
        }))

    def test_modelo_inicia_sem_preferencias(self):
        self.assertEqual(Usuario().preferencias_tabelas, {})


class TabelaPreferenciasFrontendTests(SimpleTestCase):
    def test_base_identifica_usuario_e_javascript_sincroniza_preferencias(self):
        raiz = Path(__file__).resolve().parents[2]
        template = (raiz.parent / 'templates' / '_base.html').read_text(encoding='utf-8')
        script = (raiz.parent / 'static' / 'js' / 'tabelas-configuraveis.js').read_text(
            encoding='utf-8',
        )

        self.assertIn('request.user.preferencias_tabelas|json_script', template)
        self.assertIn('name="erp-table-user-id"', template)
        self.assertIn("{% url 'core:preferencias-tabelas' %}", template)
        self.assertIn("serverPreferences[instance.preferenceKey]", script)
        self.assertIn("localStorage.removeItem(legacyStorageKey)", script)
        self.assertIn("'X-CSRFToken': csrfToken", script)
        self.assertIn("localStorage.setItem(instance.storageKey", script)
