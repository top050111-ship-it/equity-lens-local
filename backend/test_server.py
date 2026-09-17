"""Offline bridge tests; no broker credentials or model calls required."""
import copy
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
import server
from app import demo


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_data,self.old_mode,self.old_job=server.DATA,server.MODE,server.job
        server.DATA=Path(self.temp.name)
        server.MODE='local'
        server.job=None
        self.client=TestClient(server.api,base_url='http://127.0.0.1:8000',client=('127.0.0.1',32000))

    def tearDown(self):
        server.DATA,server.MODE,server.job=self.old_data,self.old_mode,self.old_job
        self.temp.cleanup()

    def post(self,path,**kwargs):
        return self.client.post(path,headers={'Origin':'http://127.0.0.1:8000'},**kwargs)

    def test_empty_does_not_fall_back_to_demo(self):
        r=self.client.get('/api/workspace')
        self.assertEqual(r.status_code,200)
        self.assertIsNone(r.json()['snapshot'])

    def test_compiled_ui_and_deep_routes(self):
        import re
        for path in ('/','/dashboard','/connect','/project'):
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn('Equity Lens',response.text)
        html=self.client.get('/').text
        for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"',html):
            self.assertEqual(self.client.get(asset).status_code,200)

    def test_python_output_and_partial_report(self):
        demo(server.DATA)
        r=self.client.get('/api/workspace')
        self.assertEqual(r.status_code,200)
        packet=r.json()
        self.assertEqual(len(packet['snapshot']['positions']),2)
        self.assertAlmostEqual(sum(p['weight_pct'] for p in packet['snapshot']['positions']),100)
        packet['report']['complete']=False
        packet['report']['macro']['status']='failed'
        result=self.post('/api/workspace',json={'report':packet['report']})
        self.assertEqual(result.status_code,200)
        self.assertFalse(self.client.get('/api/workspace').json()['report']['complete'])

    def test_public_rejects_real_data_and_actions(self):
        demo(server.DATA)
        server.MODE='public'
        for method,path in [('get','/api/workspace'),('post','/api/workspace'),('post','/api/jobs')]:
            self.assertEqual(getattr(self.client,method)(path).status_code,403)
        self.assertEqual(self.client.get('/api/config').json()['mode'],'public')

    def test_cross_origin_and_host_blocked(self):
        self.assertEqual(self.client.post('/api/jobs',headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/workspace',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/workspace',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        remote=TestClient(server.api,base_url='http://127.0.0.1:8000',client=('192.0.2.1',3000))
        self.assertEqual(remote.get('/api/workspace').status_code,403)

    def test_no_private_file_serving(self):
        for path in ('/.env','/data/latest_portfolio.json','/server.py','/api/unknown','/assets/../server.py'):
            self.assertEqual(self.client.get(path).status_code,404)

    def test_wrong_status_file_and_bad_source(self):
        self.assertEqual(self.post('/api/workspace',json={'report':'some.md'}).status_code,422)
        self.assertEqual(self.post('/api/workspace',json=[]).status_code,422)
        demo(server.DATA)
        r=self.client.get('/api/workspace').json()['report']
        r['sources'][0]['url']='javascript:alert(1)'
        self.assertEqual(self.post('/api/workspace',json={'report':r}).status_code,422)

    def test_double_job_guard(self):
        server.job={'status':'running'}
        self.assertEqual(self.post('/api/jobs').status_code,409)


if __name__=='__main__':
    unittest.main()
