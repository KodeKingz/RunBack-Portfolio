"""Focused suspension regressions with isolated SQLite storage."""
import io, json, os, tempfile, unittest
from unittest.mock import patch
_tmp=tempfile.TemporaryDirectory()
with patch.dict(os.environ,{'DB_PATH':_tmp.name+'/boot.db','DATABASE_URL':'','RUNBACK_PRODUCTION':'0','DEMO_MODE':'0'}):
    import main
class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p=patch.object(main,'DB_PATH',self.tmp.name+'/test.db'); p.start(); self.addCleanup(p.stop)
        p=patch.dict(os.environ,{'ADMIN_EMAIL':'admin@test.com'}); p.start(); self.addCleanup(p.stop)
        main.init(); self.actors={}
        with main.db() as d:
            for name in ('admin','alice','bob'):
                d.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(name,name+'@test.com','disabled',name,'Toronto',main.GAMES[0],'Ryu','Gold','PC','',0,0,0,0,0))
                d.execute('INSERT INTO tokens VALUES(?,?,?,?)',(main.hashlib.sha256(name.encode()).hexdigest(),name,name,9999999999))
            for a,b in [('alice','bob'),('bob','alice')]: d.execute('INSERT INTO swipes VALUES(?,?,?)',(a,b,'challenge'))
    def req(self,path,who='alice',body=None,query=''):
        raw=json.dumps(body or {}).encode(); status=[]
        e={'PATH_INFO':path,'REQUEST_METHOD':'POST' if body is not None else 'GET','QUERY_STRING':query,'CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_COOKIE':'runback='+who,'HTTP_X_RUNBACK':'1','HTTP_X_CSRF':who}
        data=json.loads(b''.join(main.application(e,lambda s,h:status.append(int(s.split()[0])))))
        return status[0],data
    def ban(self):
        self.assertEqual(self.req('/api/report',body={'target':'bob','reason':'Regression test'})[0],200)
        ident=self.req('/api/admin','admin')[1]['reports'][0]['id']
        self.assertEqual(self.req('/api/admin','admin',{'id':ident,'ban':True})[0],200)
    def test_chat_scores_and_tokens(self):
        self.assertEqual(self.req('/api/messages',body={'target':'bob','body':'Hello'})[0],200)
        self.assertEqual(self.req('/api/results','bob',{'target':'alice','mine':2,'theirs':1})[0],200)
        with main.db() as d: ident=d.execute('SELECT id FROM results').fetchone()['id']
        self.ban()
        self.assertIsNone(self.req('/api/me','bob')[1]['user'])
        for path,body in [('/api/messages',{'target':'bob','body':'Denied'}),('/api/results',{'target':'bob','mine':1,'theirs':0}),('/api/results/confirm',{'id':ident,'accept':True})]:
            self.assertEqual(self.req(path,body=body)[0],403)
        self.assertEqual(self.req('/api/messages',query='target=bob')[0],403)
        self.assertEqual(self.req('/api/messages','bob',{'target':'alice','body':'Denied'})[0],401)
        self.assertEqual(self.req('/api/friends')[1]['friends'],[])
        with main.db() as d:
            self.assertEqual(d.execute('SELECT COUNT(*) AS n FROM messages').fetchone()['n'],1)
            self.assertEqual(d.execute('SELECT status FROM results').fetchone()['status'],'pending')
            self.assertFalse(d.execute("SELECT 1 FROM tokens WHERE user_id='bob'").fetchone())
    def test_host_resources(self):
        self.assertEqual(self.req('/api/sessions','bob',{'title':'Test session','game':main.GAMES[0],'city':'Toronto','mode':'Online','venue':'Lobby','starts':'2099-01-01T12:00:00+00:00','capacity':4})[0],200)
        self.assertEqual(self.req('/api/crews','bob',{'name':'Test crew','game':main.GAMES[0],'city':'Toronto','description':'Test crew members'})[0],200)
        ids={r:self.req('/api/'+r)[1][r][0]['id'] for r in ('sessions','crews')}
        for route,ident in ids.items():
            self.assertEqual(self.req('/api/'+route+'/join',body={'id':ident})[0],200)
            self.assertEqual(self.req('/api/'+route+'/join',body={'id':ident,'leave':True})[0],200)
        self.ban()
        for route,ident in ids.items():
            self.assertEqual(self.req('/api/'+route)[1][route],[])
            self.assertEqual(self.req('/api/'+route+'/join',body={'id':ident})[0],404)
        with main.db() as d:
            for table in ('attendees','members'): self.assertFalse(d.execute('SELECT 1 FROM '+table+" WHERE user_id='alice'").fetchone())
    def test_block_and_unblock(self):
        self.assertEqual(self.req('/api/block','bob',{'target':'alice'})[0],200)
        self.assertEqual(self.req('/api/messages',body={'target':'bob','body':'Denied'})[0],403)
        self.assertEqual(self.req('/api/unblock','bob',{'target':'alice'})[0],200)
        self.assertEqual(self.req('/api/messages',body={'target':'bob','body':'Allowed'})[0],200)
if __name__=='__main__': unittest.main()
