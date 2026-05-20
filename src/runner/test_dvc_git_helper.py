import unittest
import os
import shutil
import tempfile
from pathlib import Path
from ruamel.yaml import YAML

# Add src to sys.path to import dvc_git_helper
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from src.runner.dvc_git_helper import inject_cache_false, get_cache_false_paths

class TestDVCGitHelper(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.dvc_yaml = os.path.join(self.test_dir, 'dvc.yaml')
        self.yaml = YAML()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_inject_shorthand(self):
        content = {
            'stages': {
                'train': {
                    'cmd': 'python train.py',
                    'metrics': ['metrics.json'],
                    'plots': ['plots.csv']
                }
            }
        }
        with open(self.dvc_yaml, 'w') as f:
            self.yaml.dump(content, f)

        inject_cache_false(self.dvc_yaml)

        with open(self.dvc_yaml, 'r') as f:
            data = self.yaml.load(f)

        self.assertEqual(data['stages']['train']['metrics'][0]['metrics.json']['cache'], False)
        self.assertEqual(data['stages']['train']['plots'][0]['plots.csv']['cache'], False)

    def test_inject_longhand(self):
        content = {
            'stages': {
                'train': {
                    'cmd': 'python train.py',
                    'metrics': [{'metrics.json': {'cache': True}}],
                    'plots': [{'plots.csv': {}}]
                }
            }
        }
        with open(self.dvc_yaml, 'w') as f:
            self.yaml.dump(content, f)

        inject_cache_false(self.dvc_yaml)

        with open(self.dvc_yaml, 'r') as f:
            data = self.yaml.load(f)

        self.assertEqual(data['stages']['train']['metrics'][0]['metrics.json']['cache'], False)
        self.assertEqual(data['stages']['train']['plots'][0]['plots.csv']['cache'], False)

    def test_get_paths(self):
        content = {
            'stages': {
                'train': {
                    'metrics': [{'m1.json': {'cache': False}}, {'m2.json': {'cache': True}}],
                    'plots': [{'p1.csv': {'cache': False}}]
                }
            }
        }
        with open(self.dvc_yaml, 'w') as f:
            self.yaml.dump(content, f)

        paths = get_cache_false_paths(self.dvc_yaml)
        self.assertIn('m1.json', paths)
        self.assertIn('p1.csv', paths)
        self.assertNotIn('m2.json', paths)

if __name__ == '__main__':
    unittest.main()
