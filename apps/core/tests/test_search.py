from django.db import models
from django.test import SimpleTestCase

from apps.core.db_lookups import AccentInsensitiveIContains
from apps.core.services.search import (
    filter_queryset_by_terms,
    normalize_search_text,
    ranked_search_ids,
)


class SearchNormalizationTests(SimpleTestCase):
    def setUp(self):
        self.products = [
            {'pk': 1, 'name': 'ABACAXI 1 KG POLPA', 'code': '10'},
            {'pk': 2, 'name': 'GOIABA 1 KG POLPA', 'code': '20'},
            {'pk': 3, 'name': 'MANGABA 400 G', 'code': '30'},
            {'pk': 4, 'name': 'CAJÁ 1 KG POLPA', 'code': '40'},
            {'pk': 5, 'name': 'CAJÁ BARRA KG POLPA', 'code': '50'},
            {'pk': 6, 'name': 'AÇÚCAR CRISTAL', 'code': '60'},
            {'pk': 7, 'name': 'FARINHA AMARELA', 'code': '70'},
        ]

    def search(self, query):
        return ranked_search_ids(
            self.products,
            query,
            name_fields=('name',),
            code_fields=('code',),
            limit=20,
        )

    def test_normalizes_accents_cedilla_and_punctuation(self):
        self.assertEqual(normalize_search_text(' AÇÚCAR-d’Água '), 'acucar d agua')

    def test_matches_only_at_word_start(self):
        self.assertEqual(self.search('aba'), [1])

    def test_single_character_matches_an_exact_word(self):
        self.products.append({'pk': 8, 'name': 'CAMISA POLO TAM P', 'code': '80'})
        self.assertEqual(self.search('polo p'), [8])

    def test_complete_term_can_match_later_word(self):
        self.assertEqual(self.search('amarela'), [7])

    def test_accented_and_unaccented_queries_are_equivalent(self):
        self.assertEqual(self.search('caja'), [4, 5])
        self.assertEqual(self.search('cajá'), [4, 5])
        self.assertEqual(self.search('acucar'), [6])

    def test_every_search_term_must_match(self):
        self.assertEqual(self.search('caja barra'), [5])

    def test_global_icontains_lookup_is_registered(self):
        self.assertIs(models.CharField().get_lookup('icontains'), AccentInsensitiveIContains)
        self.assertIs(models.TextField().get_lookup('icontains'), AccentInsensitiveIContains)

    def test_queryset_search_requires_every_word_without_requiring_order(self):
        queryset = _FakeQuerySet()
        result = filter_queryset_by_terms(
            queryset, 'bege polo', fields=('nome', 'referencia'),
        )
        self.assertEqual(len(result.filters), 2)

    def test_queryset_search_treats_single_character_as_exact_word(self):
        queryset = _FakeQuerySet()
        result = filter_queryset_by_terms(
            queryset, 'polo p', fields=('nome', 'referencia'),
        )

        self.assertEqual(len(result.filters), 2)
        one_letter_lookups = str(result.filters[1])
        self.assertIn('nome__iregex', one_letter_lookups)
        self.assertIn('referencia__iregex', one_letter_lookups)


class _FakeQuerySet:
    def __init__(self, filters=None):
        self.filters = filters or []

    def filter(self, condition):
        return _FakeQuerySet([*self.filters, condition])
