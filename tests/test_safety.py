import argparse
import builtins
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("iplc", Path(__file__).resolve().parents[1] / "iplc.py")
iplc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iplc)


def write_archive(path, state, extra_path=None):
    with tarfile.open(path, "w:gz") as tf:
        raw = json.dumps(state).encode()
        member = tarfile.TarInfo("state.json")
        member.size = len(raw)
        tf.addfile(member, io.BytesIO(raw))
        if extra_path:
            member = tarfile.TarInfo(extra_path)
            member.size = 1
            tf.addfile(member, io.BytesIO(b"x"))


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paths = {
            "STATE_DIR": self.root / "state",
            "STATE_FILE": self.root / "state/rules.json",
            "CONFIG_DIR": self.root / "etc/nftables.d",
            "CONFIG_FILE": self.root / "etc/nftables.d/iplc-light.nft",
            "NFT_MAIN": self.root / "etc/nftables.conf",
            "BACKUP_DIR": self.root / "backups",
        }
        for name, value in paths.items():
            p = patch.object(iplc, name, value)
            p.start()
            self.addCleanup(p.stop)
        iplc.ensure_dirs()
        self.good = {"version": 1, "rules": [{"listen_port": 37185, "target_ip": "1.2.3.4", "target_port": 25884}]}

    def test_missing_state_does_not_replace_live_or_persisted_rules(self):
        with patch.object(iplc, "need_root"), patch.object(iplc, "check_and_apply_candidate") as apply:
            with patch.object(iplc, "table_exists", return_value=True):
                with self.assertRaises(SystemExit):
                    iplc.init_cmd()
            iplc.CONFIG_FILE.write_text("table ip iplc_light {}\n")
            with patch.object(iplc, "table_exists", return_value=False):
                with self.assertRaises(SystemExit):
                    iplc.init_cmd()
            self.assertFalse(iplc.STATE_FILE.exists())
            apply.assert_not_called()

    def test_restore_reads_only_valid_state_without_extracting_other_paths(self):
        arc = self.root / "uploaded.tar.gz"
        write_archive(arc, self.good, "../escaped-marker")
        with patch.object(iplc, "need_root"), patch.object(iplc, "backup", return_value="test-backup"):
            with patch.object(iplc, "check_and_apply_candidate") as apply:
                iplc.restore_cmd(argparse.Namespace(archive=str(arc), yes=True))
                self.assertEqual(apply.call_args.args[0]["rules"][0]["listen_port"], 37185)
        self.assertFalse((self.root / "escaped-marker").exists())

    def test_restore_rejects_invalid_rule_and_zero_selection(self):
        arc = iplc.BACKUP_DIR / "20260927_manual.tar.gz"
        bad = {"version": 1, "rules": [{"listen_port": 37185, "target_ip": "1.2.3.4\nflush ruleset", "target_port": 25884}]}
        write_archive(arc, bad)
        with patch.object(iplc, "need_root"), patch.object(iplc, "check_and_apply_candidate") as apply:
            with self.assertRaises(SystemExit):
                iplc.restore_cmd(argparse.Namespace(archive=str(arc), yes=True))
            write_archive(arc, self.good)
            with patch.object(builtins, "input", return_value="0"):
                with self.assertRaises(SystemExit):
                    iplc.restore_cmd(argparse.Namespace(archive=None, yes=False))
            apply.assert_not_called()

    def test_commented_include_is_not_active(self):
        iplc.NFT_MAIN.write_text('# include "/etc/nftables.d/iplc-light.nft"\n')
        self.assertFalse(iplc.check_include_present())
        iplc.ensure_include()
        self.assertTrue(iplc.check_include_present())

    def test_unknown_external_dnat_blocks_add(self):
        rule = {
            "family": "ip", "table": "external_nat", "chain": "prerouting", "handle": 5,
            "expr": [
                {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": {"set": [37185, 37186]}}},
                {"dnat": {"addr": "1.2.3.4", "port": 25884}},
            ],
        }
        with patch.object(iplc, "nft_json", return_value={"nftables": [{"rule": rule}]}):
            with self.assertRaises(SystemExit):
                iplc.external_conflicts(37185)
            rule["expr"][0]["match"]["right"] = 37185
            self.assertEqual(len(iplc.external_conflicts(37185)), 1)


if __name__ == "__main__":
    unittest.main()
