import hashlib, unittest
from unittest.mock import patch
from test_app import SafetyTests
import main
class RecoveryTests(SafetyTests):
    def setUp(self):
        super().setUp()
        p=patch.dict(main.os.environ,{'APP_URL':'https://runback.example','BREVO_API_KEY':'test-key','EMAIL_FROM':'sender@example.com'});p.start();self.addCleanup(p.stop)
        p=patch.object(main,'send_recovery');self.mail=p.start();self.addCleanup(p.stop)
    def request_link(self,email='alice@test.com'):
        return self.req('/api/password/forgot','anonymous',{'email':email})
    def reset(self,token,password='new-password-123'):
        return self.req('/api/password/reset','anonymous',{'token':token,'password':password})
    def test_single_use_replaces_password_and_revokes_sessions(self):
        self.assertEqual(self.request_link()[0],200)
        raw=self.mail.call_args.args[1]
        with main.db() as d:
            r=d.execute('SELECT * FROM password_resets').fetchone()
            self.assertEqual(r['token'],hashlib.sha256(raw.encode()).hexdigest());self.assertNotEqual(r['token'],raw)
        self.assertEqual(self.reset(raw)[0],200)
        self.assertEqual(self.reset(raw)[0],400)
        self.assertIsNone(self.req('/api/me')[1]['user'])
        with main.db() as d:
            stored=d.execute("SELECT password FROM users WHERE id='alice'").fetchone()['password']
            self.assertEqual(main.pwd('new-password-123',stored.split(':')[0]),stored)
        self.assertEqual(self.req('/api/login','anonymous',{'email':'alice@test.com','password':'new-password-123'})[0],200)
    def test_expiry_invalid_and_replaced_links(self):
        self.request_link();old=self.mail.call_args.args[1]
        self.request_link();new=self.mail.call_args.args[1]
        self.assertEqual(self.reset(old)[0],400)
        self.assertEqual(self.reset('x'*43)[0],400)
        with main.db() as d:d.execute('UPDATE password_resets SET expires=0')
        self.assertEqual(self.reset(new)[0],400)
    def test_unknown_and_banned_use_same_response(self):
        known=self.request_link()[1]
        self.assertEqual(self.request_link('nobody@test.com')[1],known)
        self.ban();self.mail.reset_mock()
        self.assertEqual(self.request_link('bob@test.com')[1],known)
        self.mail.assert_not_called()
    def test_ban_invalidates_preexisting_link(self):
        self.request_link('bob@test.com');raw=self.mail.call_args.args[1];self.ban()
        self.assertEqual(self.reset(raw)[0],400)
    def test_rate_limit_and_delivery_failure(self):
        for _ in range(5):self.assertEqual(self.request_link()[0],200)
        self.assertEqual(self.mail.call_count,3)
        self.mail.side_effect=OSError('private provider details')
        self.request_link('bob@test.com')
        with main.db() as d:self.assertFalse(d.execute("SELECT 1 FROM password_resets WHERE user_id='bob'").fetchone())
    def test_not_configured_and_bad_password(self):
        with patch.dict(main.os.environ,{'BREVO_API_KEY':''}):self.assertEqual(self.request_link()[0],503)
        self.request_link();raw=self.mail.call_args.args[1]
        self.assertEqual(self.reset(raw,'short')[0],400)
        self.assertEqual(self.reset(raw)[0],200)
    def test_global_limit(self):
        with main.db() as d:d.execute('INSERT INTO attempts VALUES(?,?,?)',('reset-global',100,9999999999))
        self.assertEqual(self.request_link()[0],200);self.mail.assert_not_called()
if __name__=='__main__':unittest.main()
