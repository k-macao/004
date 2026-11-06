# RSSHub 路由核对快照

> 本文件由 `python3 community_plans.py --routes-md docs/community-49-routes.md` 生成。
> 上游：`DIYgod/RSSHub` @ `4be79cd40cae69ce68b7837f927b63cdab7be719`（2026-11-06T06:51:41Z）
> 核对日期：2026-11-06；方式：GitHub API 全量路由树 + 逐路由源码核对（本执行环境不能直连目标站点，未做在线实测）。

| 路由 | 上游文件 | 用在哪些源 |
|---|---|---|
| `/10jqka/realtimenews/:tag?` | `10jqka/realtimenews.ts` | `THS` |
| `/baidu/tieba/forum/:kw/:sortBy?` | `baidu/tieba/forum.ts` | `TIEBA` |
| `/baidu/tieba/search/:qw/:routeParams?` | `baidu/tieba/search.ts` | — |
| `/bilibili/partion/:tid/:embed?` | `bilibili/partion.ts` | `BILIBILI` |
| `/bilibili/partion/ranking/:tid/:days?/:embed?` | `bilibili/partion-ranking.ts` | `BILIBILI` |
| `/bilibili/video/reply/:bvid` | `bilibili/reply.ts` | — |
| `/bilibili/vsearch/:kw/:order?/:embed?/:tid?` | `bilibili/vsearch.ts` | `BILIBILI` |
| `/cls/depth/:category?` | `cls/depth.ts` | `CLS` |
| `/cls/telegraph/:category?` | `cls/telegraph.tsx` | `CLS` |
| `/discuss/:fid` | `discuss/index.ts` | `DISCUSS` |
| `/douyin/hashtag/:cid/:routeParams?` | `douyin/hashtag.ts` | `DOUYIN` |
| `/eastmoney/gerenzhongxin/guba/:uid` | `eastmoney/gerenzhongxin/guba.ts` | `EASTMONEY` |
| `/eastmoney/search/:keyword` | `eastmoney/search/index.ts` | `EASTMONEY` |
| `/futunn/live/:lang?` | `futunn/live.ts` | `FUTU` |
| `/futunn/main` | `futunn/main.ts` | `FUTU` |
| `/futunn/topic/:id` | `futunn/topic.ts` | `FUTU` |
| `/gelonghui/home/:tag?` | `gelonghui/home.ts` | `GELONGHUI` |
| `/gelonghui/hot-article/:type?` | `gelonghui/hot-article.ts` | `GELONGHUI` |
| `/gelonghui/keyword/:keyword` | `gelonghui/keyword.ts` | `GELONGHUI` |
| `/gelonghui/live` | `gelonghui/live.tsx` | `GELONGHUI` |
| `/jisilu/category/:id` | `jisilu/category.ts` | `JISILU` |
| `/jisilu/explore/:filter?` | `jisilu/explore.ts` | `JISILU` |
| `/jisilu/topic/:id` | `jisilu/topic.ts` | `JISILU` |
| `/laohu8/personal/:id` | `laohu8/personal.ts` | — |
| `/naver/search/:category/:keyword` | `naver/search.ts` | `NAVER` |
| `/seekingalpha/:symbol/:category?` | `seekingalpha/index.tsx` | `SEEKINGALPHA` |
| `/substack/subscribe/:user` | `substack/subscribe.ts` | — |
| `/taoguba/:category?` | `taoguba/index.ts` | `TAOGUBA` |
| `/tradingview/blog/:category{.+}?` | `tradingview/blog.ts` | `TRADINGVIEW` |
| `/twitter/keyword/:keyword/:routeParams?` | `twitter/keyword.ts` | `FINTWIT` |
| `/wallstreetcn/hot/:period?` | `wallstreetcn/hot.ts` | `WALLSTREETCN` |
| `/wallstreetcn/live/:category?/:score?` | `wallstreetcn/live.tsx` | `WALLSTREETCN` |
| `/wallstreetcn/news/:category?` | `wallstreetcn/news.ts` | `WALLSTREETCN` |
| `/weibo/keyword/:keyword/:routeParams?` | `weibo/keyword.ts` | `WEIBO` |
| `/weibo/super_index/:id/:type?/:routeParams?` | `weibo/super-index.ts` | `WEIBO` |
| `/xiaohongshu/user/:user_id/:category/:routeParams?` | `xiaohongshu/user.ts` | `XIAOHONGSHU` |
| `/xueqiu/hots` | `xueqiu/hots.ts` | `XUEQIU` |
| `/xueqiu/stock_comments/:id` | `xueqiu/stock-comments.tsx` | `XUEQIU` |
| `/yahoo/finance/:topic?` | `yahoo/finance.ts` | `YAHOO` |
| `/yahoo/finance/hk/:topic?` | `yahoo/finance-hk.ts` | `YAHOO` |
| `/yicai/brief` | `yicai/brief.ts` | `YICAI` |
| `/yicai/headline` | `yicai/headline.ts` | `YICAI` |
| `/yicai/latest` | `yicai/latest.ts` | `YICAI` |
| `/zhihu/hot/:category?` | `zhihu/hot.ts` | `ZHIHU` |
| `/zhihu/topic/:topicId/:isTop?` | `zhihu/topic.ts` | `ZHIHU` |
| `/zhitongcaijing/:id?/:category?` | `zhitongcaijing/index.tsx` | `ZHITONG` |

## 无路由站点

`reddit.com`、`ptt.cc`、`lihkg.com`、`stocktwits.com`、`bogleheads.org`、`wallstreetoasis.com`、`investing.com`、`quantnet.com`、`elitetrader.com`、`forexfactory.com`、`morningstar.com`、`marketwatch.com`、`wallstreet-online.de`、`aastocks.com`、`antfortune.com`、`jiucaishuo.com`、`kaipanla.com`、`55188.com`、`valueinvestorsclub.com`、`finance.yahoo.co.jp`
