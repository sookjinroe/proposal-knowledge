# -*- coding: utf-8 -*-
"""편집기를 띄워 두고, 꺼지면 다시 띄운다.

서버 혼자 돌면 한 번 죽는 순간 아무도 못 들어온다. 쓰는 사람은 그걸 모르고,
「사이트가 안 열린다」는 말만 듣는다. 그래서 앞에 지킴이를 하나 둔다.

인자는 그대로 server.py 에 넘긴다.
    python watchdog.py --host 0.0.0.0 --root "K:\\Workgroups\\제안전략 지식시스템" --log ...\\server.log
"""
import datetime
import io
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, 'server.py')

RESTART_CODE = 7        # server.py 가 「새 코드로 다시 띄워 달라」고 할 때
PORT_BUSY = 1           # 이미 켜져 있을 때 — 다시 띄우면 안 된다

TOO_FAST = 20           # 이 안에 죽으면 「바로 죽었다」고 본다
GIVE_UP_AFTER = 5       # 바로 죽기를 이만큼 되풀이하면 멈춘다
BUSY_TRIES = 12         # 포트가 풀리기를 기다리는 횟수 (5초씩 — 약 1분)


def log_path():
    if '--log' in sys.argv:
        try:
            return sys.argv[sys.argv.index('--log') + 1]
        except IndexError:
            return ''
    return ''


def say(msg):
    line = '%s  [지킴이] %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        print(line)
    except Exception:
        pass
    p = log_path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(p)) or '.', exist_ok=True)
        with io.open(p, 'a', encoding='utf-8', errors='replace') as f:
            f.write(line + '\n')
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    env = dict(os.environ, KS_SUPERVISED='1', PYTHONIOENCODING='utf-8')
    quick = 0
    busy = 0
    served = False      # 한 번이라도 제대로 돈 적이 있는가
    say('편집기를 띄웁니다.')
    while True:
        began = time.time()
        try:
            p = subprocess.Popen([sys.executable, SERVER] + args, cwd=HERE, env=env)
        except Exception as e:
            say('띄우지 못했습니다: %s' % e)
            return 1
        try:
            code = p.wait()
        except KeyboardInterrupt:
            say('멈추라고 하셔서 같이 내려갑니다.')
            try:
                p.terminate()
            except Exception:
                pass
            return 0
        lived = time.time() - began
        if lived >= TOO_FAST:
            served = True

        if code == RESTART_CODE:
            say('새 코드를 받았습니다. 다시 띄웁니다.')
            time.sleep(1.5)          # 쓰던 포트가 풀릴 틈을 준다
            quick = busy = 0
            continue
        if code == PORT_BUSY:
            if not served:
                # 처음부터 포트가 막혀 있다 — 남이 이미 켜 둔 것이다
                say('이미 켜져 있는 것 같습니다. 지킴이는 빠집니다.')
                return 0
            # 방금까지 내가 쓰던 포트다. 강제로 끊긴 소켓이 풀리는 데
            # 시간이 걸릴 뿐이니, 남의 것으로 보고 물러나면 안 된다.
            busy += 1
            if busy > BUSY_TRIES:
                say('포트 %d초째 풀리지 않습니다. 더 띄우지 않습니다.' % (5 * BUSY_TRIES))
                return 1
            if busy == 1:
                say('쓰던 포트가 아직 풀리지 않았습니다. 풀릴 때까지 기다립니다.')
            time.sleep(5)
            continue
        busy = 0
        if code == 0:
            say('편집기가 정상으로 멈췄습니다. 지킴이도 멈춥니다.')
            return 0

        # 여기부터는 뜻밖의 종료 — 다시 띄운다
        quick = quick + 1 if lived < TOO_FAST else 0
        if quick >= GIVE_UP_AFTER:
            say('%d번 잇달아 바로 죽습니다 (끝 코드 %s). 더 띄우지 않습니다.'
                % (quick, code))
            say('_log\\server.log 의 마지막 부분을 보세요. 경로나 파이썬 문제일 수 있습니다.')
            return 1
        wait = min(60, 5 * max(1, quick))
        say('편집기가 %.0f초 만에 꺼졌습니다 (끝 코드 %s). %d초 뒤 다시 띄웁니다.'
            % (lived, code, wait))
        time.sleep(wait)


if __name__ == '__main__':
    sys.exit(main())
