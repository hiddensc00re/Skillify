import concurrent.futures
import io
import tempfile
import threading
import unittest
from pathlib import Path
from store import Store, AppError
from web import create_app

class HiringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=100000.0
        self.store=Store(Path(self.temp.name)/'qa.db',clock=lambda:self.now,pause_seconds=60)
        self.app=create_app({'TESTING':True,'ADMIN_PASSWORD':'test-admin-'+ 'a'*32},store=self.store)
        self.counter=0

    def client(self,role='candidate'):
        self.counter+=1;c=self.app.test_client();csrf=c.get('/api/bootstrap').json['csrf']
        r=c.post('/api/auth/register',json=dict(name='QA '+str(self.counter),email=f'qa{self.counter}@example.test',password='test-password-123456',role=role),headers={'X-CSRF-Token':csrf})
        self.assertEqual(200,r.status_code)
        return c,r.json['csrf'],r.json['account']

    def post(self,c,csrf,path,data=None,status=200):
        r=c.post(path,json={} if data is None else data,headers={'X-CSRF-Token':csrf})
        self.assertEqual(status,r.status_code,(path,r.json));return r.json

    def job(self,c,csrf):
        return self.post(c,csrf,'/api/jobs',dict(title='Python Developer',company='QA',location='Roma',mode='Remoto',category='Engineering',salary='€ 30000',description='Backend',contract='Indeterminato',tags='Python',test_brief='Realizza una piccola API e spiega come hai verificato il risultato.'))['id']

    def reserve(self,c,csrf,jid):return self.post(c,csrf,f'/api/jobs/{jid}/apply')['id']
    def confirm(self,c,csrf,rid):return self.post(c,csrf,f'/api/reservations/{rid}/confirm',dict(motivation='Intendo svolgere questo test per mostrare come progetto le API.',availability='Oggi pomeriggio',commitment=True))
    def ids(self,c):return [r['id'] for r in c.get('/api/jobs').json['jobs']]

    def test_reservation_expiry_and_confirmation_does_not_extend_timer(self):
        e,es,_=self.client('employer');c,cs,_=self.client();other,_,_=self.client();jid=self.job(e,es)
        rid=self.reserve(c,cs,jid);self.assertNotIn(jid,self.ids(other))
        self.now+=30;self.confirm(c,cs,rid)
        self.now+=31
        self.assertIn(jid,self.ids(other))
        self.assertEqual('expired',c.get('/api/applications').json['applications'][0]['status'])
        self.post(c,cs,f'/api/reservations/{rid}/start',status=409)
        newrid=self.reserve(c,cs,jid);self.assertNotEqual(rid,newrid)

    def test_started_test_does_not_expire_and_hired_never_reopens(self):
        e,es,_=self.client('employer');c,cs,_=self.client();o,_,_=self.client();jid=self.job(e,es);rid=self.reserve(c,cs,jid)
        self.post(c,cs,f'/api/reservations/{rid}/start',status=409)
        self.post(c,cs,f'/api/reservations/{rid}/confirm',dict(motivation='Breve',availability='Ora',commitment=False),status=400)
        self.confirm(c,cs,rid);self.post(c,cs,f'/api/reservations/{rid}/start')
        self.now+=200000;self.assertNotIn(jid,self.ids(o))
        self.post(e,es,f'/api/jobs/{jid}/state',{'action':'select'},status=409)
        self.post(c,cs,f'/api/reservations/{rid}/submit',{'response':'Soluzione della prova con spiegazioni dettagliate e casi verificati.'})
        self.post(e,es,f'/api/jobs/{jid}/state',{'action':'select','note':'Test superato.'})
        self.now+=200000;self.assertNotIn(jid,self.ids(o))
        self.post(e,es,f'/api/jobs/{jid}/state',{'action':'reopen'},status=409)
        self.assertEqual('hired',c.get('/api/applications').json['applications'][0]['status'])

    def test_failed_test_republishes_and_candidate_history_persists(self):
        e,es,_=self.client('employer');c,cs,_=self.client();jid=self.job(e,es);rid=self.reserve(c,cs,jid)
        self.confirm(c,cs,rid);self.post(c,cs,f'/api/reservations/{rid}/start')
        self.post(c,cs,f'/api/reservations/{rid}/submit',{'response':'Una consegna completa da sottoporre alla valutazione del team.'})
        self.post(e,es,f'/api/jobs/{jid}/state',{'action':'reopen','note':'Manca il caso limite.'})
        self.assertIn(jid,self.ids(c))
        r=c.get('/api/applications').json['applications'][0];self.assertEqual('failed',r['status']);self.assertEqual('Manca il caso limite.',r['review_note'])
        restored=Store(Path(self.temp.name)/'qa.db',clock=lambda:self.now)
        self.assertEqual('failed',restored.candidate_reservations(r['candidate_id'])[0]['status'])

    def test_role_and_company_isolation_and_private_attachments(self):
        e,es,_=self.client('employer');e2,e2s,_=self.client('employer');c,cs,_=self.client();c2,c2s,_=self.client();jid=self.job(e,es)
        self.assertEqual([],e2.get('/api/dashboard').json['jobs'])
        self.post(c,cs,'/api/jobs',{},status=401)
        self.post(e,es,f'/api/jobs/{jid}/apply',status=401)
        self.post(e2,e2s,f'/api/jobs/{jid}/state',{'action':'close'},status=404)
        upload=e.post(f'/api/jobs/{jid}/files',data={'file':(io.BytesIO(b'read me'),'brief.txt')},headers={'X-CSRF-Token':es})
        self.assertEqual(200,upload.status_code);fid=upload.json['id']
        self.assertEqual(404,c2.get(f'/api/files/{fid}').status_code)
        rid=self.reserve(c,cs,jid);self.confirm(c,cs,rid);self.post(c,cs,f'/api/reservations/{rid}/start')
        download=c.get(f'/api/files/{fid}');self.assertEqual(b'read me',download.data);self.assertIn('attachment',download.headers['Content-Disposition'])
        upload=c.post(f'/api/reservations/{rid}/files',data={'file':(io.BytesIO(b'solution'),'solution.txt')},headers={'X-CSRF-Token':cs})
        self.assertEqual(200,upload.status_code);sfid=upload.json['id']
        self.assertEqual(200,e.get(f'/api/files/{sfid}').status_code)
        self.assertEqual(404,e2.get(f'/api/files/{sfid}').status_code)
        self.assertEqual(404,c2.get(f'/api/files/{sfid}').status_code)
        self.post(c2,c2s,f'/api/reservations/{rid}/submit',{'response':'x'*40},status=404)
        self.post(c,cs,f'/api/reservations/{rid}/submit',{'response':''})
        upload=c.post(f'/api/reservations/{rid}/files',data={'file':(io.BytesIO(b'late'),'late.txt')},headers={'X-CSRF-Token':cs})
        self.assertEqual(409,upload.status_code)

    def test_accounts_survive_login_in_another_browser_and_csrf_rotates(self):
        c,csrf,account=self.client();e,es,_=self.client('employer');jid=self.job(e,es);rid=self.reserve(c,csrf,jid)
        other=self.app.test_client();old=other.get('/api/bootstrap').json['csrf']
        self.post(other,old,'/api/auth/login',{'email':account['email'],'password':'test-password-123456','role':'employer'},status=401)
        login=self.post(other,old,'/api/auth/login',{'email':account['email'],'password':'test-password-123456','role':'candidate'})
        self.assertNotEqual(old,login['csrf']);self.assertEqual(rid,other.get('/api/applications').json['applications'][0]['id'])
        self.post(other,old,'/api/profile',{},status=403)
        logout=self.post(other,login['csrf'],'/api/auth/logout')
        self.assertEqual(401,other.get('/api/applications').status_code)
        self.assertIsNone(logout['account'])
        with self.store.connect() as db:self.assertNotIn('test-password',db.execute('SELECT password_hash FROM accounts WHERE id=?',(account['id'],)).fetchone()['password_hash'])

    def test_file_limits_and_concurrent_first_swipe(self):
        e,es,_=self.client('employer');c,_,a=self.client();o,_,b=self.client();jid=self.job(e,es)
        upload=e.post(f'/api/jobs/{jid}/files',data={'file':(io.BytesIO(b'x'*(2*1024*1024+1)),'large.txt')},headers={'X-CSRF-Token':es})
        self.assertEqual(400,upload.status_code)
        barrier=threading.Barrier(2)
        def reserve(cid):
            barrier.wait()
            try:self.store.reserve(cid,jid);return 200
            except AppError as error:return error.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:self.assertCountEqual([200,409],list(pool.map(reserve,[a['candidate_id'],b['candidate_id']])))
        self.assertNotIn(jid,self.ids(c))

if __name__=='__main__':unittest.main()
