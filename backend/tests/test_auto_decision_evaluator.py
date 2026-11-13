"""All rows here are synthetic test fixtures, not validation evidence."""
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.auto_decision.evaluate_dataset import evaluate, main, read_jsonl


def synthetic(ident, score, label, model='synthetic-model', ppe='helmet'):
    return {'event_id': ident, 'score': score, 'label': label,
            'model_version': model, 'ppe_type': ppe}


class OfflineEvaluatorTests(unittest.TestCase):
    def test_confusion_counts_threshold_boundary_and_group_isolation(self):
        rows = [synthetic('synthetic-tp', .8, 'violation'),
                synthetic('synthetic-fp', .9, 'non_violation'),
                synthetic('synthetic-tn', .2, 'non_violation'),
                synthetic('synthetic-fn', .7, 'violation'),
                synthetic('synthetic-other-model', 1, 'violation', model='second-model'),
                synthetic('synthetic-other-ppe', 0, 'non_violation', ppe='vest')]
        report = evaluate(rows, threshold=.8)
        group = next(g for g in report['groups'] if g['model_version']=='synthetic-model' and g['ppe_type']=='helmet')
        self.assertEqual([group[k] for k in ('tp','fp','tn','fn')], [1,1,1,1])
        self.assertEqual(group['precision'], .5)
        self.assertEqual(group['recall'], .5)
        self.assertEqual(group['sample_count'], 4)
        self.assertEqual(report['overall']['sample_count'], 6)
        self.assertFalse(report['automatic_confirmation_enabled'])
        self.assertEqual(report, evaluate(reversed(rows), threshold=.8))
        self.assertNotIn('synthetic-tp', json.dumps(report))

    def test_zero_denominators_are_none_not_zero_or_nan(self):
        report = evaluate([synthetic('synthetic-tn', 0, 'non_violation')], threshold=1)
        self.assertIsNone(report['overall']['precision'])
        self.assertIsNone(report['overall']['recall'])
        report = evaluate([synthetic('synthetic-fn', 0, 'violation')], threshold=1)
        self.assertIsNone(report['overall']['precision'])
        self.assertEqual(report['overall']['recall'], 0)
        report = evaluate([synthetic('synthetic-fp', 1, 'non_violation')], threshold=0)
        self.assertEqual(report['overall']['precision'], 0)
        self.assertIsNone(report['overall']['recall'])
        json.dumps(report, allow_nan=False)

    def test_invalid_thresholds_and_scores_rejected(self):
        for value in (True, False, '0.5', None, float('nan'), float('inf'), -float('inf'), -.1, 1.1, 10**1000):
            with self.subTest(value=repr(value)[:40]):
                with self.assertRaises(ValueError):
                    evaluate([synthetic('synthetic-a', .5, 'violation')], threshold=value)
                with self.assertRaises(ValueError):
                    evaluate([synthetic('synthetic-a', value, 'violation')], threshold=.5)

    def test_duplicate_event_fails_even_across_model_or_ppe(self):
        for changes in ({}, {'model_version':'other'}, {'ppe_type':'vest'}):
            row = synthetic('synthetic-duplicate', .5, 'violation')
            with self.assertRaisesRegex(ValueError, 'duplicate event_id'):
                evaluate([row, {**row, **changes}], threshold=.5)

    def test_strict_schema_and_identifiers(self):
        row = synthetic('synthetic-a', .5, 'violation')
        invalid = [{**row, 'unexpected': True}, {k:v for k,v in row.items() if k!='score'}, [],
                   {**row, 'label':'unknown'}, {**row, 'label':True}, {**row, 'ppe_type':'unregistered'}]
        for key in ('event_id','model_version','ppe_type'):
            invalid += [{**row,key:value} for value in ('', ' ', ' padded ', None, 1)]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):evaluate([item], threshold=.5)
        with self.assertRaisesRegex(ValueError, 'at least one'):
            evaluate([], threshold=.5)

    def test_strict_jsonl_and_no_content_echo(self):
        valid = json.dumps(synthetic('synthetic-a', .5, 'violation'))
        self.assertEqual(len(list(read_jsonl([valid]))),1)
        bad = ['', 'not JSON PRIVATE_ID', '[]', valid[:-1]+',"score":0.9}',
               valid.replace('0.5','NaN'),valid.replace('0.5','Infinity'),valid.replace('0.5','1e999')]
        for line in bad:
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, '^line 1:') as result:
                    list(read_jsonl([line]))
                self.assertNotIn('PRIVATE_ID',str(result.exception))
        with self.assertRaisesRegex(ValueError, '^line 2:'):
            list(read_jsonl([valid,'']))

    def test_cli_requires_threshold_and_emits_only_complete_report(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.jsonl'
            path.write_text(json.dumps(synthetic('synthetic-a',.9,'violation'))+'\n',encoding='utf-8')
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([str(path),'--threshold','.9']),0)
            self.assertEqual(json.loads(output.getvalue())['overall']['tp'],1)
            for args in ([str(path)], [str(path),'--threshold','nan']):
                output,errors=io.StringIO(),io.StringIO()
                with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors),self.assertRaises(SystemExit) as result:
                    main(args)
                self.assertEqual(result.exception.code,2)
                self.assertEqual(output.getvalue(),'')
            path.write_text(path.read_text()+'bad row\n')
            output=io.StringIO()
            with contextlib.redirect_stdout(output),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                main([str(path),'--threshold','.9'])
            self.assertEqual(output.getvalue(),'')
