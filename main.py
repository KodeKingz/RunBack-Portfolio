"""RUNBACK: WSGI application. SQLite locally; PostgreSQL via DATABASE_URL."""
import os, json, sqlite3, secrets, hashlib, hmac, time, re, mimetypes
from pathlib import Path
from urllib.parse import parse_qs
from http.cookies import SimpleCookie
from contextlib import contextmanager
from wsgiref.simple_server import make_server
ROOT=Path(__file__).parent
DB_PATH=os.getenv('DB_PATH',str(ROOT/'data'/'runback.db'))
DATABASE_URL=os.getenv('DATABASE_URL','')
if os.getenv('RUNBACK_PRODUCTION')=='1' and not DATABASE_URL:
    raise RuntimeError('Set DATABASE_URL to a persistent PostgreSQL database before publishing.')
GAMES=['Street Fighter 6','Tekken 8','Guilty Gear Strive','Mortal Kombat 1','2XKO','Fatal Fury','Dragon Ball FighterZ']
@contextmanager
def db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        conn=psycopg.connect(DATABASE_URL,row_factory=dict_row)
    else:
        Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(DB_PATH,timeout=20); conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
    class Store:
        def execute(self,sql,args=()): return conn.execute(sql.replace('?', '%s') if DATABASE_URL else sql,args)
    try:
        yield Store(); conn.commit()
    except Exception: conn.rollback(); raise
    finally: conn.close()
def init():
    with db() as d:
        for sql in [
        'CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,name TEXT NOT NULL,city TEXT NOT NULL,game TEXT NOT NULL,main TEXT NOT NULL,rank TEXT NOT NULL,platform TEXT NOT NULL,bio TEXT NOT NULL,ready INTEGER NOT NULL DEFAULT 0,ready_until BIGINT NOT NULL DEFAULT 0,demo INTEGER NOT NULL DEFAULT 0,banned INTEGER NOT NULL DEFAULT 0,created BIGINT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires BIGINT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS swipes(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,target TEXT REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,PRIMARY KEY(user_id,target))',
        'CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,sender TEXT REFERENCES users(id) ON DELETE CASCADE,recipient TEXT REFERENCES users(id) ON DELETE CASCADE,body TEXT NOT NULL,created BIGINT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS blocks(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,target TEXT REFERENCES users(id) ON DELETE CASCADE,PRIMARY KEY(user_id,target))',
        'CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,reporter TEXT REFERENCES users(id) ON DELETE CASCADE,target TEXT REFERENCES users(id) ON DELETE CASCADE,reason TEXT NOT NULL,created BIGINT NOT NULL,status TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,host TEXT REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,game TEXT NOT NULL,city TEXT NOT NULL,mode TEXT NOT NULL,venue TEXT NOT NULL,starts TEXT NOT NULL,capacity INTEGER NOT NULL,created BIGINT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS attendees(session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,PRIMARY KEY(session_id,user_id))',
        'CREATE TABLE IF NOT EXISTS crews(id TEXT PRIMARY KEY,owner TEXT REFERENCES users(id) ON DELETE CASCADE,name TEXT NOT NULL,city TEXT NOT NULL,game TEXT NOT NULL,description TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS members(crew_id TEXT REFERENCES crews(id) ON DELETE CASCADE,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,PRIMARY KEY(crew_id,user_id))',
        'CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,proposer TEXT REFERENCES users(id) ON DELETE CASCADE,opponent TEXT REFERENCES users(id) ON DELETE CASCADE,mine INTEGER NOT NULL,theirs INTEGER NOT NULL,status TEXT NOT NULL,created BIGINT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS password_resets(token TEXT PRIMARY KEY,user_id TEXT UNIQUE REFERENCES users(id) ON DELETE CASCADE,expires BIGINT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,reset_at BIGINT NOT NULL)']:
            d.execute(sql)
        if os.getenv('DEMO_MODE','0')=='1' and not d.execute('SELECT id FROM users WHERE demo=1').fetchone():
            for i,(name,main,rank,city) in enumerate([('NeonParry','Chun-Li','Diamond','Toronto'),('FramePerfect','Ryu','Platinum','Toronto'),('ArcadeGhost','Juri','Master','Toronto'),('LowKickClub','Ken','Gold','Mississauga')]):
                d.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(f'demo-{i}',f'demo-{i}@invalid.local','disabled',name,city,GAMES[0],main,rank,'Crossplay','Sample profile • Enable real accounts to find your community.',1,int(time.time())+86400,1,0,int(time.time())))
def uid(): return secrets.token_hex(16)
def public(u): return {k:u[k] for k in ('id','name','city','game','main','rank','platform','bio','demo')} | {'ready':bool(u['ready'] and u['ready_until']>time.time())}
def pwd(p,salt=None):
    # Tag new hashes so they remain portable across Python installations.
    salt=salt or 'pbkdf2-sha256$600000$'+secrets.token_hex(16)
    if salt.startswith('pbkdf2-sha256$'):
        _,rounds,raw_salt=salt.split('$')
        digest=hashlib.pbkdf2_hmac('sha256',p.encode(),raw_salt.encode(),int(rounds)).hex()
    else:
        # Preserve verification of accounts created with the original release.
        if not hasattr(hashlib,'scrypt'):
            raise APIError('This account uses an older password format. Open it with a Python installation that supports scrypt.',503)
        digest=hashlib.scrypt(p.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
    return salt+':'+digest
class APIError(Exception):
    def __init__(self,msg,status=400): self.msg=msg; self.status=status
def check(ok,msg,status=400):
    if not ok: raise APIError(msg,status)
def textval(b,key,minlen=1,maxlen=120):
    v=b.get(key,''); check(isinstance(v,str),f'{key}: text required')
    v=v.strip(); check(minlen<=len(v)<=maxlen,f'{key}: use {minlen}–{maxlen} characters'); return v
def blocked(d,a,b): return d.execute('SELECT 1 FROM blocks WHERE (user_id=? AND target=?) OR (user_id=? AND target=?)',(a,b,b,a)).fetchone()
def active_player(d,ident):
    return bool(d.execute('SELECT 1 FROM users WHERE id=? AND banned=0 AND demo=0',(ident,)).fetchone())
def matched(d,a,b):
    return a!=b and active_player(d,a) and active_player(d,b) and not blocked(d,a,b) and d.execute("SELECT 1 FROM swipes a JOIN swipes b ON a.user_id=b.target AND a.target=b.user_id WHERE a.user_id=? AND a.target=? AND a.kind='challenge' AND b.kind='challenge'",(a,b)).fetchone()
def profile(b):
    p={k:textval(b,k,0 if k=='bio' else 1,500 if k=='bio' else 80) for k in ('name','city','game','main','rank','platform','bio')}
    check(p['game'] in GAMES,'Choose a supported game'); return p

def recovery_config():
    from urllib.parse import urlsplit
    origin=os.getenv('APP_URL','').rstrip('/')
    u=urlsplit(origin)
    return bool(os.getenv('BREVO_API_KEY') and os.getenv('EMAIL_FROM') and u.scheme=='https' and u.netloc and not u.username and not u.password and not u.query and not u.fragment and u.path=='')
def send_recovery(email,raw):
    from urllib.request import Request,urlopen
    link=os.environ['APP_URL'].rstrip('/')+'/#reset='+raw
    payload={'sender':{'name':'RUNBACK','email':os.environ['EMAIL_FROM']},'to':[{'email':email}],
             'subject':'Reset your RUNBACK password',
             'textContent':'A password reset was requested for your RUNBACK account.\n\n'+link+'\n\nThis link expires in 30 minutes and works once. If you did not request this, ignore this email. Your password has not changed.'}
    request=Request('https://api.brevo.com/v3/smtp/email',data=json.dumps(payload).encode(),headers={'api-key':os.environ['BREVO_API_KEY'],'Content-Type':'application/json','Accept':'application/json'},method='POST')
    with urlopen(request,timeout=10) as response: response.read()
def recovery_limit(d,key,now,maximum,window):
    # Atomic upsert handles competing requests on PostgreSQL as well as SQLite.
    row=d.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN attempts.reset_at<=? THEN 1 ELSE attempts.count+1 END,reset_at=CASE WHEN attempts.reset_at<=? THEN excluded.reset_at ELSE attempts.reset_at END RETURNING count',(key,now+window,now,now)).fetchone()
    return row['count']<=maximum

def api(e,b):
    path=e['PATH_INFO']; method=e['REQUEST_METHOD']; now=int(time.time()); headers=[]
    reads={'/api/me','/api/discover','/api/friends','/api/messages','/api/blocks','/api/sessions','/api/crews','/api/admin'}
    check(method!='GET' or path in reads,'Method not allowed',405)
    check(method!='POST' or path not in {'/api/me','/api/discover','/api/friends','/api/blocks'},'Method not allowed',405)
    cookies=SimpleCookie(); cookies.load(e.get('HTTP_COOKIE','')); token=cookies.get('runback'); token=token.value if token else ''
    with db() as d:
        auth=d.execute('SELECT t.csrf,u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE token=? AND expires>? AND u.banned=0',(hashlib.sha256(token.encode()).hexdigest(),now)).fetchone() if token else None
        if method!='GET':
            check(e.get('HTTP_X_RUNBACK')=='1','Invalid request',403)
            if auth: check(hmac.compare_digest(e.get('HTTP_X_CSRF',''),auth['csrf']),'Refresh this page and try again',403)
        if path=='/api/me' and method=='GET': return {'user':public(auth) if auth else None,'csrf':auth['csrf'] if auth else None,'games':GAMES,'recovery':recovery_config(),'admin':bool(auth and auth['email']==os.getenv('ADMIN_EMAIL','').lower())},headers
        if path=='/api/password/forgot' and method=='POST':
            check(recovery_config(),'Password recovery is not configured yet. Please contact the app owner.',503)
            email=textval(b,'email',3,200).lower()
            check(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email),'Enter a valid email')
            reply={'ok':True,'message':'If this email belongs to an eligible account, a reset link will be sent. Check your inbox and spam folder.'}
            if not recovery_limit(d,'reset-global',now,100,3600): return reply,headers
            if not recovery_limit(d,'reset-email:'+hashlib.sha256(email.encode()).hexdigest(),now,3,3600): return reply,headers
            u=d.execute('SELECT id FROM users WHERE email=? AND banned=0 AND demo=0',(email,)).fetchone()
            if u:
                raw=secrets.token_urlsafe(32); digest=hashlib.sha256(raw.encode()).hexdigest()
                d.execute('DELETE FROM password_resets WHERE expires<=?',(now,))
                d.execute('INSERT INTO password_resets VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET token=excluded.token,expires=excluded.expires',(digest,u['id'],now+1800))
                try: send_recovery(email,raw)
                except Exception:
                    # Never log addresses, reset links, API keys or provider response bodies.
                    print('RUNBACK recovery email delivery failed; check provider configuration.',flush=True)
                    d.execute('DELETE FROM password_resets WHERE token=?',(digest,))
            return reply,headers
        if path=='/api/password/reset' and method=='POST':
            raw=textval(b,'token',20,200); password=textval(b,'password',10,128)
            digest=hashlib.sha256(raw.encode()).hexdigest()
            r=d.execute('SELECT p.user_id FROM password_resets p JOIN users u ON u.id=p.user_id WHERE p.token=? AND p.expires>? AND u.banned=0 AND u.demo=0'+(' FOR UPDATE OF u' if DATABASE_URL else ''),(digest,now)).fetchone()
            check(r,'This reset link is invalid or expired. Request a new one.',400)
            # Consume conditionally: only one concurrent request can use this token.
            consumed=d.execute('DELETE FROM password_resets WHERE token=? AND expires>?',(digest,now))
            check(consumed.rowcount==1,'This reset link is invalid or expired. Request a new one.',400)
            d.execute('UPDATE users SET password=? WHERE id=?',(pwd(password),r['user_id']))
            d.execute('DELETE FROM tokens WHERE user_id=?',(r['user_id'],))
            # Clear the old account login limiter so recovery restores access.
            u=d.execute('SELECT email FROM users WHERE id=?',(r['user_id'],)).fetchone()
            d.execute('DELETE FROM attempts WHERE key=?',(hashlib.sha256(u['email'].encode()).hexdigest(),))
            headers.append(('Set-Cookie','runback=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'))
            return {'ok':True},headers
        if path in ('/api/login','/api/register') and method=='POST':
            email=textval(b,'email',3,200).lower(); password=textval(b,'password',10,128)
            check(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email),'Enter a valid email')
            # Account-keyed limiter survives restarts; avoids trusting forwarded IP headers.
            key=hashlib.sha256(email.encode()).hexdigest(); limit=d.execute('SELECT * FROM attempts WHERE key=?',(key,)).fetchone()
            check(not limit or limit['reset_at']<=now or limit['count']<10,'Too many attempts. Try again in 15 minutes.',429)
            if limit and limit['reset_at']>now: d.execute('UPDATE attempts SET count=count+1 WHERE key=?',(key,))
            else: d.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=1,reset_at=excluded.reset_at',(key,now+900))
            # Invalid login returns normally so the attempt counter commits.
            if path.endswith('register'):
                check(b.get('adult') is True,'You must be 18 or older to join')
                p=profile(b); check(not d.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(),'Email already registered',409)
                ident=uid(); d.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,0,0,0,0,?)',(ident,email,pwd(password),*[p[k] for k in ('name','city','game','main','rank','platform','bio')],now))
            else:
                u=d.execute('SELECT * FROM users WHERE email=? AND demo=0 AND banned=0',(email,)).fetchone()
                valid=bool(u and hmac.compare_digest(pwd(password,u['password'].split(':')[0]),u['password']))
                if not valid: return {'error':'Email or password is incorrect','_status':401},headers
                ident=u['id']
            d.execute('DELETE FROM tokens WHERE expires<?',(now,)); raw=secrets.token_urlsafe(32); csrf=uid()
            d.execute('INSERT INTO tokens VALUES(?,?,?,?)',(hashlib.sha256(raw.encode()).hexdigest(),ident,csrf,now+604800))
            secure='; Secure' if e.get('HTTP_X_FORWARDED_PROTO',e.get('wsgi.url_scheme'))=='https' else ''
            headers.append(('Set-Cookie',f'runback={raw}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800{secure}'))
            return {'ok':True},headers
        check(auth,'Please sign in',401); me=auth['id']
        if path=='/api/logout':
            d.execute('DELETE FROM tokens WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),)); headers.append(('Set-Cookie','runback=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0')); return {'ok':True},headers
        if path=='/api/profile' and method=='POST':
            p=profile(b); ready=b.get('ready') is True
            d.execute('UPDATE users SET name=?,city=?,game=?,main=?,rank=?,platform=?,bio=?,ready=?,ready_until=? WHERE id=?',(*[p[k] for k in ('name','city','game','main','rank','platform','bio')],int(ready),now+7200 if ready else 0,me)); return {'ok':True},headers
        if path=='/api/discover' and method=='GET':
            q=parse_qs(e.get('QUERY_STRING','')); users=d.execute('SELECT * FROM users WHERE id<>? AND banned=0 ORDER BY created DESC LIMIT 500',(me,)).fetchall()
            users=[public(u) for u in users if not blocked(d,me,u['id']) and not d.execute('SELECT 1 FROM swipes WHERE user_id=? AND target=?',(me,u['id'])).fetchone()]
            for k in ('city','game','rank','platform'):
                if q.get(k,[''])[0]: users=[u for u in users if u[k].lower()==q[k][0].lower()]
            if q.get('ready',[''])[0]=='1': users=[u for u in users if u['ready']]
            return {'players':users},headers
        if path=='/api/swipe' and method=='POST':
            target=textval(b,'target'); kind=textval(b,'kind'); check(kind in ('pass','challenge'),'Invalid swipe')
            u=d.execute('SELECT * FROM users WHERE id=? AND banned=0',(target,)).fetchone(); check(u and target!=me and not blocked(d,me,target),'Player unavailable',404)
            check(not u['demo'] or kind=='pass','Sample profiles cannot accept challenges. Invite a real player!')
            d.execute('INSERT INTO swipes VALUES(?,?,?) ON CONFLICT(user_id,target) DO UPDATE SET kind=excluded.kind',(me,target,kind)); return {'matched':bool(matched(d,me,target))},headers
        if path=='/api/reset-discovery' and method=='POST':
            d.execute("DELETE FROM swipes WHERE user_id=? AND kind='pass'",(me,)); return {'ok':True},headers
        if path=='/api/friends' and method=='GET':
            users=d.execute('SELECT * FROM users WHERE banned=0 AND id<>? AND demo=0',(me,)).fetchall()
            friends=[]; incoming=[]
            for u in users:
                if blocked(d,me,u['id']): continue
                if matched(d,me,u['id']):
                    p=public(u); p['results']=[dict(r) for r in d.execute('SELECT * FROM results WHERE (proposer=? AND opponent=?) OR (proposer=? AND opponent=?) ORDER BY created DESC',(me,u['id'],u['id'],me)).fetchall()]; friends.append(p)
                elif d.execute("SELECT 1 FROM swipes WHERE user_id=? AND target=? AND kind='challenge'",(u['id'],me)).fetchone(): incoming.append(public(u))
            return {'friends':friends,'incoming':incoming},headers
        if path=='/api/messages':
            target=(parse_qs(e.get('QUERY_STRING','')).get('target',[''])[0] if method=='GET' else textval(b,'target'))
            check(matched(d,me,target),'Chat requires a mutual challenge',403)
            if method=='POST':
                recent=d.execute('SELECT COUNT(*) AS n FROM messages WHERE sender=? AND created>?',(me,now-60)).fetchone()['n']; check(recent<30,'Slow down a moment',429)
                d.execute('INSERT INTO messages VALUES(?,?,?,?,?)',(uid(),me,target,textval(b,'body',1,2000),now)); return {'ok':True},headers
            return {'messages':[dict(r) for r in d.execute('SELECT * FROM (SELECT * FROM messages WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?) ORDER BY created DESC,id DESC LIMIT 200) m ORDER BY created,id',(me,target,target,me)).fetchall()]},headers
        if path in ('/api/block','/api/report') and method=='POST':
            target=textval(b,'target'); check(target!=me and d.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(),'Invalid player')
            if path.endswith('block'): d.execute('INSERT INTO blocks VALUES(?,?) ON CONFLICT(user_id,target) DO NOTHING',(me,target))
            else: d.execute('INSERT INTO reports VALUES(?,?,?,?,?,?)',(uid(),me,target,textval(b,'reason',5,1000),now,'open'))
            return {'ok':True},headers
        if path=='/api/blocks' and method=='GET': return {'players':[public(u) for u in d.execute('SELECT u.* FROM users u JOIN blocks b ON b.target=u.id WHERE b.user_id=?',(me,)).fetchall()]},headers
        if path=='/api/unblock' and method=='POST': d.execute('DELETE FROM blocks WHERE user_id=? AND target=?',(me,textval(b,'target'))); return {'ok':True},headers
        if path=='/api/results' and method=='POST':
            target=textval(b,'target'); check(matched(d,me,target),'Select a friend',403)
            check(type(b.get('mine')) is int and type(b.get('theirs')) is int and 0<=b['mine']<=99 and 0<=b['theirs']<=99 and b['mine']+b['theirs']>0,'Scores must be 0–99, with at least one game played')
            d.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?)',(uid(),me,target,b['mine'],b['theirs'],'pending',now)); return {'ok':True},headers
        if path=='/api/results/confirm' and method=='POST':
            r=d.execute('SELECT * FROM results WHERE id=?',(textval(b,'id'),)).fetchone(); check(r and r['opponent']==me and r['status']=='pending' and matched(d,me,r['proposer']),'Result unavailable',403)
            check(b.get('accept') in (True,False),'Choose accept or reject'); d.execute('UPDATE results SET status=? WHERE id=?',('confirmed' if b['accept'] else 'rejected',r['id'])); return {'ok':True},headers
        if path=='/api/sessions':
            if method=='POST':
                title=textval(b,'title',3); game=textval(b,'game'); city=textval(b,'city'); mode=textval(b,'mode'); venue=textval(b,'venue',3,200); starts=textval(b,'starts')
                from datetime import datetime,timezone
                try: dt=datetime.fromisoformat(starts.replace('Z','+00:00')); check(dt.tzinfo and dt.timestamp()>now,'Choose a future date')
                except ValueError: raise APIError('Invalid session date')
                check(mode in ('Online','Local') and game in GAMES,'Invalid mode or game'); cap=b.get('capacity'); check(type(cap) is int and 2<=cap<=100,'Capacity must be 2–100')
                ident=uid(); d.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,me,title,game,city,mode,venue,starts,cap,now)); d.execute('INSERT INTO attendees VALUES(?,?)',(ident,me)); return {'ok':True},headers
            rows=[dict(r) for r in d.execute('SELECT s.*,u.name AS host_name FROM sessions s JOIN users u ON u.id=s.host WHERE u.banned=0 ORDER BY starts').fetchall() if not blocked(d,me,r['host'])]
            for r in rows: r['attendees']=[a['user_id'] for a in d.execute('SELECT user_id FROM attendees WHERE session_id=?',(r['id'],)).fetchall()]
            return {'sessions':rows},headers
        if path=='/api/sessions/join' and method=='POST':
            ident=textval(b,'id'); s=d.execute('SELECT * FROM sessions WHERE id=?'+(' FOR UPDATE' if DATABASE_URL else ''),(ident,)).fetchone(); check(s and active_player(d,s['host']) and not blocked(d,me,s['host']),'Session unavailable',404)
            from datetime import datetime
            check(b.get('leave') or datetime.fromisoformat(s['starts'].replace('Z','+00:00')).timestamp()>now,'Session has ended')
            if b.get('leave'):
                check(s['host']!=me,'Host must cancel the session'); d.execute('DELETE FROM attendees WHERE session_id=? AND user_id=?',(ident,me))
            else:
                # Conditional insert keeps capacity bounded across concurrent requests.
                d.execute('INSERT INTO attendees SELECT ?,? WHERE (SELECT COUNT(*) FROM attendees WHERE session_id=?) < ? ON CONFLICT(session_id,user_id) DO NOTHING',(ident,me,ident,s['capacity']))
                check(d.execute('SELECT 1 FROM attendees WHERE session_id=? AND user_id=?',(ident,me)).fetchone(),'Session is full',409)
            return {'ok':True},headers
        if path=='/api/sessions/delete' and method=='POST': d.execute('DELETE FROM sessions WHERE id=? AND host=?',(textval(b,'id'),me)); return {'ok':True},headers
        if path=='/api/crews':
            if method=='POST':
                game=textval(b,'game'); check(game in GAMES,'Invalid game'); ident=uid(); d.execute('INSERT INTO crews VALUES(?,?,?,?,?,?)',(ident,me,textval(b,'name',3),textval(b,'city'),game,textval(b,'description',3,500))); d.execute('INSERT INTO members VALUES(?,?)',(ident,me)); return {'ok':True},headers
            rows=[dict(r) for r in d.execute('SELECT c.*,u.name AS owner_name FROM crews c JOIN users u ON u.id=c.owner WHERE u.banned=0').fetchall() if not blocked(d,me,r['owner'])]
            for r in rows: r['members']=[a['user_id'] for a in d.execute('SELECT user_id FROM members WHERE crew_id=?',(r['id'],)).fetchall()]
            return {'crews':rows},headers
        if path=='/api/crews/join' and method=='POST':
            ident=textval(b,'id'); c=d.execute('SELECT * FROM crews WHERE id=?',(ident,)).fetchone(); check(c and active_player(d,c['owner']) and not blocked(d,me,c['owner']),'Crew unavailable',404)
            if b.get('leave'): check(c['owner']!=me,'Owner must delete the crew'); d.execute('DELETE FROM members WHERE crew_id=? AND user_id=?',(ident,me))
            else: d.execute('INSERT INTO members VALUES(?,?) ON CONFLICT(crew_id,user_id) DO NOTHING',(ident,me))
            return {'ok':True},headers
        if path=='/api/crews/delete' and method=='POST': d.execute('DELETE FROM crews WHERE id=? AND owner=?',(textval(b,'id'),me)); return {'ok':True},headers
        if path=='/api/admin':
            check(auth['email']==os.getenv('ADMIN_EMAIL','').lower(),'Administrator access required',403)
            if method=='GET': return {'reports':[dict(r) for r in d.execute('SELECT r.*,u.name AS target_name FROM reports r JOIN users u ON u.id=r.target ORDER BY created DESC').fetchall()]},headers
            ident=textval(b,'id'); r=d.execute('SELECT * FROM reports WHERE id=?',(ident,)).fetchone(); check(r,'Report unavailable',404)
            if b.get('ban'): check(r['target']!=me,'Cannot ban yourself'); d.execute('UPDATE users SET banned=1 WHERE id=?',(r['target'],)); d.execute('DELETE FROM tokens WHERE user_id=?',(r['target'],))
            d.execute("UPDATE reports SET status='closed' WHERE id=?",(ident,)); return {'ok':True},headers
        if path=='/api/account/delete' and method=='POST':
            check(hmac.compare_digest(pwd(textval(b,'password',10,128),auth['password'].split(':')[0]),auth['password']),'Incorrect password',403)
            d.execute('DELETE FROM users WHERE id=?',(me,)); headers.append(('Set-Cookie','runback=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')); return {'ok':True},headers
        raise APIError('Not found',404)
def application(e,start):
    status=200; headers=[]
    try:
        path=e.get('PATH_INFO','/')
        if path.startswith('/api/'):
            check(e['REQUEST_METHOD'] in ('GET','POST'),'Method not allowed',405)
            length=int(e.get('CONTENT_LENGTH') or 0); check(length<=16384,'Request too large',413)
            try: body=json.loads(e['wsgi.input'].read(length) or b'{}'); check(isinstance(body,dict),'Invalid JSON')
            except (ValueError,UnicodeError): raise APIError('Invalid JSON')
            result,headers=api(e,body); status=result.pop('_status',200); data=json.dumps(result).encode(); content='application/json'
        else:
            check(e['REQUEST_METHOD']=='GET','Method not allowed',405)
            files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/manifest.webmanifest':'manifest.webmanifest','/pwa.js':'pwa.js','/pwa.css':'pwa.css','/icon-192.png':'icon-192.png','/icon-512.png':'icon-512.png','/apple-touch-icon.png':'apple-touch-icon.png'}; check(path in files,'Not found',404)
            p=ROOT/'public'/files[path]; data=p.read_bytes(); content=mimetypes.guess_type(str(p))[0] or 'text/plain'
    except APIError as ex: status=ex.status; data=json.dumps({'error':ex.msg}).encode(); content='application/json'
    except Exception:
        import traceback; traceback.print_exc(); status=500; data=b'{"error":"Something went wrong. Please try again."}'; content='application/json'
    headers += [('Content-Type',content + ('; charset=utf-8' if content.startswith('text/') or content in ('application/json','application/javascript','application/manifest+json') else '')),('Content-Length',str(len(data))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','same-origin'),('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")]
    start(f'{status} '+{200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',409:'Conflict',413:'Too Large',429:'Too Many Requests',500:'Internal Server Error'}.get(status,'Error'),headers); return [data]
init()
if __name__=='__main__':
    port=int(os.getenv('PORT','3000')); print(f'RUNBACK listening on 0.0.0.0:{port}',flush=True); make_server('0.0.0.0',port,application).serve_forever()
