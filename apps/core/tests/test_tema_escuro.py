"""
Nenhum template pode escrever regra de tema escuro com o seletor errado.

O DEFEITO QUE ISTO IMPEDE já foi para produção. Três templates escreviam o
bloco de tema escuro como `.dark .alguma-coisa` — copiado da convenção do
Tailwind, onde `darkMode: 'class'` liga na classe `dark`. Só que **este app
nunca adiciona `dark`**: o `_base.html` alterna `tema-claro` e `tema-escuro`,
no `<html>` e no `<body>`.

O resultado é traiçoeiro porque não quebra nada — o seletor simplesmente não
casa, o navegador ignora a regra em silêncio, e as cores claras escritas acima
dela continuam valendo. Na tela de Contas Pagas isso deixava os cartões de
resumo brancos, com texto cinza-claro em cima, dentro do tema escuro.

É a mesma família de `.form-label` e `.form-select`, que existiam em vinte e
cinco telas e não estavam definidas em lugar nenhum: CSS que não casa não
avisa. Só um teste avisa.
"""
import os
import re

from django.test import SimpleTestCase

RAIZ_TEMPLATES = (
    'templates',
    'apps',
)

# `.dark ` como seletor descendente. O ponto-espaço evita pegar coisas como
# `bg-dark` ou a palavra "dark" dentro de texto.
SELETOR_ORFAO = re.compile(r'(?<![\w-])\.dark\s+\.')


class SeletorDeTemaEscuroTests(SimpleTestCase):

    def _templates(self):
        for raiz in RAIZ_TEMPLATES:
            for pasta, _sub, arquivos in os.walk(raiz):
                # A pasta de backup guarda uma cópia antiga do projeto inteiro;
                # cobrá-la faria o teste reprovar por código que ninguém serve.
                if 'apps_backup' in pasta or '__pycache__' in pasta:
                    continue
                for arquivo in arquivos:
                    if arquivo.endswith('.html'):
                        yield os.path.join(pasta, arquivo)

    def test_nenhum_template_usa_o_seletor_dark(self):
        """
        `.dark .x` não casa com nada neste app. O seletor certo é
        `body.tema-escuro .x` — ou, melhor ainda, escrever a regra escura como
        base e sobrescrever com `body.tema-claro`, que é o que o `_base.html`
        faz em setenta e cinco regras.
        """
        culpados = [
            caminho for caminho in self._templates()
            if SELETOR_ORFAO.search(open(caminho, encoding='utf-8').read())
        ]

        self.assertEqual(
            culpados, [],
            'Regra de tema escuro com seletor que nunca casa. Troque `.dark` '
            'por `body.tema-escuro`:\n  ' + '\n  '.join(culpados),
        )

    def test_a_base_continua_alternando_tema_escuro(self):
        """
        O teste acima só vale enquanto a classe for esta. Se alguém trocar o
        nome no `_base.html`, é aqui que se descobre — e não numa tela branca.
        """
        base = open('templates/_base.html', encoding='utf-8').read()

        self.assertIn("classList.toggle('tema-escuro'", base)
        self.assertIn("classList.toggle('tema-claro'", base)
