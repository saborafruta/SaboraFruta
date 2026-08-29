from django.test import SimpleTestCase

from apps.moda.services.op2_estrutura import (
    OP2_ESTRUTURA_OPCOES,
    estrutura_resumo,
    validar_estrutura_item,
)


class CorEstruturaOp2Tests(SimpleTestCase):
    def _dados_camisa(self, cor='PRETO', personalizada=''):
        grupo = OP2_ESTRUTURA_OPCOES['camisa']
        dados = {'estrutura_tipo': 'camisa'}
        for campo, opcoes in grupo['campos'].items():
            dados[f'estrutura_{campo}'] = cor if campo == 'cor' else opcoes[0]
        dados['estrutura_cor_personalizada'] = personalizada
        return dados

    def test_cores_principais_estao_disponiveis(self):
        cores = OP2_ESTRUTURA_OPCOES['camisa']['campos']['cor']
        self.assertIn('PRETO', cores)
        self.assertIn('BRANCO', cores)
        self.assertIn('AZUL MARINHO', cores)
        self.assertIn('COR PERSONALIZADA', cores)

    def test_cor_personalizada_exige_texto_e_aparece_no_resumo(self):
        dados = self._dados_camisa('COR PERSONALIZADA')
        with self.assertRaisesMessage(ValueError, 'informe a cor desejada'):
            validar_estrutura_item(dados, OP2_ESTRUTURA_OPCOES)

        dados['estrutura_cor_personalizada'] = 'Bordô especial'
        validar_estrutura_item(dados, OP2_ESTRUTURA_OPCOES)
        resumo = estrutura_resumo(dados, OP2_ESTRUTURA_OPCOES)
        self.assertIn('Cor: Bordô especial', resumo)
        self.assertNotIn('Cor: COR PERSONALIZADA', resumo)
