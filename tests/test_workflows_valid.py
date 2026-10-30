# -*- coding: utf-8 -*-
"""CI 工作流文件完整性回归防线（纯标准库，CI 无 pyyaml）。

事故复盘（2026-10-07）
----------------------
提交 ``aba7ec11``「Update community data source count to 49」在改步骤名时把
``.github/workflows/m.yml`` **截断在第 230 行** —— 文件最后一个 step 只剩::

      - name: 🗣️ 动态抓取社区 (community_data.py · 49

既没有 ``uses:`` 也没有 ``run:``，``daily`` job 后半段（社区抓取之后的舆情因子、
build_site、内嵌负载、09:00 定时推送）整段丢失。GitHub Actions 在**校验阶段**
就拒绝整个文件：连一个 job 都没创建（``gh api repos/<o>/<r>/actions/runs/<id>/jobs``
返回 0 条），push 运行判 ``failure``、手动运行判 ``startup_failure``，耗时 0–1 秒，
页面上只有一句 “This run likely failed because of a workflow file issue”，
没有任何 step 日志 —— 极易被误判成「脚本挂了」。

而 ``python3 -m unittest discover -s tests`` 当时全绿（212 项），因为它只看 Python，
不看 workflow 文件。本文件补上这条防线：workflow 文件被截断 / 步骤缺 ``run``/``uses``
时，单测直接变红，不必等 Actions 拒收。

校验器刻意**不依赖 YAML 库**（CI 是纯标准库环境），只做缩进结构检查。
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO_ROOT, '.github', 'workflows', 'm.yml')
CANONICAL = os.path.join(REPO_ROOT, 'docs', 'ci49', 'm.yml')
SNAPSHOT = os.path.join(REPO_ROOT, '.github_workflow_new.yml')

_RUN_OR_USES = re.compile(r'^(?:uses|run)\s*:')
_BLOCK_SCALAR = re.compile(r':\s*[|>][+-]?\s*(?:#.*)?$')


def _indent(line):
    return len(line) - len(line.lstrip(' '))


def logical_lines(lines):
    """产出 (行号, 缩进, 原文)，跳过空行、注释行与块标量正文。

    块标量（``run: |``）的正文是 shell 脚本，缩进随意、可能含 ``- xxx`` 之类
    以破折号开头的行；若不跳过就会被当成新的 step 项，造成误报。
    """
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip('\n')
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue
        ind = _indent(raw)
        if _BLOCK_SCALAR.search(raw):
            j = i + 1
            while j < n:
                nxt = lines[j].rstrip('\n')
                if nxt.strip() and _indent(nxt) <= ind:
                    break
                j += 1
            yield (i + 1, ind, raw)
            i = j
            continue
        yield (i + 1, ind, raw)
        i += 1


def _enclosing_job(logical, index):
    """向上找最近的「缩进 2 且以 : 结尾」的键 —— 即 job id。"""
    for lineno, ind, raw in reversed(logical[:index]):
        body = raw.strip()
        if ind == 2 and body.endswith(':') and body != 'jobs:':
            return body[:-1]
    return None


def iter_steps(lines):
    """产出每个 step：{'job', 'lineno', 'lines'}（lines 不含块标量正文）。"""
    logical = list(logical_lines(lines))
    for idx, (lineno, ind, raw) in enumerate(logical):
        if raw.strip() != 'steps:' or ind < 2:
            continue
        job = _enclosing_job(logical, idx)
        item_indent = ind + 2
        current = None
        for lineno2, ind2, raw2 in logical[idx + 1:]:
            if ind2 <= ind:
                break
            if ind2 == item_indent and raw2.lstrip().startswith('- '):
                if current:
                    yield current
                current = {'job': job, 'lineno': lineno2, 'lines': [raw2.lstrip()]}
            elif current is not None:
                current['lines'].append(raw2.lstrip())
        if current:
            yield current


def step_problems(lines):
    """返回 [(job, 行号, 原因)] —— 缺 run/uses 的 step 即 GitHub 拒收的那类。"""
    problems = []
    for step in iter_steps(lines):
        if not any(_RUN_OR_USES.match(line) for line in step['lines']):
            head = step['lines'][0]
            name = head[2:].split(':', 1)[1].strip() if ':' in head[2:] else head
            problems.append((step['job'], step['lineno'], name or '(无名步骤)'))
    return problems


def read_lines(path):
    with open(path, encoding='utf-8') as handle:
        return handle.readlines()


class TestWorkflowNotTruncated(unittest.TestCase):
    """截断是这次事故的直接成因 —— 三条断言各挡一种表现。"""

    def test_file_ends_cleanly(self):
        with open(WORKFLOW, 'rb') as handle:
            data = handle.read()
        self.assertTrue(data.endswith(b'\n'), '工作流文件必须以换行结尾')
        last = [line for line in data.decode('utf-8').splitlines() if line.strip()]
        self.assertNotRegex(
            last[-1].strip(),
            r'^-\s*name:\s*\S.*[^)\s]\s*$',
            '最后一行是被截断的 step 名（正常收尾应是 shell 块结尾）',
        )

    def test_deployed_file_matches_canonical_full_copy(self):
        """`.github/workflows/m.yml` 必须与 `docs/ci49/m.yml`（完整副本）逐字一致。"""
        self.assertEqual(
            read_lines(WORKFLOW),
            read_lines(CANONICAL),
            '已部署的 workflow 与 docs/ci49/m.yml 不一致（多半是被截断或被局部改坏）',
        )

    def test_line_count_is_not_short(self):
        count = len(read_lines(WORKFLOW))
        self.assertGreaterEqual(
            count, len(read_lines(CANONICAL)),
            '工作流文件行数少于完整副本，疑似截断',
        )


class TestEveryStepHasRunOrUses(unittest.TestCase):
    """GitHub 的校验规则：step 必须二选一有 `uses` 或 `run`，否则整个文件作废。"""

    def test_m_yml_steps_are_complete(self):
        problems = step_problems(read_lines(WORKFLOW))
        self.assertEqual(
            [], problems,
            '存在缺 run/uses 的 step（GitHub 会拒绝整个文件）: %s' % (problems,),
        )

    def test_snapshot_steps_are_complete(self):
        problems = step_problems(read_lines(SNAPSHOT))
        self.assertEqual([], problems, '快照文件存在缺 run/uses 的 step: %s' % (problems,))

    def test_checker_actually_catches_the_incident_file(self):
        """自检：把截断后的那 230 行喂给校验器，必须报出问题，否则防线是假的。"""
        full = read_lines(CANONICAL)
        # Locate daily dynamically so legitimate additions to earlier jobs do
        # not invalidate the truncated-workflow regression test.
        daily = next(i for i, line in enumerate(full) if line.strip() == 'daily:')
        community = next(i for i in range(daily, len(full))
                         if full[i].lstrip().startswith('- name: 📥 读取社区快照'))
        lines = full[:community]
        lines.append('      - name: 📥 读取社区快照 (49 \n')
        problems = step_problems(lines)
        self.assertTrue(problems, '校验器没能识别事故文件（截断的 daily job 步骤）')
        self.assertEqual(community + 1, problems[0][1])
        self.assertEqual('daily', problems[0][0])

    def test_checker_finds_every_step_of_every_job(self):
        """自检：解析器必须真的看见全部 step，不能因为漏解析而“全绿”。"""
        found = {}
        for step in iter_steps(read_lines(WORKFLOW)):
            found[step['job']] = found.get(step['job'], 0) + 1
        self.assertEqual({'community_snapshot', 'deploy', 'wechat', 'daily'}, set(found))
        for job, count in found.items():
            self.assertGreaterEqual(count, 6 if job == 'community_snapshot' else 8,
                                    '%s 的 step 数异常少: %s' % (job, count))


class TestWorkflowStillDoesItsJob(unittest.TestCase):
    """光「文件合法」不够 —— daily job 被截掉后半段时也可能凑出合法文件。"""

    def test_daily_job_still_pushes(self):
        text = '\n'.join(read_lines(WORKFLOW))
        daily = text.split('\n  daily:')[1]
        self.assertIn('wechat_push.py --push --scheduled', daily)
        self.assertIn('build_site.py', daily)
        self.assertIn('actions/download-artifact@v4', daily)
        self.assertIn('name: community-snapshot', daily)
        self.assertIn('needs: community_snapshot', daily)
        self.assertIn("github.event.schedule != '0 */4 * * *'", daily)

    def test_triggers_and_jobs_intact(self):
        lines = read_lines(WORKFLOW)
        text = ''.join(lines)
        for token in ('on:', 'push:', 'schedule:', 'workflow_dispatch:', 'permissions:'):
            self.assertIn(token, text)
        jobs = ('community_snapshot', 'deploy', 'wechat', 'daily')
        for job in jobs:
            self.assertIn('\n  ' + job + ':', text)
        for step_owner, chunk in zip(jobs, re.split(r'\n  (?:community_snapshot|deploy|wechat|daily):', text)[1:]):
            self.assertIn('runs-on:', chunk, '%s 缺 runs-on' % step_owner)
        self.assertIn("cron: '0 */4 * * *'", text)

    def test_single_collector_and_shared_snapshot(self):
        text = ''.join(read_lines(WORKFLOW))
        chunks = re.split(r'\n  (?:community_snapshot|deploy|wechat|daily):', text)[1:]
        self.assertEqual(len(chunks), 4)
        collector, deploy, wechat, daily = chunks
        for key in ('REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET', 'REDDIT_USER_AGENT'):
            self.assertIn('secrets.' + key, collector)
        self.assertIn('community_snapshot.py --install-praw-if-configured', collector)
        self.assertIn('community_snapshot.py --plan', collector)
        self.assertIn('group: community-snapshot-four-hours', collector)
        self.assertIn('actions/cache/restore@v4', collector)
        self.assertIn('actions/cache/save@v4', collector)
        self.assertIn('actions/upload-artifact@v4', collector)
        self.assertIn('needs: community_snapshot', deploy)
        self.assertIn("github.event.schedule != '0 */4 * * *'", deploy)
        self.assertIn("github.event.schedule == '0 */4 * * *' && 'community-four-hour-trigger'", text)
        self.assertIn("cron: '*/5 * * * *'", text)
        for chunk in (deploy, wechat, daily):
            self.assertIn('actions/download-artifact@v4', chunk)
            self.assertIn('name: community-snapshot', chunk)
            self.assertNotIn('python3 community_data.py', chunk)

    def test_source_count_labels_are_49(self):
        """#66 的口径改动（34 → 49）必须落在步骤名上，且不能只改一半。"""
        text = ''.join(read_lines(WORKFLOW))
        self.assertEqual(3, text.count('读取社区快照（49 源）'), '三个构建/推送 job 均读取 49 源快照')
        self.assertNotIn('34 源', text)
        self.assertNotIn('34 大社区', text)


if __name__ == '__main__':
    unittest.main()
