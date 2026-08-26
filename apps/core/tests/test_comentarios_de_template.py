"""
Comentário de template que vaza como texto na tela.

O QUE ESTE TESTE CERCA: `{# … #}` do Django é comentário de UMA LINHA. Ao
abrir em duas, a primeira linha some e o RESTO É IMPRESSO NA PÁGINA — no
meio de um card, com chaves e tudo. O parser não reclama, o `check` não
reclama, os testes de view não reclamam (a página responde 200 com o texto
dentro), e o defeito só aparece quando alguém olha a tela.

Foi assim que uma explicação sobre "por que aqui não é zero" foi parar
dentro do painel de rendimento, em produção.

O comentário de várias linhas é `{% comment %}…{% endcomment %}`, que este
teste não incomoda. Fora de bloco em template que herda de outro o vazamento
não aparece (o conteúdo é descartado), mas a regra vale para todos: um `{#`
sem `#}` na mesma linha é sempre um acidente esperando o próximo `{% block %}`.
"""
from pathlib import Path

from django.test import SimpleTestCase

RAIZ = Path(__file__).resolve().parents[3]


class ComentariosDeTemplateTests(SimpleTestCase):

    def test_nenhum_comentario_de_uma_linha_aberto_em_duas(self):
        vazando = []
        for caminho in RAIZ.glob('apps/**/templates/**/*.html'):
            texto = caminho.read_text(encoding='utf-8', errors='replace')
            for numero, linha in enumerate(texto.splitlines(), start=1):
                if '{#' in linha and '#}' not in linha:
                    vazando.append(
                        f'{caminho.relative_to(RAIZ)}:{numero}: {linha.strip()[:70]}'
                    )

        self.assertEqual(vazando, [], (
            'Comentário `{# #}` aberto em mais de uma linha — o resto vai '
            'aparecer impresso na tela. Use {% comment %}…{% endcomment %}:\n'
            + '\n'.join(vazando)
        ))
