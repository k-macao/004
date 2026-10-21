#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 量化平台「现成舆情/新闻因子」数据源注册表
================================================================

本文件是 sentiment_factors.py / tools/probe_sentiment_apis.py 的唯一事实源
（single source of truth）：把 聚宽、米筐、掘金、优矿、Tushare 等平台的
舆情/新闻因子接口，统一描述成结构化条目，供「接入实测 + 打分 + 报告注入」复用。

设计要点
  • 只做声明（数据在哪、怎么鉴权、返回什么字段、多久更新、要不要付费），
    真正的网络调用在 sentiment_adapters.py，打分在 tools/probe_sentiment_apis.py。
  • 每个源都标注 `confidence`：
      - 'verified_doc'  已比对官方文档/官方站点（字段、频率、口径有据可查）
      - 'needs_check'   接口形态来自公开资料，需在有权环境实测复核
    接入前优先复核 'needs_check'，避免按过期文档写死代码。
  • 凭据一律只写环境变量名，绝不落库、不写死 token。
  • 境内源（聚宽/米筐/掘金/优矿/东财/金十）会按 IP 拒绝境外访问：
    GitHub 海外 runner 直连必然失败，实测必须放在境内执行器上（见 sentiment_runner 说明）。

用法:
  python3 sentiment_sources.py                 # 打印清单 + 凭据/依赖状态
"""

# ---------------------------------------------------------------------------
# 因子口径: 本项目统一使用的舆情/新闻因子 (由 sentiment_factors.py 产出)
# ---------------------------------------------------------------------------
FACTOR_LIBRARY = {
    'SENT_TEMP': {
        'name': '市场舆情温度计',
        'unit': '0-100',
        'definition': '净情感 + 热度异动 + 负面占比 加权映射到 0-100；>65 偏亢奋、<35 偏恐慌',
        'best_source': 'UQER_HTTP / RQ_SDK / CHINASCOPE',
    },
    'NET_SENTI': {
        'name': '净情感强度',
        'unit': '-1 ~ +1',
        'definition': 'Σ(情感权重 × 公司相关度 × 时间衰减) / Σ权重，正值代表正面舆情占优',
        'best_source': 'RQ_SDK (news_emotion_indicator) / UQER_HTTP (sentimentIndex)',
    },
    'NEWS_HEAT_Z': {
        'name': '新闻热度 Z 值',
        'unit': 'σ',
        'definition': '当日关联新闻量相对近 20 日均值的标准分，|Z|>2 视为舆情异动',
        'best_source': 'UQER_HTTP (heatIndex) / EM_COMMENT (关注指数) / JQ_SDK (雪球热度)',
    },
    'NEG_SHARE': {
        'name': '负面舆情占比',
        'unit': '%',
        'definition': '负向情感新闻条数 / 全部关联新闻条数；>35% 触发风险提示',
        'best_source': 'RQ_SDK (news_negative_weight) / SELF_NLP (自建词库)',
    },
    'EVENT_RISK': {
        'name': '突发事件风险分',
        'unit': '0-100',
        'definition': '监管/诉讼/违约/退市/减持/质押等高风险词加权命中，单日取最大值',
        'best_source': 'EM_NEWS / TUSHARE_NEWS / JQ_SDK（公告+新闻文本）→ 自建词库打分',
    },
}

# ---------------------------------------------------------------------------
# 探测阶段 (tools/probe_sentiment_apis.py 逐阶段执行，任一阶段失败即短路)
# ---------------------------------------------------------------------------
PROBE_STAGES = [
    ('deps', '依赖可用性', 'SDK/Python 包能否导入（纯 HTTP 源恒通过）'),
    ('net', '网络可达性', 'API 端点 TCP+TLS 是否可达（区分"平台故障"与"本地出口受限/境外 IP 被拒"）'),
    ('auth', '鉴权与权限', '凭据是否存在、登录是否成功、数据权限是否开通'),
    ('fetch', '取数能力', '真实取样一次，是否返回非空结果集'),
    ('fields', '字段完整度', '舆情/新闻关键字段是否存在且非空率达标'),
    ('fresh', '时效新鲜度', '最新记录距今天数是否 ≤ 阈值（日内源要求 0-1 天）'),
    ('cover', '标的覆盖率', '抽样标的中能取到舆情/新闻的比例'),
    ('latency', '响应延迟', '连续取样的 p50 / p95 延迟'),
    ('quota', '额度与限频', '单次上限、日额度、并发限制是否满足日报节奏'),
]

# 打分维度与权重（100 分制）— 面向"日报级舆情因子接入"这一具体用途
SCORE_DIMENSIONS = [
    ('ready_factor', 30, '现成因子度', '是否直接给出可入模的舆情/新闻因子值（而非仅给原始文本）'),
    ('timeliness', 15, '时效性', '日内多次 / 半小时级 > T+1 日更 > 周更'),
    ('coverage', 10, '覆盖与口径', 'A股/港股覆盖广度、个股级 vs 市场级、历史长度'),
    ('integration', 15, '接入成本', '纯 HTTP + 免安装最低；需私有 pip 源 / 本地终端最高'),
    ('stability', 10, '稳定性与延迟', '接口可用率、返回结构稳定性、p95 延迟'),
    ('cost', 20, '成本与权限门槛', '免费额度是否够日报量级；是否需商务开通'),
]

VERDICT_LABEL = {
    'READY': '可直接接入',
    'READY_WITH_LICENCE': '可接入（需开权限/付费）',
    'PARTIAL': '部分可用（需自建清洗或 NLP）',
    'NOT_SUPPORTED': '平台不提供该能力',
    'NO_CREDENTIAL': '缺凭据未实测',
    'NET_BLOCKED': '出口受限未实测',
    'UNKNOWN': '待实测',
}

# ---------------------------------------------------------------------------
# 对外展示开关：平台「接入评测（9 阶段实测）」区块默认**不**渲染给读者
# ---------------------------------------------------------------------------
# 隐藏范围仅限「对外输出」：
#   • build_site.py       → 网页 report.html 的 03B 节不再出现评分矩阵与 9 阶段说明
#   • tools/wechat_push.py → 微信推送 03B 节不再出现「接入评测（9 阶段实测）」方框
# 管线本身完全保留：tools/probe_sentiment_apis.py 照常跑 9 阶段实测、
# api_probe_report.json 照常产出、sentiment_factors.py 的 api_eval 字段照常写入
# sentiment_data.json、docs/sentiment-api-eval.md 照常刷新（结论沉淀在仓库文档里）。
# 内部评审 / 截图需要临时恢复展示时：SENTIMENT_SHOW_API_EVAL=1
SHOW_API_EVAL_ENV = 'SENTIMENT_SHOW_API_EVAL'

# 对外输出（网页 03B 节 + 微信推送 03B 节）**不显示数据来源**：
# 平台名 / 接口 ID / 域名 / SDK / 凭据与依赖提示统一由 sentiment_match.redact() 遮成「量化平台」，
# 逐源明细表默认不渲染；来源信息仍完整保留在 sentiment_data.json（内部构建产物）与本注册表里。
# 内部核对需要临时显示来源时：SENTIMENT_SHOW_SOURCE=1
SHOW_SOURCE_ENV = 'SENTIMENT_SHOW_SOURCE'


def show_source():
    """对外输出是否显示数据来源平台（默认 False ＝ 不显示）。"""
    import os
    return str(os.environ.get(SHOW_SOURCE_ENV, '')).strip().lower() in ('1', 'true', 'yes', 'on')


def show_api_eval():
    """是否对外展示「量化平台现成舆情/新闻因子接入评测（9 阶段实测）」区块。

    默认 False（隐藏）；只有显式设置 SENTIMENT_SHOW_API_EVAL=1/true/yes/on 才展示。
    """
    import os
    return str(os.environ.get(SHOW_API_EVAL_ENV, '')).strip().lower() in ('1', 'true', 'yes', 'on')


# ---------------------------------------------------------------------------
# 数据源清单
# ---------------------------------------------------------------------------
SOURCES = [
    # ---------------- 聚宽 JoinQuant ----------------
    {
        'id': 'JQ_HTTP',
        'platform': '聚宽 JoinQuant',
        'name': 'JQData 旧版 HTTP 数据接口',
        'kind': 'jq_http',
        'category': 'price_volume_sentiment + attention_heat',
        'access': 'http',
        'endpoint': 'https://dataapi.joinquant.com/apis',
        'methods': ['get_token', 'get_current_token', 'get_all_factors', 'get_factor_values',
                    'get_alpha101', 'get_alpha191', 'get_query_count', 'get_price'],
        'auth_env': [['JQ_MOBILE', 'JQ_USERNAME'], ['JQ_PASSWORD']],
        'requires': [],
        'sentiment_fields': {
            '因子库·情绪类': ['VOL5', 'VOL10', 'VOL20', 'VOL60', 'VOL120', 'AR', 'BR', 'ARBR',
                             'VEMA5', 'DAVOL5', 'DAVOL10', 'ATR6', 'ATR14'],
            '舆情·雪球热度': ['热度值（2015 至今，每日 03:00 前更新）'],
            '舆情·新闻联播': ['finance.CCTV_NEWS 文本表（2009-06 至今，每日 20:30 前更新）'],
        },
        'update_freq': '因子 T+1 日 05:00 前；雪球热度日 03:00 前；新闻联播日 20:30 前',
        'history': '因子 2005 至今；雪球热度 2015 至今；新闻联播 2009-06 至今',
        'coverage': '沪深 A 股（个股级因子 + 市场级文本）',
        'granularity': '日频',
        'quota': '试用 100 万条/天、并发 3；正式版约 2 亿条/天（get_query_count 可查余量）',
        'cost': '试用免费（1 年，仅基础数据）；因子与特色数据（含舆情）需标准版/专业版按年付费',
        'licence_note': '两个硬坑：① 试用账号"因子和特色数据：无"，情绪因子与雪球热度必须付费；'
                        '② 官方文档页顶部标注「旧版 http 接口已不再维护，随时可能下线，不再建议使用」',
        'docs': ['https://dataapi.joinquant.com/docs',
                 'https://www.joinquant.com/help/api/help?name=JQData',
                 'https://www.joinquant.com/view/factorlib/list'],
        'confidence': 'verified_doc',
        'deprecated': True,
        'doc_scores': {'ready_factor': 15, 'timeliness': 6, 'coverage': 6,
                       'integration': 8, 'stability': 4, 'cost': 6},
        'notes': '聚宽"情绪因子"是量价/换手量能类（VOL、AR/BR、ATR、DAVOL），并非新闻 NLP 情感；'
                 '真正的新闻情感因子平台不给，只给雪球热度与新闻文本。因 HTTP 版已标记不再维护，'
                 '本项目仅保留作对照，生产接入请用 JQ_SDK。',
    },
    {
        'id': 'JQ_SDK',
        'platform': '聚宽 JoinQuant',
        'name': 'jqdatasdk 本地 SDK（因子库 + finance 舆情表）',
        'kind': 'jq_sdk',
        'category': 'price_volume_sentiment + news_text',
        'access': 'python_sdk',
        'endpoint': None,
        'methods': ['auth', 'get_query_count', 'get_all_factors', 'get_factor_values',
                    'get_index_stocks', 'finance.run_query'],
        'sdk_module': 'jqdatasdk',
        'auth_env': [['JQ_MOBILE', 'JQ_USERNAME'], ['JQ_PASSWORD']],
        'requires': ['jqdatasdk'],
        'sentiment_fields': {
            'get_factor_values': ['securities', 'factors', 'start_date', 'end_date', 'count'],
            '舆情表': ['finance.CCTV_NEWS（新闻联播文字稿）', '雪球热度数据', '百度指数因子（付费特色数据）'],
        },
        'update_freq': '因子 T+1 日更；新闻联播每日 20:30 前',
        'history': '2005 至今（因子）/ 2009-06 至今（新闻联播）',
        'coverage': '沪深 A 股',
        'granularity': '日频',
        'quota': 'auth 后 get_query_count() 直接返回剩余条数 —— 配额探针可零成本实现',
        'cost': '同上：因子与舆情属特色数据，需付费版本',
        'licence_note': 'SDK 与官网账号体系一致；聚宽研究环境内 import jqdata 免鉴权（但只能在云上跑）',
        'docs': ['https://www.joinquant.com/help/api/help?name=api'],
        'confidence': 'verified_doc',
        'doc_scores': {'ready_factor': 15, 'timeliness': 6, 'coverage': 6,
                       'integration': 9, 'stability': 8, 'cost': 6},
        'notes': '返回 DataFrame，便于与既有 pandas 管线拼接；CI 需 pip install jqdatasdk（PyPI 可装）。',
    },

    # ---------------- 米筐 RiceQuant ----------------
    {
        'id': 'RQ_SDK',
        'platform': '米筐 RiceQuant',
        'name': 'RQData 新闻舆情数据 news.get_stock_news（真·现成舆情因子）',
        'kind': 'rq_sdk',
        'category': 'news_sentiment',
        'access': 'python_sdk',
        'endpoint': None,
        'methods': ['rqdatac.init', 'rqdatac.news.get_stock_news', 'rqdatac.get_announcement',
                    'rqdatac.get_factor', 'rqdatac.get_all_factor_names'],
        'sdk_module': 'rqdatac',
        'extra_module': 'rqdatac_news',
        'auth_env': [['RQDATA_USER', 'RQDATAC_USER'], ['RQDATA_PASSWORD', 'RQDATAC_PASSWORD']],
        'requires': ['rqdatac', 'rqdatac_news'],
        'pip_index': 'https://py.ricequant.com/simple/',
        'sentiment_fields': {
            'news.get_stock_news': ['news_id', 'title', 'original_time', 'url', 'source',
                                    'news_emotion_indicator', 'news_neutral_weight',
                                    'news_positive_weight', 'news_negative_weight',
                                    'company_relevance', 'company_emotion_indicator',
                                    'company_neutral_weight', 'company_positive_weight',
                                    'company_negative_weight'],
        },
        'update_freq': '日内每 30 分钟更新（正式数据）',
        'history': '2017 至今',
        'coverage': 'A 股个股级（新闻层 + 公司层双情感口径）',
        'granularity': '事件级（每条新闻一行）+ 可日频聚合',
        'quota': '按订阅数据包授权；股票数 × 区间长度需分批（文档未给硬性条数上限，实测补）',
        'cost': '机构订阅制，需商务开通舆情数据包；个人/高校版通常不含舆情',
        'licence_note': '两个必踩坑：① rqdatac 需从米筐私有 pip 源安装（PyPI 无包）；'
                        '② 舆情模块必须单独 pip install rqdatac_news，否则 rqdatac.news 不存在',
        'docs': ['https://www.ricequant.com/doc/rqdata/python/alternative-data'],
        'confidence': 'verified_doc',
        'doc_scores': {'ready_factor': 28, 'timeliness': 14, 'coverage': 7,
                       'integration': 6, 'stability': 8, 'cost': 4},
        'notes': '字段口径最适合直接建因子：情绪极性(-1/0/1) + 正/中/负三档权重 + 公司相关度，'
                 '可做"新闻级情感 × 相关度 × 时间衰减"的标准舆情因子；缺点是要付费 + 私有源安装。',
    },
    {
        'id': 'RQ_HTTP',
        'platform': '米筐 RiceQuant',
        'name': 'RQData HTTP/WebSocket 接口（舆情同源，机构合同开通）',
        'kind': 'rq_http',
        'category': 'news_sentiment',
        'access': 'http',
        'endpoint': 'https://api.ricequant.com/apis',
        'methods': ['token', 'news.get_stock_news', 'get_price'],
        'auth_env': [['RQDATA_TOKEN'], ['RQDATA_USER', 'RQDATAC_USER'],
                     ['RQDATA_PASSWORD', 'RQDATAC_PASSWORD']],
        'requires': [],
        'sentiment_fields': {'说明': '公开文档以 SDK 为主；HTTP/WebSocket 端点与鉴权头由机构合同提供，'
                                     '实时行情推送语言中立（可用非 Python 接入）'},
        'update_freq': '同 RQ_SDK',
        'history': '2017 至今',
        'coverage': 'A 股个股级',
        'granularity': '事件级',
        'quota': '按合同',
        'cost': '机构订阅制',
        'licence_note': '公开渠道未给出通用 HTTP 端点，本条目为占位，签约后再据实填写，勿据此写死代码',
        'docs': ['https://www.ricequant.com/doc/rqdata/python/generic-api'],
        'confidence': 'needs_check',
        'doc_scores': {'ready_factor': 28, 'timeliness': 14, 'coverage': 7,
                       'integration': 11, 'stability': 8, 'cost': 3},
        'notes': '若已签 RQData，优先走 HTTP：CI 免装 rqdatac，且与本项目"纯标准库"约束一致。',
    },

    # ---------------- 掘金量化 ----------------
    {
        'id': 'GM_SDK',
        'platform': '掘金量化 Myquant',
        'name': '掘金 gm.api 数据接口（无舆情因子，需终端代理）',
        'kind': 'gm_sdk',
        'category': 'none',
        'access': 'local_terminal',
        'endpoint': 'http://127.0.0.1:31000',
        'methods': ['set_token', 'history', 'history_n', 'current', 'get_symbol_infos',
                    'stk_get_fundamentals_income_pt', 'stk_get_daily_valuation_pt',
                    'stk_get_index_constituents', 'get_trading_dates_by_year'],
        'sdk_module': 'gm',
        'auth_env': [['GM_TOKEN']],
        'requires': ['gm'],
        'sentiment_fields': {
            '说明': '掘金数据 API（2024-09-30 起老版 13 个函数已下线换新）覆盖行情/财务/成分/日历/估值，'
                    '不提供新闻、舆情、情感或情绪因子；官方 FAQ 明确"API 暂不支持获取指标数据，需要自行设计实现"'},
        'update_freq': '行情实时（tick/分钟）；财务按报告期',
        'history': '近 10 年日线/分钟/tick（行情类）',
        'coverage': '股票、期货、期权、场内基金、可转债（均无舆情维度）',
        'granularity': 'tick / 分钟 / 日',
        'quota': '单次查询最多 33000 行；免费版订阅上限 50 个标的（超出报 1202）；L2 仅券商内网且需 500 万门槛',
        'cost': '基础版免费；专业版按年；券商版由券商收费；机构版定制',
        'licence_note': 'SDK 通过掘金终端代理取数，终端必须常驻 → 无 GUI 的 CI/服务器环境完全不可用',
        'docs': ['https://www.myquant.cn/docs2/quickStart/%E6%95%B0%E6%8D%AE.html',
                 'https://www.myquant.cn/docs2/faq/%E4%BA%A7%E5%93%81%E9%97%AE%E9%A2%98.html'],
        'confidence': 'verified_doc',
        'verdict_hint': 'NOT_SUPPORTED',
        'doc_scores': {'ready_factor': 0, 'timeliness': 6, 'coverage': 2,
                       'integration': 2, 'stability': 6, 'cost': 14},
        'notes': '结论明确：掘金适合作行情 + 交易执行通道；舆情/新闻因子必须外挂'
                 '（自建 NLP，或从米筐/优矿取数后回灌掘金的组合管理）。',
    },

    # ---------------- 优矿 Uqer（通联数据） ----------------
    {
        'id': 'UQER_HTTP',
        'platform': '优矿 Uqer（通联数据）',
        'name': 'DataAPI 新闻情感/热度指数（现成舆情因子最完整）',
        'kind': 'uqer_http',
        'category': 'news_sentiment + attention_heat',
        'access': 'http',
        'endpoint': 'https://api.uqer.cn/data',
        'methods': ['NewsSentimentIndexGet', 'NewsHeatIndexGet', 'NewsByTickersGet',
                    'NoticeByTickersGet', 'MktStockFactorsOneDayGet'],
        'auth_env': [['UQER_TOKEN']],
        'requires': [],
        'sentiment_fields': {
            'NewsSentimentIndexGet': ['secID', 'tradeDate', 'sentimentIndex'],
            'NewsHeatIndexGet': ['secID', 'tradeDate', 'heatIndex'],
            'NewsByTickersGet': ['newsID', 'title', 'source', 'publishTime', 'insertTime', 'secID'],
        },
        'update_freq': '情感/热度指数按日更新；新闻明细实时更新',
        'history': '指数自 2004-10-28 起（2014-01-01 起来源完整、统计有效）',
        'coverage': '沪深个股（按 secID 批量），另含雪球/股吧社交数据与公告文本',
        'granularity': '日频因子 + 事件级新闻',
        'quota': '按 token 限速；单次批量 secID 建议 ≤ 300 只（需实测）',
        'cost': '标准版免费（约 200 基础因子）；特色/大数据因子需专业版，第三方源另计费',
        'licence_note': '优矿近年迭代放缓（文档变更日志停在早期），DataAPI 可用性与字段名需在有权环境实测复核后再上线',
        'docs': ['https://uqer.datayes.com/help/introduction/', 'https://uqer.datayes.com/data/'],
        'confidence': 'needs_check',
        'doc_scores': {'ready_factor': 27, 'timeliness': 10, 'coverage': 8,
                       'integration': 12, 'stability': 6, 'cost': 10},
        'notes': 'sentimentIndex = 当日关联新闻情感均值、heatIndex = 新闻热度，两个数即可直接入模；'
                 '若有可用 token，性价比高于米筐（存在免费层）。',
    },

    # ---------------- Tushare Pro ----------------
    {
        'id': 'TUSHARE_NEWS',
        'platform': 'Tushare Pro',
        'name': 'news / major_news / cctv_news 新闻文本接口',
        'kind': 'tushare_http',
        'category': 'news_text',
        'access': 'http',
        'endpoint': 'http://api.tushare.pro',
        'methods': ['news', 'major_news', 'cctv_news', 'anns_d', 'irm_qa_sh', 'irm_qa_sz'],
        'auth_env': [['TUSHARE_TOKEN']],
        'requires': [],
        'sentiment_fields': {
            'news': ['datetime', 'title', 'content', 'src'],
            'major_news': ['pub_time', 'title', 'content', 'src_site'],
            'src 可选值': ['sina', 'wallstreetcn', '10jqka', 'eastmoney', 'yuncaijing',
                          'fenghuang', 'jinrongjie', 'cls', 'yicai'],
        },
        'update_freq': '准实时（分钟级入库）',
        'history': 'news 6 年以上；major_news 8 年以上',
        'coverage': '全市场财经快讯（不做个股关联，需自建实体识别映射到标的）',
        'granularity': '事件级',
        'quota': 'news 单次最大 1500 条；major_news 单次 400 行，可循环补历史',
        'cost': '需单独开权限（与积分无关）；互动易/上证 e 互动 120 积分可试用、10000 积分正式',
        'licence_note': '给的是原始文本而非因子，需自建情感打分；胜在便宜、纯 HTTP、易进 CI',
        'docs': ['https://tushare.pro/document/2?doc_id=143', 'https://tushare.pro/document/2?doc_id=195'],
        'confidence': 'verified_doc',
        'doc_scores': {'ready_factor': 8, 'timeliness': 14, 'coverage': 6,
                       'integration': 14, 'stability': 8, 'cost': 14},
        'notes': 'POST {api_name, token, params, fields} → JSON，是"自建舆情因子"层最省事的文本源。',
    },

    # ---------------- 免费无鉴权兜底层（保证日报永不断供） ----------------
    {
        'id': 'CHINASCOPE',
        'platform': '数库 Chinascope（免费公开）',
        'name': 'A 股新闻情绪指数（市场级，日更）',
        'kind': 'chinascope_http',
        'category': 'news_sentiment',
        'access': 'http_free',
        'endpoint': 'https://www.chinascope.com/inews/senti/index',
        'methods': ['GET ?period=YEAR'],
        'auth_env': [],
        'requires': [],
        'sentiment_fields': {'返回字段': ['tradeDate（日期）', 'maIndex1（市场情绪指数）',
                                          'marketClose（沪深300）']},
        'update_freq': '交易日日更（akshare 同源实现口径）',
        'history': '接口返回近一年',
        'coverage': 'A 股市场级（无个股维度）',
        'granularity': '日频',
        'quota': '无鉴权、无文档化限额；作兜底建议 ≤ 1 次/日 + 本地缓存',
        'cost': '免费公开',
        'licence_note': '实测风险：原域名 www.chinascope.com 已 302 跳至 .com.cn 首页，'
                        'inews/senti/index 疑随官网改版下线；上线前必须先探一次 404/302 再决定是否保留本层',
        'docs': ['https://www.chinascope.com.cn/reasearch.html', 'https://akshare.akfamily.xyz/data/index/index.html'],
        'confidence': 'needs_check',
        'unstable': True,
        'doc_scores': {'ready_factor': 22, 'timeliness': 10, 'coverage': 3,
                       'integration': 15, 'stability': 3, 'cost': 20},
        'notes': '数库基于每日数十万篇财经新闻做情感识别（SmarTag：7 大新闻标签 + 2 大新闻情绪维度），'
                 '并与 J.P. Morgan 亚太量化团队共建情绪指数；市场级温度计可直接用，个股仍需自建。',
    },
    {
        'id': 'EM_COMMENT',
        'platform': '东方财富（免费公开）',
        'name': '千股千评：用户关注指数 / 机构参与度 / 综合得分',
        'kind': 'em_datacenter',
        'category': 'attention_heat',
        'access': 'http_free',
        'endpoint': 'https://datacenter-web.eastmoney.com/api/data/v1/get',
        'methods': ['reportName=RPT_DMSK_TS_STOCKNEW（全市场分页）',
                    'reportName=RPT_STOCK_MARKETFOCUS（个股用户关注指数）',
                    'reportName=RPT_DMSK_TS_STOCKEVALUATE（机构参与度）',
                    'reportName=RPT_STOCK_HISTORYMARK（历史评分）'],
        'auth_env': [],
        'requires': [],
        'sentiment_fields': {
            '全市场': ['SECURITY_CODE', 'SECURITY_NAME_ABBR', 'TRADE_DATE', 'MARKET_FOCUS(关注指数)',
                      'ORG_PARTICIPATE(机构参与度)', 'TOTAL_SCORE(综合得分)'],
            '个股历史': ['TRADE_DATE', 'MARKET_FOCUS / ORG_PARTICIPATE / TOTAL_SCORE'],
        },
        'update_freq': '交易日日更',
        'history': '全市场单日快照；个股序列约 30 条/页',
        'coverage': '沪深 A 股全市场（一次分页可取 500 只）',
        'granularity': '日频',
        'quota': 'pageSize ≤ 500；无限额文档，需自带重试与限速',
        'cost': '免费公开',
        'licence_note': '属热度/关注度因子，不含情感极性，需与新闻文本层结合才成"舆情因子"',
        'docs': ['https://data.eastmoney.com/stockcomment/'],
        'confidence': 'verified_doc',
        'doc_scores': {'ready_factor': 18, 'timeliness': 10, 'coverage': 9,
                       'integration': 15, 'stability': 6, 'cost': 20},
        'notes': '与米筐"舆情大数据（东财股吧，2007-10 至今，天度/小时情绪指数 + 热度因子）"同源，'
                 '可作机构版权益的热度替代。',
    },
    {
        'id': 'EM_NEWS',
        'platform': '东方财富（免费公开）',
        'name': 'search-api 个股新闻检索（关键词 → 标题 + 摘要）',
        'kind': 'em_search_api',
        'category': 'news_text',
        'access': 'http_free',
        'endpoint': 'https://search-api-web.eastmoney.com/search/jsonp',
        'methods': ['GET ?param={"uid":"","keyword":"601318","type":["cmsArticleWebOld"],'
                    '"param":{"cmsArticleWebOld":{"pageIndex":1,"pageSize":20}}}'],
        'auth_env': [],
        'requires': [],
        'sentiment_fields': {'返回字段': ['date', 'title', 'content', 'mediaName', 'code']},
        'update_freq': '准实时',
        'history': '最近约 100 条滚动',
        'coverage': 'A 股/港股可按代码或关键词检索',
        'granularity': '事件级',
        'quota': 'pageSize 建议 ≤ 20；无鉴权但可能要求浏览器 UA / Cookie（akshare 实现带固定 cookie）',
        'cost': '免费公开',
        'licence_note': 'JSONP 外壳需剥离；标题噪音高，情感需人工规则复核后才能入实盘',
        'docs': ['https://so.eastmoney.com/news/s?keyword=601318'],
        'confidence': 'verified_doc',
        'doc_scores': {'ready_factor': 8, 'timeliness': 14, 'coverage': 6,
                       'integration': 13, 'stability': 5, 'cost': 20},
        'notes': '本项目自建情感层（sentiment_nlp.py）的默认文本源之一。',
    },
    {
        'id': 'JIN10_WEIBO',
        'platform': '金十数据（免费公开）',
        'name': '微博舆情报告（个股讨论人气排行指数）',
        'kind': 'jin10_weibo',
        'category': 'attention_heat',
        'access': 'http_free',
        'endpoint': 'https://datacenter-api.jin10.com/weibo/list',
        'methods': ['GET ?timescale=CNHOUR2|CNHOUR6|CNHOUR12|CNHOUR24|CNDAY7|CNDAY30'],
        'auth_env': [],
        'requires': [],
        'sentiment_fields': {'返回字段': ['股票代码/简称', '微博人气排行指数', '区间涨幅']},
        'update_freq': '小时级（2/6/12/24 小时窗口）',
        'history': '滚动窗口，不留长历史',
        'coverage': 'A 股（按讨论热度排序的 Top 榜）',
        'granularity': '小时级',
        'quota': '需带 x-app-id / x-version 等浏览器头（akshare 同源），无正式配额',
        'cost': '免费公开',
        'licence_note': '私有请求头 + 无 SLA，随时可能加签名；只做预警辅助，不入主因子',
        'docs': ['https://datacenter.jin10.com/market'],
        'confidence': 'needs_check',
        'unstable': True,
        'doc_scores': {'ready_factor': 16, 'timeliness': 14, 'coverage': 4,
                       'integration': 10, 'stability': 4, 'cost': 20},
        'notes': '小时级热度是"舆情异动预警"的极佳补充，但只有热度没有极性。',
    },
]


def get_source(source_id):
    """按 id 取源定义，找不到返回 None。"""
    for s in SOURCES:
        if s['id'] == source_id:
            return s
    return None


def ids():
    return [s['id'] for s in SOURCES]


def by_platform():
    out = {}
    for s in SOURCES:
        out.setdefault(s['platform'], []).append(s)
    return out


def credential_status():
    """检查各源所需凭据环境变量是否已配置（只判断存在与否，绝不读取内容）。"""
    import os
    out = {}
    for s in SOURCES:
        slots = s.get('auth_env') or []
        got, missing = [], []
        for group in slots:
            names = list(group) if isinstance(group, (list, tuple)) else [group]
            if any(os.environ.get(n) for n in names):
                got.append('/'.join(names))
            else:
                missing.append('/'.join(names))
        out[s['id']] = {
            'platform': s['platform'],
            'required': bool(slots),
            'present': got,
            'missing': missing,
            'ready': (not slots) or (not missing),
        }
    return out


def deps_status():
    """检查各源所需 Python 包能否导入（不安装、不联网）。"""
    import importlib.util
    out = {}
    for s in SOURCES:
        want = []
        for m in ([s.get('sdk_module'), s.get('extra_module')] + list(s.get('requires') or [])):
            if m and m not in want:
                want.append(m)
        detail = {}
        for m in want:
            try:
                detail[m] = importlib.util.find_spec(m) is not None
            except (ImportError, ValueError, ModuleNotFoundError):
                detail[m] = False
        out[s['id']] = {
            'modules': want,
            'present': [m for m, ok in detail.items() if ok],
            'missing': [m for m, ok in detail.items() if not ok],
        }
    return out


def doc_score(source_id):
    """返回注册表里预置的"文档基线分"（未实测时的评估起点）。"""
    s = get_source(source_id) or {}
    return dict(s.get('doc_scores') or {})


# 舆情样本池：默认关注名单 + 中文名（无热度快照时用于展示）
# 已清空个股关注列表（指定个股已删除）
WATCHLIST = []
WATCHLIST_NAMES = {
    '601318': '中国平安', '00700': '腾讯控股', '09988': '阿里巴巴-W',
    '600036': '招商银行', '300750': '宁德时代',
}


def table():
    """人类可读清单（控制台用）。"""
    lines = [f"{'ID':<14}{'平台':<20}{'类别':<38}{'接入方式':<16}{'现成因子':>8}"]
    lines.append('-' * 100)
    for s in SOURCES:
        lines.append(f"{s['id']:<14}{s['platform']:<20}{s['category']:<38}"
                     f"{s['access']:<16}{s['doc_scores']['ready_factor']:>8}")
    return '\n'.join(lines)


if __name__ == '__main__':
    print(table())
    print('\n凭据状态:')
    for k, v in credential_status().items():
        flag = '✅' if v['ready'] else ('—' if not v['required'] else '⚠️')
        print(f"  {flag} {k:<14} {v['platform']:<18} 缺失: {', '.join(v['missing']) or '无（免鉴权）'}")
    print('\n依赖状态:')
    for k, v in deps_status().items():
        if v['modules']:
            print(f"  {k:<14} 需要 {', '.join(v['modules'])} · 已装 {', '.join(v['present']) or '无'}")
