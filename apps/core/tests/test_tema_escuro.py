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


class EstrategiaDoDarkDoTailwindTests(SimpleTestCase):
    """
    Os utilitários `dark:` têm de obedecer o TEMA DO APP, não o do sistema.

    Sem `darkMode` na config, o Tailwind compila todo `dark:` dentro de
    `@media (prefers-color-scheme: dark)` — e aí os 222 `dark:` espalhados por
    31 templates seguem o Windows, não o botão de tema do ERP. Quem usa o
    sistema no escuro com o SO no claro vê todos errados, e o contrário também.

    O TESTE OLHA O CSS COMPILADO, e não só a config. É onde mora a armadilha:
    mudar `tailwind.config.js` sem rodar `npm run build:css` não muda nada, e o
    navegador continua lendo o arquivo antigo. Config certa com CSS velho é
    exatamente o estado que engana quem revisa.
    """

    CSS = 'static/css/tailwind-built.css'
    CONFIG = 'tailwind.config.js'

    def test_a_config_liga_o_dark_na_classe_do_app(self):
        config = open(self.CONFIG, encoding='utf-8').read()

        self.assertIn('darkMode', config)
        self.assertIn('tema-escuro', config)

    def test_o_css_compilado_usa_a_classe_e_nao_o_sistema(self):
        """
        Se isto reprovar depois de mexer na config, falta rodar:
            npm run build:css
        """
        css = open(self.CSS, encoding='utf-8').read()

        self.assertNotIn(
            'prefers-color-scheme:dark', css,
            'O CSS compilado ainda liga o tema escuro no sistema operacional — '
            'rode `npm run build:css`.',
        )
        self.assertIn(
            '.tema-escuro', css,
            'O CSS compilado não tem regra ligada à classe de tema do app.',
        )

    def test_os_utilitarios_dark_sobreviveram_a_troca(self):
        """
        A troca de estratégia reescreve TODOS os seletores `dark:`. Um punhado
        deles sumir passaria despercebido — a tela só ficaria com a cor errada.
        """
        css = open(self.CSS, encoding='utf-8').read()

        # `dark\:` é como o Tailwind escapa o prefixo no seletor compilado.
        self.assertGreater(css.count(r'.dark\:'), 50)
        # e cada um tem de estar preso à classe do app
        self.assertGreater(css.count(':is(.tema-escuro *)'), 50)
