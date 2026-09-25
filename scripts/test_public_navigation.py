#!/usr/bin/env python3
"""Public browser workflows use real assets and controlled HTTP responses."""
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]/'frontend/public'
BASE='https://public.test'


class PublicNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True,args=['--no-proxy-server'])
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()
    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1100,'height':900})
        self.context.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{window.copied=value}}})")
        self.page=self.context.new_page();self.errors=[];self.failed_page=False;self.failed_search=False
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.route(BASE+'/**',self.route)
    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])
    def document(self,path):
        zh=path.startswith('/zh');prefix='/zh' if zh else '';route=path[3:] or '/' if zh else path
        if route=='/':main='<h1>Home</h1><a data-link href="/category/ai">AI category</a><a data-link href="/article/one">Article one</a>'
        elif route=='/category/ai':main='<h1>AI research</h1><div class="category-count">3</div><div class="category-articles">'+''.join(f'<a data-link href="/article/{slug}">{slug}</a>' for slug in ['one','two','three'])+'</div>'
        elif route.startswith('/article/') and route!='/article/missing':
            slug=route.rsplit('/',1)[-1];machine=BASE+'/agent/v1/articles/'+slug
            main=f'<article class="article-page"><h1>Article {slug}</h1><div class="article-content"><pre><code>print("原文 &lt;code&gt;")</code></pre></div><a href="{machine}?lang={"zh" if zh else "en"}">Agent JSON</a><button data-machine-url="{machine}?lang={"zh" if zh else "en"}">Copy A</button><button data-machine-url="{machine}/paid?lang={"zh" if zh else "en"}">Copy B</button><a href="/feed.xml" data-rss>RSS</a><a data-link href="/category/ai">AI category</a></article>'
        elif route=='/about':main='<h1>About</h1>'
        else:main='<section class="not-found"><h1>Not found</h1></section>'
        meta='noindex,nofollow' if route=='/article/missing' else 'index,follow'
        schema={'@context':'https://schema.org','@type':'CollectionPage' if '/category/' in route else 'WebPage','url':BASE+path,'inLanguage':'zh-CN' if zh else 'en'}
        return f'''<!doctype html><html lang="{'zh-CN' if zh else 'en'}"><head><meta charset="utf-8"><title>{route} Research</title><link rel="stylesheet" href="/styles.css"><meta name="description" content="A complete research page with original evidence"><meta name="robots" content="{meta}"><link rel="canonical" href="{BASE+path}"><meta property="og:url" content="{BASE+path}">
<link rel="alternate" hreflang="en" href="{BASE+route}"><link rel="alternate" hreflang="zh-CN" href="{BASE+'/zh'+route}"><link rel="alternate" hreflang="x-default" href="{BASE+route}"><script data-page-schema type="application/ld+json">{json.dumps(schema)}</script></head>
<body data-ssr="true"><header><nav id="primaryNav"><a data-link href="/category/ai">AI</a><a data-link href="/about">About</a></nav><div class="header-actions"><a data-language-switch href="{'/zh'+route if not zh else route}">中文</a><button id="searchButton">Search</button><button id="menuButton">Menu</button></div></header>
<main id="app" tabindex="-1">{main}</main><div id="searchOverlay" class="search-overlay" aria-hidden="true"><input id="searchInput"><button id="closeSearch">Close</button><div id="searchPrompt"><button data-search-term="Agent">Agent</button></div><div id="searchResults"></div></div><div id="toast"></div><script src="/locales.js"></script><script src="/i18n.js"></script><script src="/growth.js"></script><script src="/app.js"></script></body></html>'''
    def route(self,route):
        parsed=urlsplit(route.request.url);path=parsed.path;file=ROOT/path.lstrip('/')
        if path in ['/app.js','/locales.js','/i18n.js','/growth.js','/styles.css']:
            route.fulfill(path=file,content_type='text/css' if path.endswith('.css') else 'text/javascript');return
        if path.startswith('/api/'):
            if path=='/api/v1/search':
                if self.failed_search:route.fulfill(status=503,body='{}');return
                query=parse_qs(parsed.query).get('q',[''])[0]
                items=[] if query=='absent' else [{'slug':'one','title':'<img src=x onerror="alert(1)">'+query,'category':{'name':'AI'},'readMinutes':5}]
            elif path=='/api/v1/categories':items=[{'slug':'ai','name':'AI','eyebrow':'AI','description':'Research','articleCount':3}]
            else:items=[]
            route.fulfill(json=items);return
        if path.endswith('/legacy'):
            route.fulfill(status=301,headers={'Location':path.replace('/legacy','/one')+('?' + parsed.query if parsed.query else '')});return
        if '/article/' in path and self.failed_page:route.fulfill(status=503,body='temporarily unavailable');return
        route.fulfill(status=404 if path.endswith('/missing') else 200,body=self.document(path),content_type='text/html')
    def test_category_navigation_is_complete_in_both_languages_with_matching_metadata(self):
        for prefix in ['', '/zh']:
            with self.subTest(prefix=prefix):
                self.page.goto(BASE+prefix+'/',wait_until='domcontentloaded')
                self.page.locator('#app a[href$="/category/ai"]').click()
                expect(self.page.locator('.category-articles a')).to_have_count(3)
                expect(self.page.locator('#primaryNav a.active')).to_have_attribute('href',prefix+'/category/ai')
                expect(self.page.locator('link[rel=canonical]')).to_have_attribute('href',BASE+prefix+'/category/ai')
                self.assertEqual(self.page.locator('script[data-page-schema]').count(),1)
                self.page.go_back();expect(self.page.locator('#app h1')).to_have_text('Home')
                self.page.go_forward();expect(self.page.locator('.category-articles a')).to_have_count(3)
    def test_article_code_rss_clipboard_and_language_switch(self):
        self.page.goto(BASE+'/zh/article/one',wait_until='domcontentloaded')
        expect(self.page.locator('[data-rss]')).to_have_attribute('href','/zh/feed.xml')
        self.page.locator('[data-machine-url]').last.click()
        self.page.wait_for_function("window.copied?.endsWith('/paid?lang=zh')")
        expect(self.page.locator('code')).to_have_text('print("原文 <code>")')
        self.page.locator('[data-language-switch]').click();expect(self.page.locator('html')).to_have_attribute('lang','en')
        expect(self.page.locator('[data-rss]')).to_have_attribute('href','/feed.xml')
    def test_legacy_navigation_keeps_chinese_query_and_hash(self):
        self.page.goto(BASE+'/zh/')
        # Playwright route interception only handles the first redirect request.
        # Real HTTP 301 destinations are checked in test_publication_protection.
        self.page.evaluate("""() => {
          const original=fetch;
          window.fetch=async (url,options)=>{
            if (!String(url).includes('/legacy')) return original(url,options);
            const destination=String(url).replace('/legacy','/one');
            const response=await original(destination,options);
            Object.defineProperty(response,'redirected',{value:true});
            return response;
          };
        }""")
        self.page.evaluate("navigate('/zh/article/legacy?utm_source=test#evidence')")
        self.page.wait_for_url(BASE+'/zh/article/one?utm_source=test#evidence')
        expect(self.page.locator('#app h1')).to_have_text('Article one')
    def test_failed_load_is_retryable_and_not_a_false_404(self):
        self.page.goto(BASE+'/');self.failed_page=True
        self.page.locator('#app a[href$="/article/one"]').click()
        expect(self.page.locator('#app [role=alert]')).to_contain_text('temporarily unavailable')
        self.assertEqual(self.page.locator('meta[name=robots]').get_attribute('content'),'index,follow')
        self.failed_page=False;self.page.locator('[data-retry-page]').click()
        expect(self.page.locator('#app h1')).to_have_text('Article one')
    def test_real_not_found_and_trust_page_navigation(self):
        self.page.goto(BASE+'/');self.page.evaluate("navigate('/article/missing')")
        expect(self.page.locator('#app h1')).to_have_text('Not found')
        expect(self.page.locator('meta[name=robots]')).to_have_attribute('content','noindex,nofollow')
        self.page.locator('#primaryNav a[href="/about"]').click();expect(self.page.locator('#app h1')).to_have_text('About')
    def test_newer_navigation_wins_when_an_old_request_is_slow(self):
        self.page.goto(BASE+'/')
        self.page.evaluate("""() => { const original=window.fetch; window.fetch=(url,options)=>String(url).includes('/slow') ? new Promise(resolve=>setTimeout(resolve,400)).then(()=>original(url,options)) : original(url,options); navigate('/article/slow'); navigate('/category/ai'); }""")
        expect(self.page.locator('#app h1')).to_have_text('AI research');self.page.wait_for_timeout(500)
        expect(self.page.locator('#app h1')).to_have_text('AI research')
    def test_search_errors_empty_results_safe_text_and_accessible_close(self):
        self.page.goto(BASE+'/');self.page.locator('#searchButton').click()
        expect(self.page.locator('#searchOverlay')).to_have_attribute('aria-hidden','false')
        self.failed_search=True;self.page.evaluate("runSearch('Agent')")
        expect(self.page.locator('#searchResults [role=alert]')).to_be_visible()
        self.failed_search=False;self.page.evaluate("runSearch('absent')")
        expect(self.page.locator('#searchResults')).to_contain_text('No matching')
        self.page.evaluate("runSearch('Agent')");expect(self.page.locator('.search-result')).to_have_count(1)
        self.assertEqual(self.page.locator('#searchResults img').count(),0)
        self.page.locator('.search-result').click();expect(self.page.locator('#app h1')).to_have_text('Article one')
        expect(self.page.locator('#searchOverlay')).to_have_attribute('aria-hidden','true')
    def test_latest_search_wins_and_clipboard_failure_is_visible(self):
        self.page.goto(BASE+'/article/one')
        self.page.evaluate("""() => {const original=fetch;window.fetch=(url,opts)=>String(url).includes('q=slow')?new Promise(resolve=>setTimeout(resolve,300)).then(()=>original(url,opts)):original(url,opts);runSearch('slow');runSearch('Agent');}""")
        expect(self.page.locator('#searchResults')).to_contain_text('Agent');self.page.wait_for_timeout(400)
        self.assertNotIn('slow',self.page.locator('#searchResults').inner_text())
        self.page.evaluate("() => { navigator.clipboard.writeText=async()=>{throw new Error('denied')}; }")
        self.page.locator('[data-machine-url]').first.click();expect(self.page.locator('#toast')).to_contain_text('Could not copy')
    def test_clearing_a_pending_search_resets_busy_state_and_ignores_old_results(self):
        self.page.goto(BASE+'/')
        self.page.evaluate("""() => {
          const original=fetch;
          window.fetch=(url,opts)=>new Promise(resolve=>setTimeout(resolve,100)).then(()=>original(url,opts));
          runSearch('Agent');runSearch(' ');
        }""")
        self.page.wait_for_timeout(200)
        expect(self.page.locator('#searchResults')).to_be_empty()
        self.assertIsNone(self.page.locator('#searchResults').get_attribute('aria-busy'))

if __name__=='__main__':unittest.main()
