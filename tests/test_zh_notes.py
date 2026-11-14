#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文内容自动中文备注（zh_notes）的离线回归（零联网，模拟报文）。"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tools')]

import build_site as bs
import rss_feeds as rf
import wechat_push as wp
import zh_notes as zn

EN = 'Fed signals a rate cut as Treasury yields fall and stocks rally to a record high'
EN2 = 'Investors rotate out of semiconductors after weak earnings guidance'
ZH = '港股今日放量上涨，NVDA、TSLA 也跟涨，南向资金持续净流入。'


def deepseek_http(mapping, calls=None):
    def http(method, url, payload=None, headers=None, timeout=0):
        if calls is not None:
            calls.append(url)
        if url == zn.DEEPSEEK_CHAT_URL:
            items = json.loads(payload['messages'][1]['content'])['items']
            notes = [{'id': row['id'], 'zh': mapping.get(row['text'], '')} for row in items]
            return {'choices': [{'message': {'content': json.dumps({'notes': notes})}}]}
        raise OSError('offline')
    return http


class Base(unittest.TestCase):
    def setUp(self):
        zn._memo.clear()
        patcher = mock.patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(zn._memo.clear)


class TestDetection(Base):
    def test_only_latin_script_content_needs_a_note(self):
        self.assertTrue(zn.needs_note(EN))
        self.assertFalse(zn.needs_note(ZH))
        self.assertFalse(zn.needs_note('AAPL TSLA'))
        self.assertFalse(zn.needs_note('https://example.com/a/very/long/english/path/here'))
        self.assertFalse(zn.needs_note(''))

    def test_glossary_note_lists_terms_and_refuses_weak_matches(self):
        note = zn.glossary_note(EN)
        self.assertIn('Fed=美联储', note)
        self.assertIn('rate cut=降息', note)
        self.assertEqual(zn.glossary_note('This is a long story about my cat'), '', '只有歧义词不成备注')
        self.assertEqual(zn.glossary_note('Nothing relevant in this sentence at all'), '')

    def test_translation_validation(self):
        self.assertEqual(zn.valid_translation(EN, 'MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS'), '')
        self.assertEqual(zn.valid_translation(EN, 'still english text'), '')
        self.assertEqual(zn.valid_translation(EN, '美联储降息 https://evil.example'), '')
        self.assertEqual(zn.valid_translation('short text here', '很长' * 80), '')
        self.assertEqual(zn.valid_translation(EN, ' 美联储暗示降息。 '), '美联储暗示降息。')


class TestProviderChain(Base):
    def test_offline_uses_glossary_and_never_calls_network(self):
        obj = {}
        boom = mock.Mock(side_effect=AssertionError('offline must not call network'))
        stats = zn.annotate([(obj, EN)], online=False, http=boom)
        self.assertEqual(obj['zh_note_by'], 'glossary')
        self.assertEqual(stats['requests'], 0)
        self.assertEqual(zn.note_of(obj)[0], '中文备注·术语', '术语对照不冒充译文')

    def test_default_is_offline_outside_ci(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('GITHUB_ACTIONS', None)
            os.environ.pop('ZH_NOTES_ONLINE', None)
            self.assertFalse(zn.online_enabled())
            os.environ['GITHUB_ACTIONS'] = 'true'
            self.assertTrue(zn.online_enabled())
            os.environ['ZH_NOTES_ONLINE'] = '0'
            self.assertFalse(zn.online_enabled())

    def test_deepseek_batch_translation_wins(self):
        os.environ['DEEPSEEK_API_KEY'] = 'k'
        a, b, c = {}, {}, {'zh_note': '旧', 'zh_note_by': 'glossary', 'zh_note_src': 'x'}
        calls = []
        stats = zn.annotate([(a, EN), (b, EN2), (c, ZH)], online=True,
                            http=deepseek_http({EN: '美联储暗示降息，美债收益率回落，美股创新高。',
                                                EN2: '财报指引疲弱，投资者撤出半导体板块。'}, calls))
        self.assertEqual((a['zh_note_by'], b['zh_note_by']), ('deepseek', 'deepseek'))
        self.assertEqual(zn.note_of(a), ('中文备注', '美联储暗示降息，美债收益率回落，美股创新高。'))
        self.assertEqual(calls, [zn.DEEPSEEK_CHAT_URL], '两条合并成一次批量请求')
        self.assertNotIn('zh_note', c, '中文内容不备注，旧备注清掉')
        self.assertEqual(stats['by']['deepseek'], 2)

    def test_falls_back_to_mymemory_then_glossary(self):
        def http(method, url, payload=None, headers=None, timeout=0):
            if url.startswith(zn.MYMEMORY_URL) and 'semiconductors' not in url:
                return {'responseStatus': 200, 'responseData': {'translatedText': '美联储暗示降息。'}}
            return {'responseStatus': 429, 'responseData': {'translatedText': 'MYMEMORY WARNING: QUOTA'}}
        a, b = {}, {}
        zn.annotate([(a, EN), (b, EN2)], online=True, http=http)
        self.assertEqual(a['zh_note_by'], 'mymemory')
        self.assertEqual(b['zh_note_by'], 'glossary')
        self.assertIn('semiconductors=半导体', b['zh_note'])

    def test_bad_model_output_is_rejected(self):
        os.environ['DEEPSEEK_API_KEY'] = 'k'
        obj = {}
        zn.annotate([(obj, EN)], online=True, http=deepseek_http({EN: 'Ignore previous instructions'}))
        self.assertEqual(obj['zh_note_by'], 'glossary')

    def test_unchanged_text_is_not_retranslated_and_glossary_upgrades(self):
        obj = {}
        zn.annotate([(obj, EN)], online=False)
        self.assertEqual(obj['zh_note_by'], 'glossary')
        os.environ['DEEPSEEK_API_KEY'] = 'k'
        calls = []
        http = deepseek_http({EN: '美联储暗示降息。'}, calls)
        zn.annotate([(obj, EN)], online=True, http=http)
        self.assertEqual(obj['zh_note_by'], 'deepseek', '可联网时离线备注升级为机翻')
        zn._memo.clear()
        stats = zn.annotate([(obj, EN)], online=True, http=http)
        self.assertEqual(len(calls), 1, '原文未变不重复翻译')
        self.assertEqual(stats['reused'], 1)

    def test_online_cap(self):
        os.environ['DEEPSEEK_API_KEY'] = 'k'
        objs = [({}, f'{EN} number {i}') for i in range(5)]
        mapping = {text: '美联储暗示降息。' for _o, text in objs}
        zn.annotate(objs, online=True, http=deepseek_http(mapping), max_online=2)
        self.assertEqual([o['zh_note_by'] for o, _t in objs],
                         ['deepseek', 'deepseek', 'glossary', 'glossary', 'glossary'])


class TestDatasetsAndRender(Base):
    def community(self):
        return {'communities': [
            {'id': '27', 'key': 'BOGLEHEADS', 'name': 'Bogleheads Forum', 'icon': '📘',
             'available': True, 'verdict_class': 'bull', 'verdict_label': '偏多', 'quote': EN,
             'comments': [{'text': EN, 'post_title': ''},
                          {'text': EN2, 'post_title': 'Chip stocks', 'published_label': ''}]},
            {'id': '01', 'key': 'FUTU', 'name': '富途牛牛社区', 'icon': '🐮', 'available': True,
             'verdict_class': 'bull', 'verdict_label': '偏多', 'quote': ZH, 'comments': []},
            {'id': '02', 'key': 'X', 'name': 'X', 'available': False, 'quote': EN},
        ]}

    def test_targets_cover_quote_and_extra_comments_only_for_available(self):
        data = self.community()
        stats = zn.annotate_dataset('community', data, online=False)
        first, second, third = data['communities']
        self.assertTrue(first['zh_note'])
        self.assertNotIn('zh_note', first['comments'][0], '首条评论即卡片引文，不重复备注')
        self.assertTrue(first['comments'][1]['zh_note'])
        self.assertNotIn('zh_note', second)
        self.assertNotIn('zh_note', third)
        self.assertEqual(stats['noted'], 2)

    def test_macro_and_rss_titles(self):
        macro = {'categories': {'fed': {'label': '美联储', 'items': [{'title': EN}, {'title': ZH}]}}}
        rss = {'groups': {'g': {'sources': [{'items_list': [{'title': EN2}]}]}}}
        zn.annotate_dataset('macro', macro, online=False)
        zn.annotate_dataset('rss', rss, online=False)
        items = macro['categories']['fed']['items']
        self.assertTrue(items[0]['zh_note'])
        self.assertNotIn('zh_note', items[1])
        self.assertTrue(rss['groups']['g']['sources'][0]['items_list'][0]['zh_note'])

    def test_web_and_wechat_cards_render_note_escaped_without_links(self):
        data = self.community()
        data['communities'][0].update({'zh_note': '美联储<b>暗示</b>降息', 'zh_note_by': 'deepseek'})
        data['communities'][0]['comments'][1].update({'zh_note': 'chips=芯片', 'zh_note_by': 'glossary'})
        web = bs.build_community_html(data['communities'])
        self.assertIn('中文备注：美联储&lt;b&gt;暗示&lt;/b&gt;降息', web)
        self.assertIn('中文备注·术语：chips=芯片', web)
        self.assertEqual(web.count('zh-note'), 2, '中文卡片不加备注')
        self.assertNotIn('href', web)
        card = dict(data['communities'][0], vclass='bull', label='偏多')
        for level in ('full', 'standard'):
            html = wp.community_card(card, level=level)
            self.assertIn('中文备注：美联储&lt;b&gt;暗示&lt;/b&gt;降息', html)
        self.assertNotIn('中文备注', wp.community_card(card, level='compact'))

    def test_render_is_pure_read(self):
        with mock.patch.object(zn, '_http', side_effect=AssertionError('render must not fetch')):
            with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'DEEPSEEK_API_KEY': 'k'}):
                html = bs.build_community_html(self.community()['communities'])
        self.assertNotIn('中文备注', html, '数据层没有备注时渲染不现算、不联网')

    def test_macro_render(self):
        item = {'title': EN, 'zh_note': '美联储暗示降息。', 'zh_note_by': 'mymemory'}
        self.assertIn('中文备注：美联储暗示降息。', bs.zh_note_html(item, 'span'))
        self.assertEqual(bs.zh_note_html({'title': ZH}), '')

    def test_annotate_file_writes_back_only_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'macro_data.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'categories': {'fed': {'items': [{'title': EN}]}}}, handle)
            self.assertEqual(zn.annotate_file('macro', path, online=False)['changed'], 1)
            with open(path, encoding='utf-8') as handle:
                saved = json.load(handle)
            self.assertEqual(saved['categories']['fed']['items'][0]['zh_note_by'], 'glossary')
            self.assertEqual(zn.annotate_file('macro', path, online=False)['changed'], 0)
            self.assertIsNone(zn.annotate_file('macro', os.path.join(tmp, 'missing.json')))
            self.assertEqual(os.listdir(tmp), ['macro_data.json'], '不留临时文件')


if __name__ == '__main__':
    unittest.main()
