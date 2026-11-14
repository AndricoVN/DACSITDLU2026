"""Offline regression tests for v2 preparation/validation; no external services."""
import unittest

from prepare_v2 import config_text
from validate_v2 import HiddenInputs


class Milestone2Tests(unittest.TestCase):
    def test_config_isolated_and_outbound_mail_off(self):
        config = config_text("synthetic-test-password")
        for fragment in ("$CFG->dbname='moodledb_v2'", "$CFG->dbuser='dacs_v2_app'",
                         "$CFG->wwwroot='http://localhost:8081'", "$CFG->sessioncookie='V2'",
                         "$CFG->dataroot='/var/moodledata'", "$CFG->noemailever=true",
                         "$CFG->cron_enabled=false"):
            self.assertIn(fragment, config)
        self.assertNotIn("$CFG->dbname='moodledb';", config)
        self.assertNotIn("$CFG->dbuser='root';", config)

    def test_login_form_token_does_not_depend_on_attribute_order(self):
        parser = HiddenInputs()
        parser.feed('<input value="token&amp;value" name="logintoken" type="hidden">'
                    '<input name="username" type="text" value="not-a-token">')
        self.assertEqual(parser.values, {"logintoken": "token&value"})


if __name__ == "__main__":
    unittest.main()
