import tempfile
import unittest
from pathlib import Path
from . import domain_checks

class DomainKeyTests(unittest.TestCase):
    def test_every_seed_key(self):
        with tempfile.TemporaryDirectory() as folder:
            domain_checks.test_every_seed_key_runs_without_switching_at_shared_candidate(Path(folder))

    def test_shared_source(self):
        with tempfile.TemporaryDirectory() as folder:
            domain_checks.test_all_keys_on_shared_source_are_eligible(Path(folder))

    def test_inactive_edges(self):
        with tempfile.TemporaryDirectory() as folder:
            domain_checks.test_inactive_edges_do_not_open_branches(Path(folder))

    def test_snapshot_compatibility(self):
        domain_checks.test_real_database_preserves_baseline_candidates_and_adds_branch_provenance()
