from unittest.mock import patch

from django.template.loader import get_template
from django.test import SimpleTestCase

from apps.integracoes.models import CredencialIntegracao


class CredencialIPrintTests(SimpleTestCase):
    def test_rotacao_invalida_segredo_anterior_e_revela_novo_uma_vez(self):
        credencial = CredencialIntegracao(
            empresa_id=1,
            nome='iPrint - 06722483000114',
            prefixo='antigo',
            token_hash=CredencialIntegracao.hash_token('ited_antigo.segredo'),
            escopos=['produtos:ler'],
            ativo=False,
        )
        hash_anterior = credencial.token_hash

        with patch.object(CredencialIntegracao, 'save') as salvar:
            token = credencial.rotacionar(escopos=['produtos:ler', 'estoque:ler'])

        self.assertTrue(token.startswith(f'ited_{credencial.prefixo}.'))
        self.assertNotEqual(credencial.token_hash, hash_anterior)
        self.assertEqual(credencial.token_hash, CredencialIntegracao.hash_token(token))
        self.assertEqual(credencial.escopos, ['produtos:ler', 'estoque:ler'])
        self.assertTrue(credencial.ativo)
        salvar.assert_called_once()

    def test_parametros_expoe_geracao_segura_da_chave(self):
        get_template('core/admin/parametros_form.html')
