"""Isolated integrity tests plus a real stdio MCP integration test."""
import asyncio,copy,json,os,sys,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from store import Store,Capture,Conflict
ROOT=Path(__file__).resolve().parent.parent
SAMPLE=json.loads((ROOT/'assets/sample-reading.json').read_text())
class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'reading.sqlite3';self.s=Store(self.path)
    def tearDown(self):self.tmp.cleanup()
    def seed(self):return self.s.capture(Capture.model_validate(SAMPLE))
    def test_restart_persists_graph_and_sources(self):
        self.seed();view=Store(self.path).view();self.assertEqual(view['revision'],1);self.assertEqual(len(view['nodes']),6);self.assertEqual(len(view['edges']),5)
        self.assertIn('收音机',view['nodes'][1]['quote']);self.assertEqual(view['nodes'][3]['status'],'accepted');self.assertEqual(view['nodes'][5]['status'],'proposed')
    def test_same_request_retry_and_collision(self):
        a=self.seed();b=self.seed();self.assertTrue(b['replayed']);self.assertEqual(a['change_id'],b['change_id']);self.assertEqual(b['graph']['revision'],1)
        change=copy.deepcopy(SAMPLE);change['nodes'][0]['body']='another payload'
        with self.assertRaises(Conflict):self.s.capture(Capture.model_validate(change))
    def test_stale_write_rejected(self):
        self.seed();c=copy.deepcopy(SAMPLE);c['request_id']='stale'
        with self.assertRaises(Conflict):self.s.capture(Capture.model_validate(c))
        self.assertEqual(self.s.view()['revision'],1)
    def test_invalid_edge_rolls_back_entire_batch(self):
        c=copy.deepcopy(SAMPLE);c['edges'][0]['target']='missing'
        with self.assertRaises(ValueError):self.s.capture(Capture.model_validate(c))
        self.assertEqual(self.s.view()['nodes'],[]);self.assertEqual(self.s.view()['revision'],0)
    def test_edit_retains_quote_history_and_undo(self):
        a=self.seed();old=self.s.view()['nodes'][1];r=self.s.revise(old['id'],'新标题','新的整理','open','edit',1)
        self.assertEqual(r['graph']['nodes'][1]['quote'],old['quote']);self.assertEqual(r['graph']['nodes'][1]['sources'],old['sources'])
        u=self.s.undo(r['change_id'],'undo',2);self.assertEqual(u['graph']['nodes'][1]['title'],old['title']);self.assertEqual(u['graph']['revision'],3)
    def test_archive_hides_links_reversible(self):
        self.seed();r=self.s.archive('radio-family-analogy','archive',1);self.assertEqual(len(r['graph']['nodes']),5)
        self.assertFalse(any(e['source']=='radio-family-analogy' or e['target']=='radio-family-analogy' for e in r['graph']['edges']))
        self.assertEqual(len(self.s.view(True)['nodes']),6)
        self.s.undo(r['change_id'],'undo-archive',2);self.assertEqual(len(self.s.view()['edges']),5)
    def test_noop_and_duplicate_nodes(self):
        self.seed();c=copy.deepcopy(SAMPLE);c.update(request_id='same-content',expected_revision=1)
        self.assertFalse(self.s.capture(Capture.model_validate(c))['changed'])
        c['nodes'].append(c['nodes'][0])
        with self.assertRaises(ValueError):self.s.capture(Capture.model_validate(c))
    def test_search_chinese_and_unrelated(self):
        self.seed();r=self.s.search('材质 身份');self.assertTrue(r['nodes']);self.assertEqual(self.s.search('量子隧穿')['nodes'],[])
    def test_ai_cannot_be_saved_as_verified(self):
        c=copy.deepcopy(SAMPLE);c['nodes'][2]['status']='verified'
        with self.assertRaises(ValueError):Capture.model_validate(c)
    def test_concurrent_revision_only_one_wins(self):
        def save(i):
            c=copy.deepcopy(SAMPLE);c['request_id']=f'parallel-{i}'
            try:Store(self.path).capture(Capture.model_validate(c));return True
            except Conflict:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,range(2)))
        self.assertEqual(sum(results),1);self.assertEqual(self.s.view()['revision'],1)
    def test_nonlatest_undo_does_not_overwrite(self):
        r=self.seed();self.s.archive('objects-atmosphere','archive-new',1)
        with self.assertRaises(Conflict):self.s.undo(r['change_id'],'bad-undo',2)
        self.assertEqual(self.s.view()['revision'],2)
    def test_source_url_scheme_rejected(self):
        c=copy.deepcopy(SAMPLE);c['source']['url']='javascript:alert(1)'
        with self.assertRaises(ValueError):Capture.model_validate(c)

async def mcp_roundtrip():
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    with tempfile.TemporaryDirectory() as tmp:
        env={**os.environ,'READING_BETWEEN_DATA_DIR':tmp}
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'scripts/server.py')],env=env)
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools=(await session.list_tools()).tools
                assert len(tools)==6,len(tools)
                meta=next(t for t in tools if t.name=='get_knowledge_map').meta
                assert meta['ui']['resourceUri']=='ui://reading-between/map.html'
                blank=await session.call_tool('get_knowledge_map',{})
                assert blank.structuredContent['graph']['revision']==0
                saved=await session.call_tool('capture_reading_batch',SAMPLE)
                assert not saved.isError,saved
                assert saved.structuredContent['graph']['revision']==1
                retry=await session.call_tool('capture_reading_batch',SAMPLE)
                assert retry.structuredContent['replayed']
                found=await session.call_tool('find_related',{'query':'材质 身份'})
                assert found.structuredContent['nodes']
                ui=await session.read_resource('ui://reading-between/map.html')
                assert ui.contents[0].mimeType=='text/html;profile=mcp-app'
                assert 'ui/initialize' in ui.contents[0].text
                assert 'capture_reading_batch' not in ui.contents[0].text # UI does not fabricate AI capture
                stale=await session.call_tool('archive_reading_node',{'node_id':'objects-atmosphere','request_id':'mcp-stale','expected_revision':0})
                assert stale.isError
        # Start a NEW MCP process over the SAME database to prove process-independent persistence.
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize();reloaded=await session.call_tool('get_knowledge_map',{})
                assert len(reloaded.structuredContent['graph']['nodes'])==6
                assert reloaded.structuredContent['graph']['revision']==1
    print('MCP integration passed: discover, initialize, save, retry, query, UI resource, conflict, new process reload.')
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PersistenceTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():sys.exit(1)
    asyncio.run(mcp_roundtrip())
