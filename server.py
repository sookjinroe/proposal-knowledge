"""제안전략 지식시스템 편집기 — 로컬 서버.

K드라이브의 md 레코드를 브라우저에서 읽고 고친다. 저장하면 K:에 바로 쓴다.
외부 의존성 없음. 127.0.0.1 에만 바인딩한다.

    python server.py            # http://127.0.0.1:8777
    python server.py --port 9000
    python server.py --root "K:/Workgroups/제안전략 지식시스템"
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import shutil
import sys
import hashlib
import subprocess
import threading
import time
import traceback
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = 'K:/Workgroups/제안전략 지식시스템'
BACKUP = os.path.join(HERE, '_백업')

CATS = [
    ('01_사업실적', '사업실적', True),
    ('02_보유자산', '보유자산', False),
    ('03_수행체계', '수행체계', False),
    ('04_회사정보', '회사정보', False),
    ('05_오퍼링', '오퍼링', False),
    ('06_인력', '인력', False),
    ('07_실주사업', '실주사업', True),
]

# 폴더 방식 카테고리의 하위 폴더 — 레코드를 새로 만들 때 같이 판다.
# 자료를 둘 자리가 없으면 결국 아무 데나 쌓인다.
SUBDIRS = {
    '01_사업실적': ['제안자료', '보고자료', '산출물', '증빙'],
    # 실주 건에는 산출물·증빙이 없다. 제안서와 실주 보고서뿐이다.
    '07_실주사업': ['제안자료', '실주보고서'],
}

# 카테고리별 필수 필드 — 저장 전에 있는지 본다
REQUIRED = {
    '01_사업실적': ['사업명', '고객사 / 발주처', '수행 기간', '진행 상태',
                 '참여 형태와 담당 범위', '계약 금액', '투입 인력', '선행·후속 사업'],
    '02_보유자산': ['자산명', '기준 시점', '소속 오퍼링', '담당자'],
    # 수행체계에는 담당자가 없는 게 맞다. 체계는 사람에게 딸린 것이 아니다.
    # 필수로 걸어 둔 탓에 24건 전부가 영영 못 채우는 (확인 필요)를 달고 있었다.
    '03_수행체계': ['체계명', '기준 시점'],
    '05_오퍼링': ['오퍼링명'],
    # 실주 건은 모르는 것이 많다. 제안가·제안 PM·투입 인력은 뒤늦게 채워지므로
    # 필수로 걸지 않는다. 없으면 (확인 필요) 로 두고 저장은 막지 않는다.
    '07_실주사업': ['사업명', '고객사 / 발주처', '제안 시점'],
}

ROOT = DEFAULT_ROOT


# ─────────────────────────── 파싱 ───────────────────────────

# 「확인 필요」 표시를 찾는 규칙. 세는 쪽과 노랗게 칠하는 쪽이 이 하나를 같이 쓴다.
# 따로 두었더니 닫는 괄호를 빼먹은 자리가 숫자만 오르고 칠해지지 않았다.
# 괄호는 반각·전각 둘 다, 띄어쓰기는 몇 칸이든(없어도) 같은 것으로 본다.
Q_OPEN = re.compile(r'[(（]\s*확인\s*필요')
_OPENERS, _CLOSERS = '(（', ')）'


def q_spans(s):
    """「확인 필요」 표시가 놓인 자리를 [시작, 끝) 으로 돌려준다.

    괄호 안에 괄호가 또 있어도(「파일명(확장자 제외)」) 짝을 맞춰 끝까지 간다.
    닫는 괄호가 없으면 그 줄 끝까지로 본다 — 안 그러면 뒤가 통째로 물든다.
    """
    out, at = [], 0
    while True:
        m = Q_OPEN.search(s, at)
        if not m:
            return out
        depth, i, end = 0, m.start(), None
        while i < len(s):
            c = s[i]
            if c == '\n':
                break
            elif c in _OPENERS:
                depth += 1
            elif c in _CLOSERS:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1
        if end is None:
            end = i                      # 줄 끝(또는 글 끝)에서 끊는다
        out.append((m.start(), end))
        at = max(end, m.start() + 1)


def count_q(body):
    return len(q_spans(body))


# 「검토」 — 사람이 직접 보고 보증했는가. 사람만 적을 수 있는 사실이라
# 내용 완성도(확인 필요 수)와 따로 둔다. 둘을 한 숫자로 뭉치면
# 「덜 채워졌다」와 「아무도 안 봤다」가 구분되지 않는다.
REVIEW_ROW = re.compile(r'^\|\s*검토\s*\|(.*?)\|\s*$', re.M)


def review_of(body):
    """검토한 사람·날짜. 칸이 없거나 아직 아무도 안 봤으면 None."""
    m = REVIEW_ROW.search(body or '')
    if not m:
        return None
    v = m.group(1).strip()
    if not v or v == '미검토' or v.startswith('(확인'):
        return None
    return v


def has_review(body):
    """검토 칸을 쓰는 레코드인가 (칸이 아예 없는 카테고리와 가르기 위해)."""
    return bool(REVIEW_ROW.search(body or ''))


# K: 는 네트워크 드라이브라 파일을 열 때마다 느리다.
# (경로, 수정시각) 이 그대로면 다시 읽지 않는다.
_CACHE = {}
_CACHE_LOCK = threading.Lock()

# K: 는 EFSS 가상 드라이브(`\\localhost\EFSSRedirector`)다. 멀쩡한 경로인데도
# 순간적으로 튕긴다 — 2026-10-19 16:04 에 목록 읽기가 [WinError 87] 로 한 번
# 실패했다. 그 한 번이 45초 목록 갱신에 걸리면 사용자는 레코드가 사라진
# 화면을 보고, 저장에 걸리면 고친 것을 잃는다. 그래서 몇 번 더 두드린다.
#
# 「없다」·「이미 있다」·「권한이 없다」는 다시 물어도 같은 답이다. 그런 것은
# 바로 올려 보낸다. 특히 FileExistsError 를 다시 두드리면 잠금 경쟁이
# 깨진다 — 남이 쥔 자리를 내가 쥐었다고 볼 수 있다.
K_SAME = (FileNotFoundError, FileExistsError, NotADirectoryError,
          IsADirectoryError, PermissionError)
K_TRIES = 3
K_WAIT = 0.3


def kd(fn, *a, **kw):
    """K: 를 만지는 호출 하나. 순간적인 실패는 다시 두드린다."""
    for i in range(1, K_TRIES + 1):
        try:
            return fn(*a, **kw)
        except K_SAME:
            raise
        except OSError as e:
            nm = getattr(fn, '__name__', str(fn))
            if i == K_TRIES:
                print('  ** K드라이브 %s 가 %d번 다 실패했습니다: %s' % (nm, K_TRIES, e))
                raise
            print('  K드라이브 %s 가 튕겼습니다 (%s). %.1f초 뒤 다시 봅니다.'
                  % (nm, e, K_WAIT * i))
            time.sleep(K_WAIT * i)


def _read_text(fp):
    with open(fp, encoding='utf-8') as f:
        return f.read()


def _write_text(fp, text):
    # 통째로 덮어쓴다. 두 번 써도 결과가 같으니 다시 두드려도 된다.
    with open(fp, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def kd_read(fp):
    return kd(_read_text, fp)


def kd_write(fp, text):
    return kd(_write_text, fp, text)


def read_cached(fp):
    try:
        mt = kd(os.path.getmtime, fp)
    except OSError:
        return None, None
    with _CACHE_LOCK:
        hit = _CACHE.get(fp)
    if hit and hit[0] == mt:
        return hit[1], mt
    body = kd_read(fp)
    with _CACHE_LOCK:
        _CACHE[fp] = (mt, body)
    return body, mt


def parse(body):
    """md 를 줄 단위로 들여다본다.

    원본 줄 목록(`lines`)을 그대로 들고 있고, 필드와 섹션은 거기에 대한
    줄 번호로만 가리킨다. 저장할 때는 고친 자리만 바꿔 끼우므로
    손대지 않은 줄은 한 글자도 달라지지 않는다.
    """
    lines = body.split('\n')
    title = ''
    i = 0
    if lines and lines[0].startswith('# '):
        title = lines[0][2:].strip()
        i = 1
    pre_lo = i
    while i < len(lines) and not lines[i].startswith('## '):
        i += 1
    pre_hi = i

    secs = []
    for n, ln in enumerate(lines):
        if n >= pre_hi and ln.startswith('## '):
            if secs:
                secs[-1]['hi'] = n
            secs.append({'head': ln[3:].strip(), 'lo': n + 1, 'hi': len(lines)})

    # 정보표 — 정확히 2열이고 머리가 「항목 | 내용」인 표만 칸 편집 대상으로 본다.
    # 04_회사정보처럼 열이 더 많은 표는 건드리지 않고 본문으로 둔다.
    fields, info_idx, tbl = [], None, None
    for n, s in enumerate(secs):
        rows = [k for k in range(s['lo'], s['hi'])
                if lines[k].startswith('|')
                and set(lines[k].replace('|', '').replace('-', '').replace(':', '').strip())]
        if (len(rows) >= 2 and lines[rows[0]].count('|') == 3
                and lines[rows[0]].split('|')[1].strip() == '항목'):
            info_idx = n
            # 표는 파이프 줄이 끊기지 않고 이어지는 데까지다.
            # 섹션의 마지막 파이프 줄까지 잡으면 그 사이의 메모나 두 번째 표를
            # 저장할 때 통째로 지워 버린다.
            lo = hi = rows[0]
            while hi + 1 < s['hi'] and lines[hi + 1].startswith('|'):
                hi += 1
            for k in range(lo + 1, hi + 1):
                if lines[k].count('|') != 3:
                    continue
                # 구분선은 `-` 가 있어야 구분선이다. 이걸 안 보면 빈 행 `|  |  |` 도
                # 구분선으로 보고 버려서, 격자로 만든 빈 줄이 파일에 남은 채
                # 아무 검사에도 안 걸린다.
                bare = lines[k].replace('|', '').replace(':', '').strip()
                if bare and not bare.strip('-'):
                    continue        # 구분선
                p = lines[k].split('|')
                fields.append({'label': p[1].strip(), 'value': p[2].strip(), 'line': k})
            tbl = (lo, hi)
            break

    out = []
    for n, s in enumerate(secs):
        out.append({'head': s['head'], 'lo': s['lo'], 'hi': s['hi'],
                    'body': '\n'.join(lines[s['lo']:s['hi']]).strip('\n'),
                    'is_info': n == info_idx})
    return {'title': title, 'lines': lines,
            'pre': '\n'.join(lines[pre_lo:pre_hi]).strip('\n'),
            'fields': fields, 'secs': out, 'tbl': tbl}


def assemble(p, fields=None, secs=None):
    """원본 줄을 바탕으로, 바뀐 자리만 갈아 끼운다."""
    lines = list(p['lines'])
    edits = []  # (lo, hi, 새 줄들)

    if fields is not None and p['tbl']:
        lo, hi = p['tbl']
        rows = [lines[lo], lines[lo + 1]] if lines[lo + 1].lstrip().startswith('|-') \
            else [lines[lo], '|---|---|']
        for f in fields:
            v = f['value']
            rows.append('| %s | %s |' % (f['label'], v) if v else '| %s | |' % f['label'])
        edits.append((lo, hi, rows))

    if secs is not None:
        # 이름이 아니라 순서로 짝지운다 — 같은 이름의 섹션이 둘이어도 엉키지 않는다
        for s in secs:
            i = s.get('i')
            o = p['secs'][i] if isinstance(i, int) and 0 <= i < len(p['secs']) else None
            if o is None or o['head'] != s['head'] or o['is_info']:
                continue
            old = '\n'.join(lines[o['lo']:o['hi']])
            lead = len(old) - len(old.lstrip('\n'))
            trail = len(old) - len(old.rstrip('\n'))
            new = ('\n' * lead) + s['body'].strip('\n') + ('\n' * trail)
            edits.append((o['lo'], o['hi'] - 1, new.split('\n')))

    for lo, hi, new in sorted(edits, key=lambda e: -e[0]):
        lines[lo:hi + 1] = new
    return '\n'.join(lines).rstrip('\n') + '\n'


# ─────────────────────────── 검사 ───────────────────────────

def validate(cat, p, text):
    errs, warns = [], []

    for f in p['fields']:
        v = f['value']
        if '\n' in v or '\r' in v:
            errs.append('필드 「%s」 값에 줄바꿈이 있습니다. 표가 그 행부터 깨집니다.' % f['label'])
        if '|' in v:
            errs.append('필드 「%s」 값에 | 가 있습니다. 표가 깨집니다.' % f['label'])
        if re.search(r'^##\s', v) or '## ' in v:
            errs.append('필드 「%s」 값에 섹션 헤딩(## )이 들어 있습니다. '
                        '예전에 이것 때문에 레코드가 깨진 적이 있습니다.' % f['label'])
        if v.count('(') != v.count(')'):
            warns.append('필드 「%s」 의 괄호 짝이 맞지 않습니다.' % f['label'])

    req = REQUIRED.get(cat, [])
    have = {f['label'] for f in p['fields']}
    for r in req:
        if r not in have:
            errs.append('필수 필드 「%s」 가 없습니다.' % r)

    # 표 열 수 정합성 (본문 포함 전체)
    ncol, blk = None, 0
    for n, ln in enumerate(text.split('\n'), 1):
        if not ln.startswith('|'):
            ncol = None
            continue
        if not set(ln.replace('|', '').replace('-', '').replace(':', '').strip()):
            continue
        c = ln.count('|')
        if ncol is None:
            ncol, blk = c, n
        elif c != ncol:
            errs.append('%d행: 표 열 수가 %d행에서 시작한 표(%d개 칸)와 다릅니다 — %s'
                        % (n, blk, ncol - 1, ln[:50]))

    heads = [s['head'] for s in p['secs']]
    if len(heads) != len(set(heads)):
        errs.append('같은 이름의 섹션이 둘 이상입니다: %s' % heads)
    if not heads:
        errs.append('섹션이 하나도 없습니다.')

    # 정보표에 같은 항목이 두 줄이면 하나만 쓰이고 나머지는 조용히 묻힌다.
    # 격자에서 「행 추가」를 눌러 같은 이름을 치면 생기는 일이라 막는다.
    labels = [f['label'] for f in p['fields']]
    dup = sorted({x for x in labels if labels.count(x) > 1 and x})
    if dup:
        errs.append('정보표에 같은 항목이 두 번 있습니다: %s — 한 줄만 남기세요.'
                    % ', '.join(dup))
    # 격자에서 「행 추가」만 누르고 비워 둔 줄. 이름 없는 항목은 아무도 못 읽는다
    if any(not x for x in labels):
        errs.append('정보표에 항목 이름이 빈 줄이 있습니다 — 이름을 적거나 그 줄을 지우세요.')

    return errs, warns


# ─────────────────────────── 읽기 ───────────────────────────

def rec_path(cat, name, folder_mode):
    base = os.path.join(ROOT, cat)
    if folder_mode:
        g = glob.glob(os.path.join(base, name) + '/*.md')
        if g:
            return g[0]
        # 01 의 `_양식_*.md` 처럼 폴더 없이 낱개로 놓인 파일
        loose = os.path.join(base, name + '.md')
        return loose if os.path.exists(loose) else None
    return os.path.join(base, name + '.md')


def _one(cat, n, folder_mode, base):
    """레코드 한 건의 요약. K: 는 느려서 이걸 스레드로 한꺼번에 돌린다."""
    fp = rec_path(cat, n, folder_mode)
    if not fp:
        return None
    body, mt = read_cached(fp)
    if body is None:
        return None
    subs = []
    sibling = os.path.join(base, n)
    try:
        if os.path.isdir(sibling):
            for s in sorted(kd(os.listdir, sibling)):
                sp = os.path.join(sibling, s)
                if os.path.isdir(sp):
                    subs.append({'name': s, 'n': len(kd(os.listdir, sp))})
    except OSError:
        pass
    return {'name': n, 'q': count_q(body), 'tmpl': n.startswith('_'),
            'ex': '기입례' in n, 'subs': subs,
            'rev': review_of(body), 'hasrev': has_review(body),
            'path': os.path.relpath(fp, ROOT).replace('/', '\\'), 'mtime': mt}


def scan():
    from concurrent.futures import ThreadPoolExecutor
    jobs, meta = [], []
    for cat, label, folder_mode in CATS:
        base = os.path.join(ROOT, cat)
        names = []
        if os.path.isdir(base):
            if folder_mode:
                names = sorted(d for d in kd(os.listdir, base)
                               if os.path.isdir(os.path.join(base, d)))
                # 폴더 없이 놓인 양식도 레코드 목록에 올린다
                names += sorted(os.path.basename(f)[:-3]
                                for f in kd(glob.glob, base + '/_*.md'))
            else:
                names = sorted(os.path.basename(f)[:-3]
                               for f in kd(glob.glob, base + '/*.md')
                               if os.path.basename(f) != 'index.md')
        meta.append((cat, label, folder_mode, names))
        for n in names:
            jobs.append((cat, n, folder_mode, base))

    with ThreadPoolExecutor(max_workers=16) as ex:
        done = list(ex.map(lambda a: _one(*a), jobs))
    byname = {}
    for (cat, n, _, _), r in zip(jobs, done):
        if r:
            byname[(cat, n)] = r

    cats = []
    for cat, label, folder_mode, names in meta:
        recs = [byname[(cat, n)] for n in names if (cat, n) in byname]
        cats.append({'id': cat, 'label': label, 'folder': folder_mode, 'records': recs})

    tests = []
    for d in sorted(kd(os.listdir, ROOT)):
        p = os.path.join(ROOT, d)
        if d.startswith('test_') and os.path.isdir(p):
            groups = []
            for g in sorted(kd(os.listdir, p)):
                gp = os.path.join(p, g)
                if os.path.isdir(gp):
                    groups.append({'name': g,
                                   'files': sorted(x for x in kd(os.listdir, gp)
                                                   if x.endswith('.md'))})
            tests.append({'name': d, 'groups': groups})

    cm = os.path.join(ROOT, 'CLAUDE.md')
    ctext = read_cached(cm)[0]
    # 레코드와 같은 모양으로 그려 준다. 읽기 전용이라 줄 번호는 심지 않는다.
    return {'cats': cats, 'tests': tests, 'root': ROOT.replace('/', '\\'),
            'claude': ctext, 'claudeHtml': md_html(ctext) if ctext else ''}


# 스캔 결과도 들고 있는다. 「새로 읽기」 를 누르면 다시 훑는다.
_SCAN = {'data': None, 'at': 0}
_SCAN_LOCK = threading.Lock()


def scan_cached(force=False):
    with _SCAN_LOCK:
        if not force and _SCAN['data'] is not None:
            return _SCAN['data']
    try:
        d = scan()
    except OSError as e:
        # 실패한 결과로 덮으면 사용자는 레코드가 사라진 화면을 본다.
        # 멀쩡한 지난 목록이 있으면 그것을 그대로 쓴다.
        with _SCAN_LOCK:
            old = _SCAN['data']
        if old is not None:
            print('  ** 목록을 다시 훑지 못했습니다 (%s). 지난 목록을 그대로 씁니다.' % e)
            return old
        raise
    with _SCAN_LOCK:
        _SCAN['data'] = d
    return d


def load(cat, name):
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp:
        return None
    body, mt = read_cached(fp)
    if body is None:
        return None
    p = parse(body)
    p.update({'cat': cat, 'name': name, 'raw': body,
              'path': os.path.relpath(fp, ROOT).replace('/', '\\'),
              'full': os.path.join(ROOT, os.path.relpath(fp, ROOT)).replace('/', '\\'),
              'q': count_q(body), 'mtime': mt,
              'rev': review_of(body), 'hasrev': has_review(body)})
    return p


# ─────────────────────── 마크다운 렌더 ───────────────────────
# 사내망에서 CDN 이 막혀 있어도 되게, 바깥 라이브러리 없이 직접 그린다.
# 레코드가 실제로 쓰는 문법만 다룬다.

def _esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _inline(s):
    s = _esc(s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', s)
    # wiki 는 code 와 같은 모양으로 보이지만, 화면에서 고친 뒤 되돌릴 때
    # [[ ]] 인지 ` ` 인지 가려내야 해서 표시를 남긴다.
    s = re.sub(r'\[\[([^\]]+)\]\]', r'<code class="wiki">\1</code>', s)
    for lo, hi in reversed(q_spans(s)):          # 뒤에서부터 끼워야 자리가 안 밀린다
        s = s[:lo] + '<span class="q">' + s[lo:hi] + '</span>' + s[hi:]
    return s


def _row(ln):
    cells = ln.strip().strip('|').split('|')
    return [c.strip() for c in cells]


def md_html(text, anchor=False):
    """마크다운을 HTML 로.

    anchor=True 면 블록마다 원문 줄 번호를 심는다. 읽기 화면에서 그 블록을
    눌러 그 자리에서 고치고, 그 줄만 갈아 끼우기 위한 것이다.
    """
    out, lines, i = [], text.split('\n'), 0

    def wrap(html, lo, hi, kind, extra=''):
        if not anchor:
            return html
        return ('<div class="blk" data-lo="%d" data-hi="%d" data-kind="%s"%s>%s</div>'
                % (lo, hi, kind, extra, html))

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        lo = i

        if not s:
            i += 1
            continue

        if s.startswith('#'):
            n = len(s) - len(s.lstrip('#'))
            i += 1
            # 섹션 제목은 구조라 편집 대상이 아니다
            out.append(wrap('<h%d>%s</h%d>'
                            % (min(n, 6), _inline(s[n:].strip()), min(n, 6)),
                            lo, i - 1, 'head'))
            continue

        # 표
        if s.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|$', lines[i + 1].strip()):
            head = _row(s)
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                body.append(_row(lines[i].strip()))
                i += 1
            # 칸이 넷 이상인 표는 글 폭(한 줄 48자) 안에 넣으면 못 읽는다.
            # 그런 표만 글 칸 밖으로 내보낸다. 두 칸짜리 정보표는 글과 같이 둔다.
            wide = ' wide' if len(head) >= 4 else ''
            h = ['<div class="tblwrap%s"><table><thead><tr>' % wide]
            h += ['<th>%s</th>' % _inline(c) for c in head]
            h.append('</tr></thead><tbody>')
            for r in body:
                h.append('<tr>' + ''.join('<td>%s</td>' % _inline(c) for c in r) + '</tr>')
            h.append('</tbody></table></div>')
            out.append(wrap(''.join(h), lo, i - 1, 'table',
                            ' data-wide="1"' if wide else ''))
            continue

        # <!-- --> 안내 주석 — 양식이 「여기엔 뭘 쓴다」를 담아 두는 자리다.
        # 문단으로 읽으면 &lt;!-- 가 글자 그대로 나와 읽을 수가 없다.
        # 흐리게 그려만 주고 편집은 막는다.
        if s.startswith('<!--'):
            buf = []
            while i < len(lines):
                buf.append(lines[i])
                if '-->' in lines[i]:
                    i += 1
                    break
                i += 1
            inner = '\n'.join(buf)
            inner = re.sub(r'^\s*<!--', '', inner)
            inner = re.sub(r'-->\s*$', '', inner)
            rows = [x.strip() for x in inner.split('\n') if x.strip()]
            out.append(wrap('<div class="note">%s</div>'
                            % '<br>'.join(_inline(x) for x in rows),
                            lo, i - 1, 'note'))
            continue

        # ``` 로 묶은 덩어리 — 조직도처럼 줄 모양 자체가 내용이다.
        # 문단으로 보면 한 줄로 합쳐져 그림이 무너진다. 그려만 주고 편집은 막는다.
        if s.startswith('```'):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            out.append(wrap('<pre><code>%s</code></pre>' % _esc('\n'.join(buf)),
                            lo, i - 1, 'code'))
            continue

        if s.startswith('>'):
            buf = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip().lstrip('>').strip())
                i += 1
            # 「출처」 줄은 근거라 편집기에서 손대지 않는다
            out.append(wrap('<blockquote>%s</blockquote>' % _inline(' '.join(buf)),
                            lo, i - 1, 'src'))
            continue

        # 목록 — 들여쓰기로 깊이를 잡는다
        if re.match(r'^[-*·•]\s', s):
            items, depth, gap = [], [], False
            while i < len(lines):
                cur = lines[i]
                cs = cur.strip()
                if not cs:
                    if (i + 1 < len(lines) and re.match(r'^\s*[-*·•]\s', lines[i + 1])):
                        gap = True       # 항목 사이를 한 줄 띄워 둔 것을 기억한다
                        i += 1
                        continue
                    break
                if not re.match(r'^\s*[-*·•]\s', cur):
                    # 제목·표·인용은 목록의 보조 줄이 아니다. 빈 줄 없이 이어져도
                    # 삼키면 그 섹션이 통째로 사라진 것처럼 보인다.
                    if re.match(r'^\s*(#{1,6}\s|\||>)', cur):
                        break
                    if items:
                        items[-1][1].append(cs)
                        items[-1][3].append(len(cur) - len(cur.lstrip()))
                        i += 1
                        continue
                    break
                ind = len(cur) - len(cur.lstrip())
                # 글머리 기호와 들여쓴 칸 수를 적어 둔다. 화면에서 고친 뒤
                # 되돌릴 때 원래 모습 그대로 써야 해서다 (· 를 - 로 바꾸면
                # 손대지 않은 줄까지 달라져 버린다).
                items.append([ind, [re.sub(r'^[-*·•]\s+', '', cs)], cs[0], [], gap])
                gap = False
                i += 1
            lvl, h = [], []
            for ind, parts, mk, cinds, gap1 in items:
                while lvl and ind < lvl[-1]:
                    h.append('</ul>')
                    lvl.pop()
                if not lvl or ind > lvl[-1]:
                    h.append('<ul>')
                    lvl.append(ind)
                first = _inline(parts[0])
                rest = ''.join('<div class="sub" data-ind="%d">%s</div>'
                               % (cinds[k], _inline(x))
                               for k, x in enumerate(parts[1:]))
                h.append('<li data-ind="%d" data-mk="%s"%s>%s%s</li>'
                         % (ind, mk, ' data-gap="1"' if gap1 else '', first, rest))
            h += ['</ul>'] * len(lvl)
            # 목록 끝의 빈 줄은 블록 범위에 넣지 않는다
            hi = i - 1
            while hi > lo and not lines[hi].strip():
                hi -= 1
            out.append(wrap(''.join(h), lo, hi, 'list'))
            continue

        buf, pind = [], len(ln) - len(ln.lstrip())
        while i < len(lines) and lines[i].strip() and not re.match(
                r'^\s*([-*·•]\s|\||>|#)', lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append(wrap('<p data-ind="%d">%s</p>' % (pind, _inline(' '.join(buf))),
                            lo, i - 1, 'para'))
        else:
            i += 1
    return '\n'.join(out)


def save_block(cat, name, payload, who=None):
    """블록 하나(줄 구간)만 갈아 끼운다. 읽기 화면에서 그 자리 편집용."""
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp or not os.path.exists(fp):
        return {'ok': False, 'errors': ['레코드 파일을 찾지 못했습니다.']}

    who = clean_who(who or payload.get('who'))
    held = lock_read(cat, name, who)
    if not held or not held['mine']:
        return {'ok': False, 'lock': held, 'errors': [
            '편집 잠금이 없습니다. %s' % ('지금은 %s 님이 편집 중입니다.' % held['who']
                                   if held else '「수정하기」를 다시 눌러 주세요.')]}

    if payload.get('mtime') and abs(os.path.getmtime(fp) - payload['mtime']) > 1:
        return {'ok': False, 'stale': True, 'errors': [
            '이 파일이 편집기 밖에서 바뀌었습니다. 새로 고쳐 다시 여세요.']}

    body = read_cached(fp)[0]
    lines = body.split('\n')
    lo, hi = int(payload['lo']), int(payload['hi'])
    if not (0 <= lo <= hi < len(lines)):
        return {'ok': False, 'errors': ['고칠 위치를 찾지 못했습니다. 새로 고쳐 다시 여세요.']}

    new = str(payload.get('text', '')).replace('\r\n', '\n').split('\n')
    while new and not new[-1].strip():
        new.pop()
    if not new:
        return {'ok': False, 'errors': ['내용이 비었습니다. 지우려면 줄을 남겨 두세요.']}

    # 섹션 제목을 새로 만들어 구조를 흔드는 것은 막는다
    for l in new:
        if l.startswith('## '):
            return {'ok': False, 'errors': [
                '본문에 섹션 제목(## )을 넣을 수 없습니다. 구조가 흔들립니다.']}

    lines[lo:hi + 1] = new
    text = '\n'.join(lines).rstrip('\n') + '\n'

    p = parse(text)
    errs, warns = validate(cat, p, text)
    if errs:
        return {'ok': False, 'errors': errs, 'warnings': warns}

    bk = backup(fp, cat, name)
    kd_write(fp, text)
    with _CACHE_LOCK:
        _CACHE[fp] = (os.path.getmtime(fp), text)
    # 정보표를 고쳤으면 index.md 의 같은 칸도 맞춘다.
    # 통째로 저장하는 save() 에만 있어서, 격자로 담당자를 고쳐도
    # index 는 옛 값을 그대로 들고 있었다.
    idx = sync_index(cat, name, p['fields']) if p['tbl'] else []
    scan_cached(True)
    return {'ok': True, 'warnings': warns, 'backup': os.path.relpath(bk, HERE),
            'index': idx, 'mtime': os.path.getmtime(fp), 'q': count_q(text)}


# ─────────────────────────── 잠금 ───────────────────────────
# 여러 사람이 각자 PC 에서 같은 K: 를 본다. 그래서 잠금은 공유 폴더에 둔다.
# 레코드마다 파일 하나. 만들기(O_EXCL)가 원자적이라 동시에 둘이 못 잡는다.

LOCK_DIR = '_편집중'
LOCK_TTL = 15 * 60      # 이 시간 동안 신호가 없으면 끊긴 잠금으로 본다

# 편집자 이름은 접속자가 보낸다. 서버 계정이 아니다 —
# 서버 한 대에 여러 명이 붙으면 서버 계정은 모두 같아서 잠금이 무의미해진다.
# 이 이름은 본인이 적는 것이라 신원 증명이 아니다. "누가 보고 있는지" 를 알리는 용도다.
def clean_who(s):
    s = (s or '').strip()
    s = re.sub(r'[\x00-\x1f|/\\]', '', s)[:40]
    return s or '이름 없음'


def _lock_path(cat, name):
    d = os.path.join(ROOT, LOCK_DIR)
    key = re.sub(r'[^0-9A-Za-z가-힣._-]', '_', '%s__%s' % (cat, name))
    return d, os.path.join(d, key + '.json')


def lock_read(cat, name, who=None):
    _, fp = _lock_path(cat, name)
    try:
        d = json.loads(kd_read(fp))
    except Exception:
        return None
    age = time.time() - d.get('beat', 0)
    d['age'] = int(age)
    d['stale'] = age > LOCK_TTL
    d['mine'] = bool(who) and d.get('who') == who
    return d


def lock_take(cat, name, who, steal=False):
    who = clean_who(who)
    d, fp = _lock_path(cat, name)
    os.makedirs(d, exist_ok=True)
    cur = lock_read(cat, name, who)
    if cur and not cur['mine'] and not cur['stale'] and not steal:
        return {'ok': False, 'lock': cur}
    rec = {'who': who, 'at': time.time(), 'beat': time.time(),
           'cat': cat, 'name': name}
    blob = json.dumps(rec, ensure_ascii=False).encode('utf-8')

    # 끊긴 잠금을 가져올 때도 그냥 덮어쓰면, 둘이 동시에 가져갈 경우
    # 둘 다 "내 것" 으로 믿고 편집에 들어간다. 지우고 O_EXCL 로 다시 만들어
    # 한 사람만 이기게 한다.
    if cur:
        try:
            os.remove(fp)
        except OSError:
            pass
    try:
        fd = os.open(fp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        again = lock_read(cat, name, who)
        if again and not again['mine']:
            return {'ok': False, 'lock': again}   # 경쟁에서 졌다
        return {'ok': True, 'lock': again}
    with os.fdopen(fd, 'wb') as f:
        f.write(blob)

    # 쓴 뒤 다시 읽어 내가 주인인지 확인한다
    back = lock_read(cat, name, who)
    if not back or not back['mine']:
        return {'ok': False, 'lock': back}
    return {'ok': True, 'lock': back}


def lock_beat(cat, name, who):
    who = clean_who(who)
    cur = lock_read(cat, name, who)
    if not cur or not cur['mine']:
        return {'ok': False, 'lock': cur}
    _, fp = _lock_path(cat, name)
    cur2 = {k: cur[k] for k in ('who', 'at', 'cat', 'name') if k in cur}
    cur2['beat'] = time.time()
    kd_write(fp, json.dumps(cur2, ensure_ascii=False))
    return {'ok': True, 'lock': lock_read(cat, name, who)}


def lock_free(cat, name, who, force=False):
    who = clean_who(who)
    cur = lock_read(cat, name, who)
    if cur and not cur['mine'] and not cur['stale'] and not force:
        return {'ok': False, 'lock': cur}
    _, fp = _lock_path(cat, name)
    try:
        os.remove(fp)
    except OSError:
        pass
    return {'ok': True, 'lock': None}


def locks_all(who=None):
    d = os.path.join(ROOT, LOCK_DIR)
    out = {}
    if not os.path.isdir(d):
        return out
    for f in sorted(kd(os.listdir, d)):
        if not f.endswith('.json'):
            continue
        try:
            r = json.loads(kd_read(os.path.join(d, f)))
        except Exception:
            continue
        age = time.time() - r.get('beat', 0)
        if age > LOCK_TTL:
            continue
        out['%s/%s' % (r.get('cat'), r.get('name'))] = {
            'who': r.get('who'), 'age': int(age), 'mine': r.get('who') == who}
    return out


# ─────────────────────────── 쓰기 ───────────────────────────

def backup(fp, cat, name):
    d = os.path.join(BACKUP, cat, re.sub(r'[^0-9A-Za-z가-힣._-]', '_', name))
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(d, stamp + '.md')
    # 초 단위라 같은 초에 두 번 저장하면 직전 백업을 덮는다. 번호를 붙인다.
    k = 1
    while os.path.exists(dst):
        dst = os.path.join(d, '%s-%d.md' % (stamp, k))
        k += 1
    kd(shutil.copy2, fp, dst)
    keep = sorted(glob.glob(d + '/*.md'))
    for old in keep[:-30]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dst


INDEX_COL = {
    '02_보유자산': {'기준 시점': 2, '담당자': -1},
    '03_수행체계': {'기준 시점': 2},
}


def sync_index(cat, name, fields):
    """index.md 의 해당 행에서 레코드와 어긋난 칸을 맞춘다."""
    cols = INDEX_COL.get(cat)
    if not cols:
        return []
    ip = os.path.join(ROOT, cat, 'index.md')
    if not os.path.exists(ip):
        return []
    t = open(ip, encoding='utf-8').read()
    lines = t.split('\n')
    fv = {f['label']: f['value'] for f in fields}
    changed = []
    for i, ln in enumerate(lines):
        if not ln.startswith('| ') or '---' in ln:
            continue
        cells = ln.split(' | ')
        if cells[0][2:].strip() != name:
            continue
        for label, ci in cols.items():
            if label not in fv:
                continue
            new = fv[label]
            # 레코드 쪽 긴 꼬리표는 index 에서 짧게
            new = re.sub(r' \(모판·목록 두 자료가 다름\)$', '', new)
            # index 칸은 사람이 줄여 쓴 요약이다. 잘라서 덮으면 그 요약이 망가지므로
            # 통째로 들어가는 짧은 값일 때만 맞춘다.
            if len(new) > 46:
                continue
            old = cells[ci].rstrip('|').strip()
            if old != new:
                cells[ci] = new + (' |' if ci == -1 else '')
                changed.append('%s: %s → %s' % (label, old[:28], new[:28]))
        if changed:
            lines[i] = ' | '.join(cells)
        break
    if changed:
        backup(ip, cat, 'index')  # 공유 폴더에 .bak 을 남기지 않는다
        open(ip, 'w', encoding='utf-8', newline='').write('\n'.join(lines))
        with _CACHE_LOCK:
            _CACHE.pop(ip, None)
    return changed


def save(cat, name, payload, who=None):
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp or not os.path.exists(fp):
        return {'ok': False, 'errors': ['레코드 파일을 찾지 못했습니다.']}

    who = clean_who(who or payload.get('who'))
    held = lock_read(cat, name, who)
    if not held or not held['mine']:
        return {'ok': False, 'lock': held, 'errors': [
            '편집 잠금이 없습니다. %s' % ('지금은 %s 님이 편집 중입니다.' % held['who']
                                   if held else '「수정하기」를 다시 눌러 주세요.')]}

    disk = os.path.getmtime(fp)
    if payload.get('mtime') and abs(disk - payload['mtime']) > 1:
        return {'ok': False, 'stale': True, 'errors': [
            '이 파일이 편집기 밖에서 바뀌었습니다. 새로 고쳐 다시 여세요. '
            '(지금 덮어쓰면 그 변경이 사라집니다)']}

    cur = parse(read_cached(fp)[0])
    new_fields = payload.get('fields') or cur['fields']
    text = assemble(cur, fields=new_fields, secs=payload.get('secs') or None)
    chk = dict(cur)
    chk['fields'] = new_fields
    errs, warns = validate(cat, chk, text)
    if errs:
        return {'ok': False, 'errors': errs, 'warnings': warns}

    bk = backup(fp, cat, name)
    kd_write(fp, text)
    with _CACHE_LOCK:
        _CACHE[fp] = (os.path.getmtime(fp), text)
    idx = sync_index(cat, name, new_fields)
    scan_cached(True)
    lock_free(cat, name, who)
    return {'ok': True, 'warnings': warns, 'backup': os.path.relpath(bk, HERE),
            'index': idx, 'mtime': os.path.getmtime(fp), 'q': count_q(text)}


# ──────────────────── 레코드 만들기·지우기 ────────────────────

# 윈도우에서 파일 이름에 못 쓰는 글자 + 폴더를 거슬러 올라가는 수단
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
TRASH = '_지운것'


def check_name(name):
    """레코드 이름으로 쓸 수 있는지 본다. 경로를 벗어나는 이름을 막는다."""
    name = (name or '').strip()
    if not name:
        return None, '이름을 적어 주세요.'
    if len(name) > 80:
        return None, '이름이 너무 깁니다 (80자까지).'
    if BAD_NAME.search(name):
        return None, '이름에 \\ / : * ? " < > | 는 쓸 수 없습니다.'
    if name.startswith('_') or name.startswith('.'):
        return None, '_ 나 . 로 시작하는 이름은 쓸 수 없습니다 (양식·숨김용).'
    if name in ('.', '..') or '..' in name:
        return None, '쓸 수 없는 이름입니다.'
    if name == 'index':
        return None, 'index 는 목록 파일 이름이라 쓸 수 없습니다.'
    return name, None


def blank_record(cat, name):
    """양식이 없는 카테고리(04·05·06)에 쓸 최소 골격."""
    label = dict((c, l) for c, l, _ in CATS)[cat]
    head = REQUIRED.get(cat) or ['%s명' % label]
    rows = '\n'.join('| %s | (확인 필요) |' % f for f in head)
    return ('# %s\n\n## %s 정보\n\n| 항목 | 내용 |\n|---|---|\n%s\n\n'
            '## 내용\n- (확인 필요)\n' % (name, label, rows))


def add_missing_fields(cat, text):
    """양식에 빠져 있는 필수 필드를 정보표에 채워 넣는다.

    02 양식에는 「소속 오퍼링·담당자」가, 03 에는 「담당자」가 없다.
    그대로 만들면 다 채우고 저장할 때 「필수 필드가 없습니다」로 막힌다.
    """
    need = REQUIRED.get(cat)
    if not need:
        return text
    p = parse(text)
    if not p['tbl']:
        return text
    have = [f['label'] for f in p['fields']]
    miss = [x for x in need if x not in have]
    if not miss:
        return text
    lines = list(p['lines'])
    at = p['tbl'][1] + 1                      # 표의 마지막 줄 바로 뒤
    lines[at:at] = ['| %s | (확인 필요) |' % x for x in miss]
    return '\n'.join(lines).rstrip('\n') + '\n'


def new_record(cat, name, who):
    """레코드를 새로 만든다. 그 카테고리의 양식을 그대로 복사해 쓴다."""
    if cat not in dict((c, f) for c, _, f in CATS):
        return {'ok': False, 'errors': ['그런 카테고리가 없습니다.']}
    name, err = check_name(name)
    if err:
        return {'ok': False, 'errors': [err]}
    fm = dict((c, f) for c, _, f in CATS)[cat]
    # rec_path 는 폴더 방식이 아닐 때 있든 없든 경로를 돌려준다. 있는지는 따로 본다
    cur = rec_path(cat, name, fm)
    if cur and os.path.exists(cur):
        return {'ok': False, 'errors': ['같은 이름의 레코드가 이미 있습니다.']}

    base = os.path.join(ROOT, cat)
    label = dict((c, l) for c, l, _ in CATS)[cat]
    if fm:
        d = os.path.join(base, name)
        if os.path.exists(d):
            return {'ok': False, 'errors': ['같은 이름의 폴더가 이미 있습니다.']}
        os.makedirs(d)
        for s in SUBDIRS.get(cat, []):
            os.makedirs(os.path.join(d, s), exist_ok=True)
        fp = os.path.join(d, '%s_%s.md' % (label, name))
    else:
        fp = os.path.join(base, name + '.md')
        if os.path.exists(fp):
            return {'ok': False, 'errors': ['같은 이름의 파일이 이미 있습니다.']}

    tpl = os.path.join(base, '_양식_%s.md' % label)
    if os.path.exists(tpl):
        text = open(tpl, encoding='utf-8').read()
        # 양식의 첫 제목을 이 레코드 이름으로 바꾼다.
        # 폴더 방식은 파일명(`사업실적_…`·`실주사업_…`)을 그대로 쓴다. 파일 하나만
        # 떼어 봐도 무엇인지 알아야 한다 — 특히 07 은 실적으로 읽히면 안 된다.
        head = os.path.basename(fp)[:-3] if fm else name
        text = re.sub(r'^#\s+.*$', '# ' + head, text, count=1, flags=re.M)
        # 양식은 표의 값 칸을 비워 둔다. 그대로 두면 아무것도 안 채웠는데도
        # 트리 배지가 ✓(다 찼음)로 보인다. 빈 칸은 (확인 필요)로 표시해 둔다.
        text = re.sub(r'^(\|[^|\n]+\|)\s*\|\s*$', r'\1 (확인 필요) |', text, flags=re.M)
        text = add_missing_fields(cat, text)
    else:
        text = blank_record(cat, name)
    kd_write(fp, text)
    scan_cached(True)
    return {'ok': True, 'cat': cat, 'name': name,
            'path': os.path.relpath(fp, ROOT), 'q': count_q(text)}


def set_review(cat, name, who, clear=False):
    """「검토」 칸에 이름과 날짜를 적는다 (또는 지운다).

    버튼 한 번으로 끝나야 꼴이 흐트러지지 않는다. 손으로 치면 사람마다
    「노숙진」, 「노숙진 10/16」, 「10월 16일 확인」 처럼 제각각이 된다.
    """
    if cat not in dict((c, f) for c, _, f in CATS):
        return {'ok': False, 'errors': ['그런 카테고리가 없습니다.']}
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp or not os.path.exists(fp):
        return {'ok': False, 'errors': ['레코드를 찾지 못했습니다.']}

    who = clean_who(who)
    if who == '이름 없음':
        return {'ok': False,
                'errors': ['이름을 먼저 적어 주세요. 누가 봤는지가 검토의 전부입니다.']}

    cur = lock_read(cat, name, who)
    if cur and not cur['mine'] and not cur['stale']:
        return {'ok': False, 'lock': cur,
                'errors': ['%s 님이 편집 중이라 지금은 바꿀 수 없습니다.' % cur['who']]}

    body, _ = read_cached(fp)
    if body is None:
        return {'ok': False, 'errors': ['레코드를 읽지 못했습니다.']}
    lines = body.split('\n')
    at = next((i for i, l in enumerate(lines) if REVIEW_ROW.match(l)), None)
    if at is None:
        return {'ok': False, 'errors': ['이 레코드에는 검토 칸이 없습니다.']}

    val = '미검토' if clear else '%s %s' % (
        who, datetime.date.today().strftime('%Y-%m-%d'))
    row = '| 검토 | %s |' % val
    text = body
    if lines[at] != row:
        backup(fp, cat, name)
        lines[at] = row
        text = '\n'.join(lines)
        kd_write(fp, text)
        with _CACHE_LOCK:
            _CACHE[fp] = (os.path.getmtime(fp), text)
        scan_cached(True)
    return {'ok': True, 'rev': review_of(text), 'q': count_q(text),
            'mtime': os.path.getmtime(fp)}


def del_record(cat, name, who):
    """레코드를 지운다 — 정확히는 `_지운것` 으로 옮긴다.

    되돌릴 길이 없으면 안 된다. `_백업` 은 고치기 전 원본만 남겨서,
    통째로 지운 레코드는 거기서 찾을 수 없다.
    """
    if cat not in dict((c, f) for c, _, f in CATS):
        return {'ok': False, 'errors': ['그런 카테고리가 없습니다.']}
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp or not os.path.exists(fp):
        return {'ok': False, 'errors': ['레코드를 찾지 못했습니다.']}
    if name.startswith('_'):
        return {'ok': False, 'errors': ['양식은 지울 수 없습니다.']}

    cur = lock_read(cat, name, who)
    if cur and not cur['mine'] and not cur['stale']:
        return {'ok': False, 'lock': cur,
                'errors': ['%s 님이 편집 중이라 지울 수 없습니다.' % cur['who']]}

    # 폴더 방식(01)이면 폴더째, 아니면 파일만 옮긴다
    src = os.path.dirname(fp) if (fm and os.path.basename(os.path.dirname(fp)) == name) else fp
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    d = os.path.join(ROOT, TRASH, cat)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, '%s_%s' % (stamp, name))
    if not os.path.isdir(src):
        dst += '.md'
    shutil.move(src, dst)

    with _CACHE_LOCK:
        _CACHE.pop(fp, None)
    lock_free(cat, name, who, force=True)
    scan_cached(True)
    return {'ok': True, 'cat': cat, 'name': name,
            'trash': os.path.relpath(dst, ROOT)}


# ─────────────────────── 자동 갱신 ───────────────────────
# 아무도 손대지 않아도 최신 코드로 따라가게 한다.
# 서버가 주기적으로 git 으로 당겨 받고, 받았으면 스스로 다시 뜬다.
# 열어 둔 화면은 버전이 바뀐 것을 보고 알아서 새로 고친다.

RESTART = {'want': False, 'srv': None}
RESTART_CODE = 7        # 지킴이에게 「새 코드로 다시 띄워 달라」고 알리는 값
# 사내 프록시를 거치면 GitHub 응답이 몇 분씩 걸린다. 짧게 잡으면
# 자동 갱신이 매번 타임아웃으로 조용히 건너뛰어, 영영 최신이 되지 않는다.
GIT_TIMEOUT = 300
_UPD = {'fail': 0}


def version():
    """화면 파일이 바뀌면 값이 바뀐다. 브라우저가 이걸로 갱신을 안다."""
    h = hashlib.sha1()
    for f in ('app.html', 'server.py'):
        p = os.path.join(HERE, f)
        try:
            h.update(('%s:%s' % (f, os.path.getmtime(p))).encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


# pythonw 로 띄우면 이 프로세스에는 콘솔이 없다. 그 상태에서 git 같은
# 콘솔 프로그램을 부르면 윈도우가 **검은 창을 새로 띄운다.**
# 자동 갱신이 10분마다 도니 10분마다 창이 깜빡였다.
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def _git(*args, timeout=None):
    return subprocess.run(['git'] + list(args), cwd=HERE,
                          timeout=timeout or GIT_TIMEOUT,
                          creationflags=NO_WINDOW,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def update_once():
    """새 커밋이 있으면 당겨 받는다. 받았으면 True."""
    if not os.path.isdir(os.path.join(HERE, '.git')):
        return False
    try:
        if not _git('remote').stdout.strip():
            return False
        if _git('fetch', '--quiet').returncode != 0:
            raise RuntimeError('git fetch 실패')
        br = _git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip() or 'HEAD'
        up = _git('rev-parse', '--abbrev-ref', '@{u}').stdout.strip()
        if not up:
            return False
        local = _git('rev-parse', 'HEAD').stdout.strip()
        remote = _git('rev-parse', up).stdout.strip()
        if not local or local == remote:
            return False
        # 원격이 이미 우리 조상이면 받을 것이 없다 — 아직 못 올린 커밋이
        # 있을 뿐이다. 이걸 안 보면 「다르다」는 이유로 10분마다 다시 뜬다.
        if _git('merge-base', '--is-ancestor', remote, 'HEAD').returncode == 0:
            return False
        if _git('status', '--porcelain').stdout.strip():
            print('  [자동 갱신] 고쳐 둔 파일이 있어 건너뜁니다.')
            return False
        r = _git('pull', '--ff-only', '--quiet')
        if r.returncode != 0:
            print('  [자동 갱신] 당기지 못했습니다: %s' % (r.stderr or '').strip()[:120])
            return False
        print('  [자동 갱신] %s → %s 로 올렸습니다.' % (local[:7], remote[:7]))
        _UPD['fail'] = 0
        return True
    except subprocess.TimeoutExpired:
        _UPD['fail'] += 1
        print('  [자동 갱신] GitHub 응답이 %d초 안에 안 왔습니다 (연속 %d회).'
              % (GIT_TIMEOUT, _UPD['fail']))
        if _UPD['fail'] == 3:
            print('  ** 자동 갱신이 계속 실패합니다. 프록시나 자격증명을 확인하세요.')
            print('     --git-timeout 으로 더 늘리거나, --no-auto-update 로 끄고 직접 받으세요.')
        return False
    except Exception as e:
        _UPD['fail'] += 1
        print('  [자동 갱신] 건너뜀 (연속 %d회): %s' % (_UPD['fail'], e))
        if _UPD['fail'] == 3:
            print('  ** 자동 갱신이 계속 실패합니다. 서버에서 git pull 이 되는지 확인하세요.')
        return False


def updater(every):
    blocked = 0
    while True:
        time.sleep(every)
        try:
            held = locks_all()
            if held:
                # 누가 편집 중이면 다음 차례에. 다만 편집 화면을 열어 둔 채
                # 자리를 비우면 하트비트가 잠금을 계속 살려서 갱신이 영영 막힌다.
                # 한 시간 넘게 막히면 누가 잡고 있는지 알린다.
                blocked += every
                if blocked >= 3600:
                    who = ', '.join(sorted({v.get('who') or '?' for v in held.values()}))
                    print('  ** 자동 갱신이 %d분째 막혀 있습니다 — %s 님이 편집 중입니다.'
                          % (blocked // 60, who))
                    print('     편집 화면을 열어 둔 채 자리를 비운 것일 수 있습니다.')
                    blocked = 0
                continue
            blocked = 0
            if update_once():
                print('  [자동 갱신] 새 코드로 다시 뜹니다.')
                RESTART['want'] = True
                if RESTART['srv']:
                    threading.Thread(target=RESTART['srv'].shutdown, daemon=True).start()
                return
        except Exception:
            pass


# ─────────────────────────── HTTP ───────────────────────────

SAVE_LOCK = threading.Lock()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # 브라우저가 탭을 닫으면 끊긴 연결로 보인다. 서버 문제가 아니라
        # 매번 통째로 찍히면 진짜 오류를 가린다.
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)
    # Windows 에서는 이걸 켜 두면 이미 떠 있는 편집기가 있어도 또 뜬다.
    # 그러면 요청이 옛 서버로 가 버려서, 고친 게 반영 안 된 것처럼 보인다.
    allow_reuse_address = False


class H(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        # send_error 는 여기에 HTTPStatus 를 넘긴다. 문자열로 단정하면
        # 404 가 날 때마다 TypeError 로 응답 없이 연결이 끊긴다.
        first = args[0] if args else ''
        if isinstance(first, str) and '/api/' in first:
            sys.stderr.write('  %s\n' % first)

    def who(self):
        import urllib.parse as _u
        raw = self.headers.get('X-Editor-Who') or ''
        try:
            raw = _u.unquote(raw)
        except Exception:
            pass
        return clean_who(raw)

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def end_headers(self):
        # 서버를 새 버전으로 올렸는데 브라우저가 옛 화면을 들고 있으면
        # 고친 게 반영 안 된 것처럼 보인다. 화면 파일은 캐시하지 않는다.
        if self.path.split('?')[0] in ('/', '/app.html'):
            self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs, unquote
        u = urlparse(self.path)
        if u.path == '/':
            self.path = '/app.html'
            return super().do_GET()
        if u.path == '/api/tree':
            try:
                force = parse_qs(u.query).get('force', ['0'])[0] == '1'
                return self._json(scan_cached(force))
            except Exception as e:
                return self._json({'error': str(e)}, 500)
        if u.path == '/api/record':
            try:
                q = parse_qs(u.query)
                cat = unquote(q.get('cat', [''])[0])
                name = unquote(q.get('name', [''])[0])
                if cat not in dict((c, f) for c, _, f in CATS):
                    return self._json({'error': '없는 카테고리: %s' % cat}, 404)
                r = load(cat, name)
                if r:
                    r['html'] = md_html(r['raw'], anchor=True)
                    r['lock'] = lock_read(cat, name, self.who())
                    r['me'] = self.who()
                return self._json(r or {'error': '없는 레코드'}, 200 if r else 404)
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._json({'error': '레코드를 읽지 못했습니다: %s' % e}, 500)
        if u.path == '/api/locks':
            try:
                me = self.who()
                return self._json({'me': me, 'locks': locks_all(me), 'ver': version()})
            except Exception as e:
                return self._json({'error': str(e)}, 500)
        if u.path.startswith('/api/'):
            return self._json({'error': 'unknown'}, 404)
        return super().do_GET()

    def do_POST(self):
        from urllib.parse import urlparse
        u = urlparse(self.path)
        if u.path not in ('/api/save', '/api/block', '/api/lock',
                          '/api/unlock', '/api/beat', '/api/new', '/api/del',
                          '/api/review'):
            return self._json({'error': 'unknown'}, 404)
        n = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception as e:
            return self._json({'ok': False, 'errors': ['요청을 읽지 못했습니다: %s' % e]}, 400)

        if u.path == '/api/review':
            try:
                with SAVE_LOCK:
                    return self._json(set_review(body.get('cat'), body.get('name'),
                                                 self.who(), bool(body.get('clear'))))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._json({'ok': False, 'errors': ['처리 중 오류: %s' % e]}, 500)

        if u.path in ('/api/new', '/api/del'):
            fn = new_record if u.path == '/api/new' else del_record
            try:
                with SAVE_LOCK:
                    return self._json(fn(body.get('cat'), body.get('name'),
                                         self.who()))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._json({'ok': False, 'errors': ['처리 중 오류: %s' % e]}, 500)

        if u.path == '/api/block':
            try:
                with SAVE_LOCK:
                    return self._json(save_block(body['cat'], body['name'],
                                                 body, self.who()))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._json({'ok': False, 'errors': ['저장 중 오류: %s' % e]}, 500)

        if u.path != '/api/save':
            c, nm = body.get('cat'), body.get('name')
            try:
                me = self.who()
                if u.path == '/api/lock':
                    return self._json(lock_take(c, nm, me, bool(body.get('steal'))))
                if u.path == '/api/unlock':
                    return self._json(lock_free(c, nm, me, bool(body.get('force'))))
                return self._json(lock_beat(c, nm, me))
            except Exception as e:
                return self._json({'ok': False, 'errors': ['잠금 처리 중 오류: %s' % e]}, 500)
        try:
            with SAVE_LOCK:
                return self._json(save(body['cat'], body['name'], body, self.who()))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json({'ok': False, 'errors': ['저장 중 오류: %s' % e]}, 500)


# ─────────────────────── 살아 있는지 기록 ───────────────────────
# 2026-10-19 에 편집기가 멈춰 윈도우가 죽였는데(AppHang), 로그에 시각이 없어
# 언제 어디서 멈췄는지 알 수가 없었다. 다음에는 잡히도록 남긴다.
#   · 1분마다 「살아 있음」 과 K드라이브 응답 시간
#   · K드라이브가 10초 넘게 안 돌아오면 그 자리에서 모든 스레드 스택을 찍는다
#     (멈춘 곳이 K드라이브인지 아닌지가 이것으로 갈린다)

HB_SLOW = 10.0          # 이 이상 걸리면 느린 것으로 본다
HB_EVERY = 60


def heartbeat(root):
    n = 0
    while True:
        time.sleep(HB_EVERY)
        n += 1
        t0 = time.time()
        ok, err = True, ''
        try:
            os.listdir(root)
        except Exception as e:
            ok, err = False, '%s: %s' % (type(e).__name__, e)
        dt = time.time() - t0
        slow = dt >= HB_SLOW
        if slow or not ok or n % 10 == 0:
            print('  [상태] K드라이브 %.1f초%s · 스레드 %d개%s'
                  % (dt, '' if ok else ' · 읽기 실패 ' + err,
                     threading.active_count(), ' ** 느림' % () if slow else ''))
        if slow or not ok:
            # faulthandler 는 진짜 파일 디스크립터를 요구해서 로그(Tee)에 못 쓴다.
            # 스택을 글자로 만들어 평소 로그에 그대로 남긴다 — 한 군데서 보게.
            try:
                print('  [상태] 멈춘 자리 ─────────────')
                for tid, frame in sys._current_frames().items():
                    nm = next((t.name for t in threading.enumerate()
                               if t.ident == tid), '?')
                    print('   · 스레드 %s (%s)' % (tid, nm))
                    for ln in traceback.format_stack(frame)[-6:]:
                        for x in ln.rstrip().split('\n'):
                            print('     ' + x.strip()[:150])
                print('  [상태] ───────────────────────')
            except Exception as e:
                print('  [상태] 스택을 찍지 못했습니다: %s' % e)


def main():
    global ROOT
    # 한글 윈도우 콘솔은 기본이 cp949 라서 — 「」 ※ 같은 글자를 못 찍고 죽는다.
    # .bat 로 켜면 chcp 65001 이 깔리지만, 직접 띄우거나 서비스로 돌리면 그게 없다.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    # pythonw 로 띄우면 콘솔이 없어 메시지가 어디에도 남지 않는다.
    # 로그온 자동 시작처럼 창 없이 돌릴 때 무슨 일이 있었는지 볼 수 있게 한다.
    a0, _ = argparse.ArgumentParser(add_help=False).parse_known_args()
    if '--log' in sys.argv:
        path = sys.argv[sys.argv.index('--log') + 1]
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        fh = open(path, 'a', encoding='utf-8', errors='replace', buffering=1)
        fh.write('\n%s  ─── 켬 ───\n' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        class _Tee:
            """줄마다 시각을 붙인다.

            시각이 없으면 멈춘 시점을 영영 특정할 수 없다 —
            2026-10-19 에 멈췄을 때 로그가 폴링 기록만 남기고 끊겨서
            언제 죽었는지 알 수가 없었다.
            """
            def __init__(self, *s):
                self.s = [x for x in s if x]
                self.bol = True

            def write(self, t):
                if t:
                    now = datetime.datetime.now().strftime('%m-%d %H:%M:%S')
                    out = []
                    for i, part in enumerate(t.split('\n')):
                        if i:
                            out.append('\n')
                            self.bol = True
                        if part and self.bol:
                            out.append(now + ' ')
                            self.bol = False
                        out.append(part)
                    t = ''.join(out)
                for x in self.s:
                    try:
                        x.write(t)
                    except Exception:
                        pass
                return len(t)

            def flush(self):
                for x in self.s:
                    try:
                        x.flush()
                    except Exception:
                        pass

        sys.stdout = _Tee(sys.stdout, fh)
        sys.stderr = _Tee(sys.stderr, fh)
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8777)
    ap.add_argument('--host', default='127.0.0.1',
                    help='여러 명이 쓰는 서버면 0.0.0.0')
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--no-open', action='store_true')
    ap.add_argument('--log', default='',
                    help='콘솔 없이 돌릴 때 기록을 남길 파일')
    ap.add_argument('--no-auto-update', action='store_true',
                    help='git 으로 스스로 최신 코드를 받지 않는다')
    ap.add_argument('--update-every', type=int, default=600,
                    help='자동 갱신 확인 주기(초). 기본 10분')
    ap.add_argument('--git-timeout', type=int, default=300,
                    help='git 한 번에 기다릴 시간(초). 프록시가 느리면 늘린다')
    a = ap.parse_args()
    ROOT = a.root.replace('\\', '/').rstrip('/')
    global GIT_TIMEOUT
    GIT_TIMEOUT = a.git_timeout

    if not os.path.isdir(ROOT):
        print('지식시스템 폴더를 찾지 못했습니다: %s' % ROOT)
        print('K드라이브가 연결돼 있는지 확인하세요. 다른 경로면 --root 로 지정하세요.')
        sys.exit(1)

    os.makedirs(BACKUP, exist_ok=True)
    s = scan_cached(True)
    tot = sum(len([r for r in c['records'] if not r['tmpl']]) for c in s['cats'])
    shown = '127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host
    url = 'http://%s:%d/' % (shown, a.port)
    print('제안전략 지식시스템 편집기')
    print('  원본  %s' % ROOT.replace('/', '\\'))
    print('  백업  %s' % BACKUP)
    print('  레코드 %d건' % tot)
    print('  주소  %s' % url)
    if a.host not in ('127.0.0.1', 'localhost', '::1'):
        print()
        print('  ** %s 로 열려 있습니다 — 이 PC 밖에서도 들어옵니다.' % a.host)
        print('     로그인이 없습니다. 망에서 포트에 닿는 사람은 누구나 레코드를 고칠 수 있습니다.')
        print('     사내망 안에서만 쓰거나, 앞에 인증 프록시를 두세요.')
    print('  멈추려면 Ctrl+C')
    if not a.no_open and a.host in ('127.0.0.1', 'localhost'):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv = Server((a.host, a.port), H)
    except OSError:
        print('\n%d번 포트를 이미 쓰고 있습니다.' % a.port)
        print('편집기가 이미 켜져 있는지 보세요 — %s' % url)
        print('다른 포트로 켜려면: python server.py --port 8778')
        sys.exit(1)
    RESTART['srv'] = srv
    threading.Thread(target=heartbeat, args=(a.root,), daemon=True).start()
    if not a.no_auto_update:
        if os.path.isdir(os.path.join(HERE, '.git')):
            update_once()
            threading.Thread(target=updater, args=(a.update_every,), daemon=True).start()
            print('  자동 갱신 %d분마다 (끄려면 --no-auto-update)' % max(1, a.update_every // 60))
        else:
            print('  자동 갱신 없음 — git 으로 받은 폴더가 아닙니다')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n멈췄습니다.')
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
    if RESTART['want']:
        # 지킴이가 띄운 것이면 지킴이가 다시 띄운다. 알려 주고 빠지면 된다
        if os.environ.get('KS_SUPERVISED'):
            sys.exit(RESTART_CODE)
        time.sleep(1)
        # 윈도우의 os.execv 는 빈칸이 든 인자를 따옴표로 묶지 않는다.
        # 「제안서 작성 에이전트」 같은 경로가 토막 나 새로 뜬 쪽이 바로 죽는다.
        # 실제로 이것 때문에 밤사이 편집기가 꺼져 있었다. Popen 은 제대로 묶는다.
        flags = 0
        if os.name == 'nt':
            flags = (getattr(subprocess, 'DETACHED_PROCESS', 0)
                     | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        subprocess.Popen([sys.executable, os.path.abspath(__file__)] + sys.argv[1:],
                         cwd=HERE, close_fds=True, creationflags=flags)


if __name__ == '__main__':
    main()
