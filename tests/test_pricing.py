import copy
import unittest
from unittest.mock import patch

from app.pricing import calculate_quote, pricing_config, PricingError


class PricingTest(unittest.TestCase):
    def test_spec_example(self):
        quote = calculate_quote('in_person', 'cash', '40', 20, 'within_1_week')
        self.assertEqual(quote['reward_per_person_cents'], 4000)
        self.assertEqual(quote['recruitment_fee_per_person_cents'], 1500)
        self.assertTrue(quote['minimum_applied'])
        self.assertEqual(quote['urgency_amount_cents'], 11000)
        self.assertEqual(quote['total_cents'], 121000)

    def test_rate_and_minimum_thresholds(self):
        for kind, minimum, threshold in [('survey', 500, '25'), ('interview', 1000, '40'), ('in_person', 1500, '60')]:
            for incentive in ['cash', 'gift_card']:
                with self.subTest(kind=kind, incentive=incentive):
                    low = calculate_quote(kind, incentive, '1', 3)
                    exact = calculate_quote(kind, incentive, threshold, 3)
                    high = calculate_quote(kind, incentive, '100', 3)
                    self.assertEqual(low['recruitment_fee_per_person_cents'], minimum)
                    self.assertTrue(low['minimum_applied'])
                    self.assertFalse(exact['minimum_applied'])
                    self.assertEqual(high['recruitment_fee_per_person_cents'], 2000 if kind == 'survey' else 2500)

    def test_non_cash_charges_minimum_per_target_person(self):
        for kind, fee in [('survey', 500), ('interview', 1000), ('in_person', 1500)]:
            for incentive in ['raffle', 'volunteer']:
                q = calculate_quote(kind, incentive, '999', 20, 'within_1_month')
                self.assertEqual(q['reward_per_person_cents'], 0)
                self.assertEqual(q['total_cents'], fee * 21)

    def test_external_self_guided_aliases_use_survey_pricing(self):
        for kind in ['builtin', 'external', 'ux_test', 'diary_study']:
            self.assertEqual(calculate_quote(kind, 'cash', '10', 20)['total_cents'], 30000)

    def test_integer_cent_rounding(self):
        q = calculate_quote('survey', 'cash', '25.03', 3, 'within_1_month')
        self.assertEqual(q['recruitment_fee_per_person_cents'], 501)
        self.assertEqual(q['subtotal_cents'], 9012)
        self.assertEqual(q['urgency_amount_cents'], 451)
        self.assertEqual(q['total_cents'], 9463)

    def test_invalid_inputs(self):
        for field, values in [('per_person', ['NaN', 'Infinity', '-1', '1.001', 'abc', None, '0']),
                              ('target_responses', [0, -1, '1.5', True, None, 100001]),
                              ('study_type', ['niche', 'invalid', None, []]),
                              ('incentive_type', ['invalid', None]),
                              ('urgency_level', ['invalid'])]:
            for value in values:
                args = dict(study_type='survey', incentive_type='cash', per_person='10', target_responses=1)
                args[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(PricingError):
                    calculate_quote(**args)

    def test_config_changes_only_affect_new_quotes(self):
        old = calculate_quote('survey', 'cash', '10', 20)
        config = copy.deepcopy(pricing_config())
        config['study_types']['survey']['minimum_fee_cents'] = 800
        with patch('app.pricing.pricing_config', return_value=config):
            new = calculate_quote('survey', 'cash', '10', 20)
        self.assertEqual(old['total_cents'], 30000)
        self.assertEqual(new['total_cents'], 36000)


if __name__ == '__main__':
    unittest.main()
