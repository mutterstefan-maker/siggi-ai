import unittest
from unittest.mock import Mock, patch

import shopware_engine as shop


class ReadOnlyTests(unittest.TestCase):
    def test_mutating_and_unexpected_paths_rejected_before_network_or_credentials(self):
        with patch.object(shop.requests, 'post') as network, patch.object(shop, '_cfg') as config:
            for entity in ('../order', '../../_action/sync', 'order/123', 'product?x=1',
                           'integration', 'user', 'acl-role', '', None, ['order']):
                with self.assertRaises(PermissionError):
                    shop._search(entity, {})
            network.assert_not_called()
            config.assert_not_called()

    def test_only_fixed_read_search_routes_are_used_without_redirects(self):
        with patch.object(shop, '_cfg', return_value={'url': 'https://example.com'}), \
             patch.object(shop, '_bearer', return_value='test'), \
             patch.object(shop.requests, 'post', return_value=Mock(status_code=200, json=lambda: {'data':[]})) as network:
            for entity in shop.READ_ENTITIES:
                shop._search(entity, {'limit':1})
                self.assertEqual(network.call_args.args[0], 'https://example.com/api/search/' + entity)
                self.assertFalse(network.call_args.kwargs['allow_redirects'])

    def test_search_redirect_and_denied_permissions_fail_closed(self):
        with patch.object(shop, '_cfg', return_value={'url': 'https://example.com'}), \
             patch.object(shop, '_bearer', return_value='test'), \
             patch.object(shop.requests, 'post') as network:
            network.return_value = Mock(status_code=307)
            with self.assertRaises(PermissionError):
                shop._search('order', {})
            network.return_value = Mock(status_code=403, json=lambda: {'errors':[{'detail':'{"missingPrivileges":["order:read"]}'}]})
            with self.assertRaisesRegex(PermissionError, 'order:read'):
                shop._search('order', {})


class DailyOrderTests(unittest.TestCase):
    def test_day_boundaries_follow_berlin_dst(self):
        for day, start, end in [
            ('2026-11-13', '2026-11-12T23:00:00Z', '2026-11-13T23:00:00Z'),
            ('2026-03-29', '2026-03-28T23:00:00Z', '2026-03-29T22:00:00Z'),
            ('2026-10-25', '2026-10-24T22:00:00Z', '2026-10-25T23:00:00Z'),
        ]:
            with self.subTest(day=day), patch.object(shop, '_search', return_value=123) as search:
                result = shop.bestellungen_tag(day)
                self.assertEqual(result['bestellungen'], 123)
                body = search.call_args.args[1]
                self.assertEqual(body['filter'][0]['parameters'], {'gte': start, 'lt': end})
                self.assertEqual(body['total-count-mode'], 1)
                self.assertTrue(search.call_args.kwargs['total'])

    def test_total_is_not_the_page_length(self):
        with patch.object(shop, '_cfg', return_value={'url': 'https://example.com'}), \
             patch.object(shop, '_bearer', return_value='test'), \
             patch.object(shop.requests, 'post', return_value=Mock(status_code=200, json=lambda: {'total': 123, 'data': [{'id': 'one'}]})):
            self.assertEqual(shop._search('order', {}, total=True), 123)

    def test_missing_total_is_not_reported_as_zero(self):
        with patch.object(shop, '_cfg', return_value={'url': 'https://example.com'}), \
             patch.object(shop, '_bearer', return_value='test'), \
             patch.object(shop.requests, 'post', return_value=Mock(status_code=200, json=lambda: {'data': []})):
            with self.assertRaises(ValueError):
                shop._search('order', {}, total=True)

    def test_invalid_date_does_not_query_shop(self):
        with patch.object(shop, '_search') as search:
            with self.assertRaises(ValueError):
                shop.bestellungen_tag('not-a-date')
            search.assert_not_called()


class FlexibleReadTests(unittest.TestCase):
    def test_pagination_preserves_exact_total_and_does_not_mutate_input(self):
        criteria = {'limit': 500, 'page': 2, 'filter': [{'type': 'equals', 'field': 'active', 'value': True}]}
        with patch.object(shop, '_search', return_value={'total': 247, 'data': [{'id': 'one'}]}) as search:
            result = shop.daten_lesen('product', criteria)
            self.assertEqual(result['gesamt'], 247)
            self.assertTrue(result['weitere_seiten'])
            self.assertEqual(search.call_args.args[1]['limit'], 100)
            self.assertEqual(criteria['limit'], 500)

    def test_invalid_entities_and_parameters_never_reach_network(self):
        with patch.object(shop, '_search') as search:
            for entity in ('integration', '../order', 'user'):
                with self.assertRaises(PermissionError):
                    shop.daten_lesen(entity)
            with self.assertRaises(ValueError):
                shop.daten_lesen('order', {'url': 'https://example.com'})
            with self.assertRaises(ValueError):
                shop.daten_lesen('order', [])
            search.assert_not_called()

    def test_order_details_load_positions_and_use_inclusive_date_range(self):
        with patch.object(shop, 'daten_lesen', return_value={'daten': [], 'weitere_seiten': False}) as read:
            shop.bestellungen_lesen('2026-11-12', '2026-11-13', 2, 50)
            entity, criteria = read.call_args.args
            self.assertEqual(entity, 'order')
            self.assertEqual(criteria['page'], 2)
            self.assertIn('lineItems', criteria['associations'])
            self.assertIn('payload', criteria['includes']['order_line_item'])
            self.assertEqual(criteria['filter'][0]['parameters'],
                             {'gte': '2026-11-11T23:00:00Z', 'lt': '2026-11-13T23:00:00Z'})

    def test_inverted_date_range_is_rejected(self):
        with patch.object(shop, 'daten_lesen') as read:
            with self.assertRaises(ValueError):
                shop.bestellungen_lesen('2026-11-13', '2026-11-12')
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
